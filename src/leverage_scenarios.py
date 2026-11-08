"""Independent $10 isolated-margin short sensitivities on the frozen spot signals.

The ledger is a research approximation: spot execution prices are retained,
historical USD-M funding is included where available, and USD-M mark 5m highs
are used for a conservative liquidation check. Historical maintenance brackets,
futures execution prices and exact intrabar order are unavailable here.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "futures"
OUT = ROOT / "output" / "leverage_scenarios"
BASE = "https://fapi.binance.com/fapi/v1"
MARGIN = 10.0
FEE = .001  # same research assumption as the spot-direction ledger
MMR = .005  # illustrative maintenance-margin ratio; historical tiers not verified
STEP = 300_000
TLS = threading.local()
RATE_LOCK = threading.Lock()
NEXT_REQUEST = 0.0


def api(path, params=None):
    global NEXT_REQUEST
    if not hasattr(TLS, "session"):
        TLS.session = requests.Session()
    for attempt in range(8):
        with RATE_LOCK:
            wait = max(0.0, NEXT_REQUEST-time.monotonic())
            NEXT_REQUEST = max(NEXT_REQUEST, time.monotonic()) + .16
        if wait:
            time.sleep(wait)
        try:
            r = TLS.session.get(BASE+path, params=params, timeout=45)
            if r.status_code in (418,429,500,502,503,504):
                time.sleep(min(60, 2**attempt+1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 7:
                raise
            time.sleep(min(30,2**attempt))
    raise RuntimeError(f"API failed: {path} {params}")


def select_trades():
    ledger = pd.read_csv(ROOT / "output/all_trades.csv", low_memory=False)
    closed = ledger[ledger.status.eq("closed")].copy()
    means = closed.groupby(["pattern_id","variant"]).net_pnl.mean()
    positive = set(means[means>0].index)
    chosen = closed[[tuple(x) in positive for x in closed[["pattern_id","variant"]].itertuples(index=False,name=None)]].copy()
    chosen["strategy"] = chosen.pattern_id.astype(str)+"/"+chosen.variant
    return chosen, sorted(f"{p}/{v}" for p,v in positive)


def exchange_info():
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "exchange_info.json"
    if not path.exists():
        path.write_text(json.dumps(api("/exchangeInfo"),ensure_ascii=False),encoding="utf-8")
    j = json.loads(path.read_text(encoding="utf-8"))
    return {x["symbol"]:x for x in j["symbols"] if x.get("contractType")=="PERPETUAL"
            and x.get("quoteAsset")=="USDT" and x.get("marginAsset")=="USDT"
            and x.get("status")=="TRADING"}


def eligible_trades():
    chosen, positive = select_trades()
    contracts = exchange_info()
    chosen["onboard_date"] = chosen.symbol.map(lambda s: contracts.get(s,{}).get("onboardDate", np.nan))
    chosen["futures_eligible"] = chosen.entry_time >= chosen.onboard_date
    chosen.loc[~chosen.symbol.isin(contracts), "futures_eligible"] = False
    return chosen, positive, contracts


def funding_symbol(symbol, start, end):
    path = DATA / "funding" / f"{symbol}.csv.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = pd.read_csv(path)
        except (pd.errors.EmptyDataError,EOFError,OSError):
            old = pd.DataFrame(columns=["fundingTime","fundingRate"])
    else:
        old = pd.DataFrame(columns=["fundingTime","fundingRate"])
    cursor = max(int(start), int(old.fundingTime.max())+1) if len(old) else int(start)
    parts = [old]
    while cursor <= end:
        raw = api("/fundingRate", {"symbol":symbol,"startTime":cursor,"endTime":int(end),"limit":1000})
        if not raw:
            break
        parts.append(pd.DataFrame([{"fundingTime":int(x["fundingTime"]),
                                    "fundingRate":float(x["fundingRate"])} for x in raw]))
        cursor = int(raw[-1]["fundingTime"])+1
        if len(raw)<1000:
            break
    df = pd.concat(parts,ignore_index=True).drop_duplicates("fundingTime").sort_values("fundingTime")
    if len(df):
        df.to_csv(path,index=False,compression="gzip")
    return symbol, len(df)


def prepare_funding(trades):
    jobs = []
    for symbol,g in trades.groupby("symbol"):
        jobs.append((symbol,int(g.entry_time.min())-8*3600000,int(g.exit_time.max())+8*3600000))
    failures = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(funding_symbol,*job):job[0] for job in jobs}
        for n,f in enumerate(as_completed(futures),1):
            try:
                symbol,count = f.result()
                print(f"funding {n}/{len(jobs)} {symbol}: {count}",flush=True)
            except Exception as e:
                failures[futures[f]] = str(e)
                print(f"funding {n}/{len(jobs)} {futures[f]}: ERROR {e}",flush=True)
    (DATA/"funding_failures.json").write_text(json.dumps(failures,indent=2),encoding="utf-8")
    return failures


def mark_window(symbol, start, end):
    folder = DATA / "mark_5m"
    folder.mkdir(parents=True,exist_ok=True)
    path = folder / f"{symbol}_{start}_{end}.csv.gz"
    if path.exists():
        try:
            df = pd.read_csv(path)
            if len(df): return df
        except (pd.errors.EmptyDataError,EOFError,OSError):
            pass
    cursor = int(start)
    parts = []
    while cursor < end:
        raw = api("/markPriceKlines",{"symbol":symbol,"interval":"5m",
                                      "startTime":cursor,"endTime":int(end)-1,"limit":1000})
        if not raw: break
        parts.extend((int(x[0]),float(x[2])) for x in raw)
        cursor = int(raw[-1][0])+STEP
        if len(raw)<1000: break
    df = pd.DataFrame(parts,columns=["open_time","mark_high"]).drop_duplicates("open_time")
    if len(df): df.to_csv(path,index=False,compression="gzip")
    return df


def needed_mark(trades):
    x = trades.copy()
    # Fetch mark data where a 1% MMR liquidation level is within 2% of stop.
    liq_10 = x.entry_price*(1+1/10-FEE)/(1+.01)
    liq_5 = x.entry_price*(1+1/5-FEE)/(1+.01)
    x = x[(liq_10 <= x.stop*1.02) | (liq_5 <= x.stop*1.02)]
    x["mark_end"] = np.where(x.exit_reason.eq("timeout"),x.exit_time,x.exit_time+STEP).astype(np.int64)
    return x[["symbol","entry_time","mark_end"]].drop_duplicates()


def prepare_mark(trades):
    windows = list(needed_mark(trades).itertuples(index=False,name=None))
    failures = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(mark_window,s,int(a),int(b)):(s,int(a),int(b)) for s,a,b in windows}
        for n,f in enumerate(as_completed(futures),1):
            key = futures[f]
            try:
                df = f.result()
                if len(df)==0: failures[str(key)]="empty mark window"
            except Exception as e:
                failures[str(key)] = str(e)
            if n%100==0 or n==len(windows):
                print(f"mark {n}/{len(windows)}, failures {len(failures)}",flush=True)
    (DATA/"mark_failures.json").write_text(json.dumps(failures,indent=2),encoding="utf-8")
    return failures


def load_funding(symbol):
    path = DATA/"funding"/f"{symbol}.csv.gz"
    return pd.read_csv(path) if path.exists() else None


def trade_scenarios(x, funding, mark, contract, leverages=(2,5,10), minute_exit=False):
    entry, exit_time = int(x.entry_time),int(x.exit_time)
    rates = funding[(funding.fundingTime >= entry) & (funding.fundingTime < exit_time)]
    event_t = rates.fundingTime.to_numpy(dtype=np.int64)
    event_r = rates.fundingRate.to_numpy(dtype=float)
    # A 1m TP/SL may occur inside the 5m mark bar that begins at the
    # original 5m exit. Include that bar once, never a later 5m bar.
    mark_end = int(exit_time if x.exit_reason=="timeout" else
                   ((exit_time//STEP+1)*STEP if minute_exit else exit_time+STEP))
    need = (x.entry_price*(1+1/10-FEE)/(1+.01) <= x.stop*1.02 or
            x.entry_price*(1+1/5-FEE)/(1+.01) <= x.stop*1.02)
    if need and mark is not None:
        expected = np.arange(entry,mark_end,STEP,dtype=np.int64)
        mt = mark.open_time.to_numpy(dtype=np.int64)
        if len(mt)!=len(expected) or not np.array_equal(mt,expected):
            mark = None
    results=[]
    for lev in leverages:
        qty=MARGIN*lev/float(x.entry_price)
        filters={z['filterType']:z for z in contract.get('filters',[])}
        lot=filters.get('MARKET_LOT_SIZE',filters.get('LOT_SIZE',{}))
        step=float(lot.get('stepSize',0) or 0)
        min_qty=float(lot.get('minQty',0) or 0)
        min_notional=float(filters.get('MIN_NOTIONAL',{}).get('notional',0) or 0)
        rounded_qty=math.floor(qty/step+1e-10)*step if step else qty
        meets_current_min=rounded_qty+1e-12>=min_qty and rounded_qty*float(x.entry_price)+1e-9>=min_notional
        entry_fee=FEE*MARGIN*lev
        liq_initial=float(x.entry_price)*(1+1/lev-FEE)/(1+MMR)
        candidate=liq_initial <= float(x.stop)*1.02
        status="resolved"
        if candidate and mark is None:
            status="mark_unavailable"
        funding_acc=0.0
        liquidation=False
        liq_time=np.nan
        liq_price=np.nan
        used_events=0
        if status=="resolved" and candidate:
            event_i=0
            for bar in mark.itertuples(index=False):
                t=int(bar.open_time)
                # Funding in a bar is credited after testing that bar's high.
                while event_i<len(event_t) and event_t[event_i]<t:
                    funding_acc += qty*float(x.entry_price)*event_r[event_i]
                    event_i+=1
                threshold=(MARGIN-entry_fee+qty*float(x.entry_price)+funding_acc)/(qty*(1+MMR))
                if float(bar.mark_high)>=threshold:
                    liquidation=True
                    liq_time=t
                    liq_price=threshold
                    used_events=event_i
                    break
            if not liquidation:
                funding_acc=qty*float(x.entry_price)*float(event_r.sum())
                used_events=len(event_t)
        elif status=="resolved":
            funding_acc=qty*float(x.entry_price)*float(event_r.sum())
            used_events=len(event_t)
        if status!="resolved":
            net=np.nan
            reason="unresolved_mark"
            used_events=0
        elif liquidation:
            liq_fee=float(contract.get("liquidationFee",0))*qty*liq_price
            net=max(-MARGIN,qty*(float(x.entry_price)-liq_price)-entry_fee+funding_acc-liq_fee)
            reason="approx_liquidation"
        else:
            net=qty*(float(x.entry_price)-float(x.exit_price))-FEE*qty*(float(x.entry_price)+float(x.exit_price))+funding_acc
            reason=x.exit_reason
            # A spot-price proxy can imply a loss beyond isolated collateral
            # even when 5m futures mark highs did not cross our threshold.
            # Such cases cannot be exact futures fills; cap collateral loss
            # and identify them separately from mark-triggered liquidations.
            if net < -MARGIN:
                net=-MARGIN
                reason="margin_exhausted_proxy"
        results.append({"symbol":x.symbol,"strategy":x.strategy,"pattern_id":int(x.pattern_id),
                        "variant":x.variant,"entry_time":entry,"base_exit_time":exit_time,
                        "base_exit_reason":x.exit_reason,"entry_price":x.entry_price,
                        "stop":x.stop,"target":x.target,"leverage":lev,"margin_usdt":MARGIN,
                        "notional_usdt":MARGIN*lev,"quantity":qty,"funding_usdt":funding_acc if status=="resolved" else np.nan,
                        "meets_current_min_order":meets_current_min,
                        "funding_events":used_events,"liquidation_level_initial":liq_initial,
                        "liquidation_time":liq_time,"liquidation_price":liq_price,
                        "exit_reason":reason,"status":status,"net_pnl_usdt":net,
                        "roi_on_margin_pct":100*net/MARGIN if status=="resolved" else np.nan,
                        "base_1x_return_pct":100*float(x.net_pnl),
                        "mark_checked":bool(candidate),"model_mmr":MMR})
    return results


def calculate(trades,contracts):
    OUT.mkdir(parents=True,exist_ok=True)
    rows=[]
    for n,(symbol,g) in enumerate(trades.groupby("symbol"),1):
        funding=load_funding(symbol)
        if funding is None:
            raise RuntimeError(f"Missing historical funding for {symbol}; run --prepare again")
        for x in g.itertuples(index=False):
            end=int(x.exit_time if x.exit_reason=="timeout" else x.exit_time+STEP)
            initial=x.entry_price*(1+1/10-FEE)/(1+.01)
            initial5=x.entry_price*(1+1/5-FEE)/(1+.01)
            need=initial<=x.stop*1.02 or initial5<=x.stop*1.02
            mark=mark_window(symbol,int(x.entry_time),end) if need else None
            rows.extend(trade_scenarios(x,funding,mark,contracts[symbol]))
        print(f"scenario {n}/{trades.symbol.nunique()} {symbol}: {len(g)} trades",flush=True)
    df=pd.DataFrame(rows)
    df.to_csv(OUT/"all_scenarios.csv",index=False)
    return df


def report(df,positive,chosen):
    from collections import Counter
    OUT.mkdir(parents=True,exist_ok=True)
    base=chosen[chosen.futures_eligible].copy()
    summary=[]
    for strategy in positive:
        b=base[base.strategy==strategy]
        for lev in (2,5,10):
            g=df[(df.strategy==strategy)&(df.leverage==lev)]
            r=g[g.status=="resolved"]
            summary.append({"strategy":strategy,"leverage":lev,
                            "spot_positive_trades":int((chosen.strategy==strategy).sum()),
                            "futures_eligible_trades":len(b),"resolved_trades":len(r),
                            "unresolved_mark":int((g.status!="resolved").sum()),
                            "coin_count":r.symbol.nunique(),
                            "current_min_order_pass_pct":100*g.meets_current_min_order.mean() if len(g) else np.nan,
                            "base_1x_mean_pct":100*b.net_pnl.mean() if len(b) else np.nan,
                            "mean_pnl_usdt":r.net_pnl_usdt.mean() if len(r) else np.nan,
                            "total_pnl_usdt":r.net_pnl_usdt.sum() if len(r) else np.nan,
                            "mean_roi_margin_pct":r.roi_on_margin_pct.mean() if len(r) else np.nan,
                            "win_rate_pct":100*(r.net_pnl_usdt>0).mean() if len(r) else np.nan,
                            "approx_liquidations":int(r.exit_reason.isin(["approx_liquidation","margin_exhausted_proxy"]).sum()),
                            "funding_total_usdt":r.funding_usdt.sum() if len(r) else np.nan})
    s=pd.DataFrame(summary)
    s.to_csv(OUT/"summary.csv",index=False)
    head="""<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>2x 5x 10x short senaryoları</title><style>body{font:14px system-ui;background:#101522;color:#e8eef8;margin:0}main{max-width:1500px;margin:auto;padding:26px}h1{font-size:29px}.note{background:#1b2538;border:1px solid #36475f;border-radius:10px;padding:15px;line-height:1.55}table{border-collapse:collapse;width:100%;font-size:12px}th,td{padding:8px;border-bottom:1px solid #2d3b50;text-align:left;white-space:nowrap}th{position:sticky;top:0;background:#1b2538}.box{overflow:auto;max-height:65vh;border:1px solid #36475f;border-radius:10px;margin:15px 0}input,select{background:#1b2538;color:white;border:1px solid #405472;border-radius:7px;padding:9px;margin:4px}a{color:#a6caff}.bad{color:#ff9b9b}.good{color:#72ddb1}</style><main><h1>Short · 10 USDT teminat · 2x / 5x / 10x</h1><div class="note">Düzeltilmiş 1x sonuçta işlem başına ortalaması pozitif olan 9 varyant seçildi. Her işlem bağımsız 10 USDT izole teminat: 2x=20, 5x=50, 10x=100 USDT nominal. Sadece bugün işlem gören USDT sürekli vadeli sözleşmesinin listelenme tarihinden sonraki sinyaller değerlendirildi. Gerçek futures işlem fiyatı yerine spot 5m fiyatı kullanıldı; tarihsel funding dâhil, tasfiye kontrolü Binance 5m mark fiyatı ve varsayımsal %0,5 bakım teminatı ile yaklaşık. Tarihsel bracket, kesin tasfiye ücreti ve mum içi sıra bilinmiyor. Güncel minimum emir kurallarına uygunluk ayrı bir tanı alanıdır; geçmiş emir kuralları değildir. Aynı coindeki eşzamanlı işlemler/kasa kısıtı modellenmedi. Toplam PnL gerçek uygulanabilir portföy getirisi değildir. <a href="summary.csv">Özet CSV</a> · <a href="all_scenarios.csv">Tüm senaryolar CSV</a>.</div><h2>Varyant özeti</h2><div class="box"><table><tr><th>Varyant</th><th>Kaldıraç</th><th>Uygun işlem</th><th>Çözülen</th><th>Mark eksik</th><th>Güncel min emir uygun %</th><th>1x baz ort. %</th><th>Ortalama net $</th><th>Toplam net $</th><th>10 $ teminat ROI %</th><th>Kârlı %</th><th>Yaklaşık tasfiye</th><th>Funding toplam $</th></tr>"""
    for x in s.itertuples(index=False):
        vals=[x.strategy,x.leverage,x.futures_eligible_trades,x.resolved_trades,x.unresolved_mark,f"{x.current_min_order_pass_pct:.1f}",
              f"{x.base_1x_mean_pct:.3f}",f"{x.mean_pnl_usdt:.4f}",f"{x.total_pnl_usdt:.2f}",
              f"{x.mean_roi_margin_pct:.2f}",f"{x.win_rate_pct:.1f}",x.approx_liquidations,f"{x.funding_total_usdt:.2f}"]
        head+="<tr>"+"".join(f"<td>{escape(str(v))}</td>" for v in vals)+"</tr>"
    head+="</table></div><h2>İşlem ayrıntıları</h2><input id='q' placeholder='Coin veya varyant ara'><select id='l'><option value=''>Tüm kaldıraçlar</option><option>2</option><option>5</option><option>10</option></select><div class='box'><table id='trades'><thead><tr>"
    cols=["symbol","strategy","leverage","entry_time","base_exit_time","entry_price","stop","target","notional_usdt","meets_current_min_order","funding_usdt","liquidation_level_initial","liquidation_time","exit_reason","status","net_pnl_usdt","roi_on_margin_pct","base_1x_return_pct"]
    labels=["Coin","Varyant","x","Giriş UTC","Baz çıkış UTC","Giriş","Stop","Hedef","Nominal $","Güncel min emir uygun","Funding $","İlk tahmini tasfiye fiyatı","Tasfiye UTC","Çıkış","Durum","Net $","Teminat ROI %","1x baz %"]
    head+="".join(f"<th>{z}</th>" for z in labels)+"</tr></thead><tbody>"
    for _,x in df.iterrows():
        cells=[]
        for c in cols:
            v=x[c]
            if pd.isna(v): v=""
            elif c.endswith("_time"):
                v=pd.to_datetime(v,unit="ms",utc=True).strftime("%Y-%m-%d %H:%M")
            elif isinstance(v,(float,np.floating)): v=f"{v:.5f}"
            cells.append(f"<td>{escape(str(v))}</td>")
        head+="<tr>"+"".join(cells)+"</tr>"
    head+="</tbody></table></div><script>const q=document.querySelector('#q'),l=document.querySelector('#l'),rows=[...document.querySelectorAll('#trades tbody tr')];function f(){let z=q.value.toLowerCase(),v=l.value;for(let r of rows)r.hidden=!(r.textContent.toLowerCase().includes(z)&&(!v||r.cells[2].textContent===v))}q.oninput=f;l.onchange=f</script></main></html>"
    (OUT/"dashboard.html").write_text(head,encoding="utf-8")
    return s


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--prepare",action="store_true")
    parser.add_argument("--run",action="store_true")
    args=parser.parse_args()
    chosen,positive,contracts=eligible_trades()
    eligible=chosen[chosen.futures_eligible].copy()
    print(f"{len(positive)} positive variants; {len(chosen)} spot trades; {len(eligible)} futures-eligible trades; {eligible.symbol.nunique()} symbols",flush=True)
    if args.prepare:
        prepare_funding(eligible)
        prepare_mark(eligible)
    if args.run:
        out=calculate(eligible,contracts)
        report(out,positive,chosen)

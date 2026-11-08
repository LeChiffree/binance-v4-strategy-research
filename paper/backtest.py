"""Reproducible Binance spot-OHLCV short-direction research backtest."""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"
API = "https://api.binance.com/api/v3"
HTTP = requests.Session()
CUTOFF = int(datetime(2023, 10, 9, tzinfo=timezone.utc).timestamp() * 1000)
NOW = int(time.time() * 1000)
FEE = .001
SLIP = .0002
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
VARIANTS = {
    "65": [("close_2r", 50), ("retest_2r", 50), ("close_2r_h100", 100)],
    "9": [("close_2r", 50), ("close_2r_h25", 25), ("close_2r_h100", 100)],
    "16": [("close_2r", 50), ("close_2r_h25", 25)],
    "21": [("close_2r", 50), ("retest_2r", 50), ("close_2r_trend", 50), ("close_2r_volume", 50)],
}


def request(path, params=None, attempts=7):
    for n in range(attempts):
        try:
            r = HTTP.get(API + path, params=params, timeout=40)
            if r.status_code in (418, 429, 500, 502, 503, 504):
                time.sleep(min(60, 2 ** n + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if n == attempts - 1:
                raise
            time.sleep(min(30, 2 ** n))
    raise RuntimeError(f"Request failed: {path} {params}")


def inventory():
    DATA.mkdir(exist_ok=True)
    info = request("/exchangeInfo")
    symbols = [x["symbol"] for x in info["symbols"]
               if x["status"] == "TRADING" and x["quoteAsset"] == "USDT"
               and x.get("isSpotTradingAllowed", False) and x["baseAsset"] not in {"EUR"}]
    def first(symbol):
        x = request("/klines", {"symbol": symbol, "interval": "1d", "startTime": 0, "limit": 1})
        return {"symbol": symbol, "first_daily_ms": x[0][0] if x else None,
                "eligible": bool(x and x[0][0] <= CUTOFF)}
    rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(first, s): s for s in symbols}
        for future in as_completed(jobs):
            try:
                rows.append(future.result())
            except Exception as e:
                rows.append({"symbol": jobs[future], "first_daily_ms": None,
                             "eligible": False, "error": str(e)})
    rows.sort(key=lambda r: r["symbol"])
    (DATA / "inventory.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventory: {len(rows)} USDT spot pairs, {sum(r['eligible'] for r in rows)} eligible")
    return rows


def get_bars(symbol, interval, start, end):
    folder = DATA / "bars" / interval
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{symbol}_{start}_{end}.csv.gz" if interval == "5m" else f"{symbol}.csv.gz")
    if path.exists():
        try:
            old = pd.read_csv(path)
        except (pd.errors.EmptyDataError, EOFError, OSError):
            old = pd.DataFrame(columns=COLS)
    else:
        old = pd.DataFrame(columns=COLS)
    cursor = max(start, int(old.open_time.max()) + 1) if len(old) else start
    duration = {"5m": 300000, "1h": 3600000, "4h": 14400000}[interval]
    parts = [old]
    pages = 0
    while cursor + duration <= end:
        raw = request("/klines", {"symbol": symbol, "interval": interval,
                                  "startTime": cursor, "endTime": end - 1, "limit": 1000})
        if not raw:
            break
        batch = pd.DataFrame([[r[0], r[1], r[2], r[3], r[4], r[5], r[6]] for r in raw], columns=COLS)
        parts.append(batch)
        cursor = int(raw[-1][0]) + duration
        pages += 1
        if pages % 25 == 0:
            pd.concat(parts, ignore_index=True).drop_duplicates("open_time").to_csv(path, index=False, compression="gzip")
        if len(raw) < 1000:
            break
        time.sleep(.11)
    df = pd.concat(parts, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    for c in COLS:
        df[c] = pd.to_numeric(df[c])
    df = df[df.close_time < end].reset_index(drop=True)
    df.to_csv(path, index=False, compression="gzip")
    return df


def segments(df, duration):
    return np.cumsum(np.r_[0, np.diff(df.open_time.to_numpy()) != duration])


def features(df, duration):
    df = df.copy()
    df["segment"] = segments(df, duration)
    prev = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    tr[df.segment.ne(df.segment.shift(1))] = (df.high - df.low)[df.segment.ne(df.segment.shift(1))]
    df["atr"] = tr.groupby(df.segment).transform(lambda s: s.rolling(14, min_periods=14).mean())
    return df


def pivots(df, width):
    hi, lo = df.high.to_numpy(), df.low.to_numpy()
    seg = df.segment.to_numpy()
    result = []
    for i in range(width, len(df) - width):
        if seg[i-width] != seg[i+width]:
            continue
        h = hi[i-width:i+width+1]
        l = lo[i-width:i+width+1]
        if hi[i] == h.max() and np.count_nonzero(h == hi[i]) == 1:
            result.append((i, i+width, "H", hi[i]))
        if lo[i] == l.min() and np.count_nonzero(l == lo[i]) == 1:
            result.append((i, i+width, "L", lo[i]))
    return sorted(result, key=lambda p: (p[1], p[0]))


def line(points):
    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[3] for p in points], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    return slope, intercept, max(abs(y - (slope*x+intercept)))


def structures(df, tf):
    t = df.open_time.to_numpy()
    atr = df.atr.to_numpy()
    seg = df.segment.to_numpy()
    found = {}
    for width in (3, 6, 12):
        pts = pivots(df, width)
        for z in range(len(pts)):
            tail = pts[max(0, z-5):z+1]
            if len(tail) < 6 or any(tail[k][2] == tail[k+1][2] for k in range(5)):
                continue
            known = max(p[1] for p in tail)
            if known >= len(df) or not np.isfinite(atr[known]) or seg[tail[0][0]] != seg[known]:
                continue
            a = atr[known]
            highs = [p for p in tail if p[2] == "H"]
            lows = [p for p in tail if p[2] == "L"]
            hs, hi, he = line(highs)
            ls, li, le = line(lows)
            x0, x1 = tail[0][0], tail[-1][0]
            span = x1-x0
            top0, top1 = hs*x0+hi, hs*x1+hi
            bot0, bot1 = ls*x0+li, ls*x1+li
            w0, w1 = top0-bot0, top1-bot1
            if max(he, le) > .5*a or min(w0, w1) < a:
                continue
            kind = None
            if tf == "4h" and hs*span < -.5*a and ls*span > .5*a and w0-w1 > .5*a:
                kind = "9"
            elif tf == "1h" and abs(hs*span) <= .5*a and abs(ls*span) <= .5*a:
                kind = "16"
            elif tf == "1h" and hs*span > .5*a and ls*span < -.5*a and w1-w0 > .5*a:
                kind = "21"
            if kind:
                trigger = ls*known+li
                key = (kind, known, round(trigger, 10))
                found[key] = {"pattern_id": kind, "known_idx": known,
                              "trigger": trigger, "raw_stop": highs[-1][3],
                              "pivot_times": [int(t[p[0]]) for p in tail],
                              "pivot_prices": [float(p[3]) for p in tail], "pivot_width": width}
        if tf != "4h":
            continue
        for z in range(3, len(pts)):
            tail = pts[z-3:z+1]
            if [p[2] for p in tail] != ["L", "H", "L", "H"]:
                continue
            known = tail[-1][1]
            if known >= len(df) or not np.isfinite(atr[known]) or seg[tail[0][0]] != seg[known]:
                continue
            pa,pb,pc,pd = [p[3] for p in tail]
            ab,bc,cd = pb-pa,pb-pc,pd-pc
            if min(ab,bc,cd) <= 0 or pc <= pa:
                continue
            if not (.382*.95 <= bc/ab <= .886*1.05 and 1.13*.95 <= cd/bc <= 2.618*1.05):
                continue
            if not any(abs(cd/ab-r) <= .05*r and abs(cd-r*ab) <= .5*atr[known] for r in (1.27, 1.618)):
                continue
            key = ("65", known, round(float(df.low.iloc[known]), 10))
            found[key] = {"pattern_id": "65", "known_idx": known,
                          "trigger": float(df.low.iloc[known]), "raw_stop": pd,
                          "pivot_times": [int(t[p[0]]) for p in tail],
                          "pivot_prices": [float(p[3]) for p in tail], "pivot_width": width}
    return list(found.values())


def signals(df, symbol, tf):
    out = []
    duration = 14400000 if tf == "4h" else 3600000
    df = features(df, duration)
    arr = {k: df[k].to_numpy() for k in ["open_time", "open", "high", "low", "close", "volume", "atr", "segment"]}
    for st in structures(df, tf):
        k, level, high = st["known_idx"], st["trigger"], st["raw_stop"]
        confirm = None
        for q in range(k+1, min(len(df), k+1+(10 if st["pattern_id"] == "65" else 20))):
            if arr["segment"][q] != arr["segment"][k]:
                break
            if arr["high"][q] > high + .25*arr["atr"][k]:
                break
            if arr["close"][q] < level and np.isfinite(arr["atr"][q]):
                confirm = q
                break
        if confirm is None:
            continue
        q = confirm
        for variant, horizon in VARIANTS[st["pattern_id"]]:
            if variant.endswith("trend") and (q < 11 or arr["segment"][q-11] != arr["segment"][q]
                   or not arr["close"][q-1]-arr["close"][q-11] < -arr["atr"][q]):
                continue
            if variant.endswith("volume") and (q < 20 or arr["segment"][q-20] != arr["segment"][q]
                   or not arr["volume"][q] >= 1.5*np.mean(arr["volume"][q-20:q])):
                continue
            j = q
            if variant.startswith("retest"):
                j = None
                for idx in range(q+1, min(len(df), q+11)):
                    if arr["segment"][idx] != arr["segment"][q] or arr["close"][idx] > high:
                        break
                    if (arr["high"][idx] >= level-.25*arr["atr"][q]
                            and arr["close"][idx] < level and arr["close"][idx] < arr["open"][idx]):
                        j = idx
                        break
                if j is None:
                    continue
            if j+1 >= len(df) or arr["segment"][j+1] != arr["segment"][j]:
                continue
            entry = j+1
            if int(arr["open_time"][entry]+horizon*duration) > NOW:
                continue
            out.append({"symbol": symbol, "pattern_id": st["pattern_id"], "timeframe": tf,
                        "variant": variant, "side": -1, "pivot_width": st["pivot_width"],
                        "pivot_times": json.dumps(st["pivot_times"]),
                        "pivot_prices": json.dumps(st["pivot_prices"]),
                        "known_time": int(arr["open_time"][k]+duration), "trigger": level,
                        "raw_stop": high, "confirm_time": int(arr["open_time"][q]+duration),
                        "atr_at_confirm": float(arr["atr"][q]),
                        "retest_time": int(arr["open_time"][j]+duration) if j != q else None,
                        "entry_time": int(arr["open_time"][entry]), "horizon_bars": horizon,
                        "expiry_time": int(arr["open_time"][entry]+horizon*duration),
                        "signal_open": float(arr["open"][entry])})
    return out


def resolve(signal, bars):
    row = dict(signal)
    entry_t = row["entry_time"]
    expiry = row["expiry_time"]
    bars = bars[(bars.open_time >= entry_t) & (bars.open_time < expiry)].copy()
    if bars.empty or int(bars.open_time.iloc[0]) != entry_t:
        return dict(row, status="rejected", reject_reason="missing_entry_5m")
    # A short sell fills below the reference open when slippage is adverse.
    e = float(bars.open.iloc[0])*(1-SLIP)
    stop = row["raw_stop"]+.25*row["atr_at_confirm"]
    risk = stop-e
    target = e-2*risk
    if risk <= 0 or target <= 0:
        return dict(row, status="rejected", reject_reason="invalid_risk_or_target")
    target_fill = target*(1+SLIP)
    stop_fill = stop*(1+SLIP)
    net_win = (e-target_fill)/e-FEE*(1+target_fill/e)
    net_loss = (e-stop_fill)/e-FEE*(1+stop_fill/e)
    if net_win/abs(net_loss) < 1.5:
        return dict(row, status="rejected", reject_reason="net_reward_risk_below_1_5")
    reason, exit_price, exit_time, ambiguous = "timeout", float(bars.close.iloc[-1])*(1+SLIP), expiry, False
    next_open = entry_t
    for x in bars.itertuples(index=False):
        if int(x.open_time) != next_open:
            return dict(row, status="rejected", reject_reason="5m_gap_before_exit")
        next_open += 300000
        if x.open >= stop:
            reason, exit_price, exit_time = "stop_gap", x.open*(1+SLIP), int(x.open_time)
            break
        hit_stop, hit_target = x.high >= stop, x.low <= target
        if hit_stop:
            reason, exit_price, exit_time = "stop", stop*(1+SLIP), int(x.open_time)
            ambiguous = bool(hit_target)
            break
        if hit_target:
            reason, exit_price, exit_time = "target", target*(1+SLIP), int(x.open_time)
            break
    if reason == "timeout" and next_open != expiry:
        return dict(row, status="rejected", reject_reason="5m_gap_or_incomplete_horizon")
    pnl = (e-exit_price)/e-FEE*(1+exit_price/e)
    return dict(row, status="closed", reject_reason="", entry_price=e, stop=stop,
                target=target, exit_time=exit_time, exit_price=exit_price,
                exit_reason=reason, ambiguous=ambiguous, fees=FEE*(1+exit_price/e),
                funding=0, net_pnl=pnl, net_pnl_usd_per_10_notional=10*pnl)


def process_coin(item):
    symbol = item["symbol"]
    try:
        signals_for_coin = []
        first = item["first_daily_ms"]
        for tf in ("1h", "4h"):
            df = get_bars(symbol, tf, first, NOW)
            signals_for_coin.extend(signals(df, symbol, tf))
        trades = []
        for s in signals_for_coin:
            five = get_bars(symbol, "5m", s["entry_time"], s["expiry_time"])
            trades.append(resolve(s, five))
        pd.DataFrame(trades).to_csv(OUT / f"trades_{symbol}.csv", index=False)
        return symbol, {"status": "complete", "signals": len(trades),
                        "first_daily_ms": first}
    except Exception as e:
        return symbol, {"status": "error", "error": str(e), "first_daily_ms": item["first_daily_ms"]}


def run(limit=None):
    OUT.mkdir(exist_ok=True)
    inventory_path = DATA / "inventory.json"
    rows = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.exists() else inventory()
    eligible = [r for r in rows if r["eligible"]]
    if limit:
        eligible = eligible[:limit]
    progress_path = OUT / "progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {}
    pending = [r for r in eligible if progress.get(r["symbol"], {}).get("status") != "complete"]
    with ProcessPoolExecutor(max_workers=8) as pool:
        jobs = {pool.submit(process_coin, item): item["symbol"] for item in pending}
        for n, future in enumerate(as_completed(jobs), 1):
            symbol, state = future.result()
            progress[symbol] = state
            print(f"[{n}/{len(pending)}] {symbol}: {state['status']}, {state.get('signals','')} signals", flush=True)
            progress_path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
            if n % 5 == 0 or n == len(pending):
                dashboard(progress)
    dashboard(progress)


def dashboard(progress):
    from html import escape
    inventory_path = DATA / "inventory.json"
    if inventory_path.exists():
        inventory_rows = json.loads(inventory_path.read_text(encoding="utf-8"))
        pd.DataFrame(inventory_rows).to_csv(OUT / "coin_inventory.csv", index=False)
        allowed = {r["symbol"] for r in inventory_rows if r["eligible"]}
        progress = {symbol: state for symbol, state in progress.items() if symbol in allowed}
    frames = []
    for symbol, state in progress.items():
        p = OUT / f"trades_{symbol}.csv"
        if state["status"] == "complete" and p.exists() and p.stat().st_size:
            try:
                frames.append(pd.read_csv(p))
            except pd.errors.EmptyDataError:
                pass
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(df):
        df["alt_net_pnl"] = df.get("net_pnl", pd.Series(index=df.index, dtype=float))
        df["leverage"] = 1
        df["margin_usdt"] = np.where(df.status.eq("closed"), 10.0, np.nan)
        df["quantity_per_10_usdt"] = np.where(df.status.eq("closed"), 10.0/pd.to_numeric(df.entry_price, errors="coerce"), np.nan)
        ambiguous_mask = df.get("ambiguous", pd.Series(False, index=df.index)).astype(str).str.lower().eq("true")
        for idx in df.index[ambiguous_mask & df.status.eq("closed")]:
            entry, fill = float(df.at[idx, "entry_price"]), float(df.at[idx, "target"])*(1+SLIP)
            df.at[idx, "alt_net_pnl"] = (entry-fill)/entry-FEE*(1+fill/entry)
    df.to_csv(OUT / "all_trades.csv", index=False)
    closed = df[df.status == "closed"] if "status" in df else pd.DataFrame()
    n = len(closed)
    win = float((closed.net_pnl > 0).mean()*100) if n else 0
    mean = float(closed.net_pnl.mean()*100) if n else 0
    cumulative = float(closed.net_pnl_usd_per_10_notional.sum()) if n else 0
    ambiguous_count = int(closed.ambiguous.astype(str).str.lower().eq("true").sum()) if n else 0
    counts = {k: sum(v["status"] == k for v in progress.values()) for k in ("complete", "error")}
    body = """<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>12 formasyon Binance backtest</title><style>
body{font:14px system-ui;background:#101522;color:#e7edf7;margin:0}main{max-width:1500px;margin:auto;padding:28px}h1{font-size:30px;margin-bottom:5px}.muted{color:#9caec8}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:22px 0}.card{background:#1a2335;border:1px solid #30405a;border-radius:12px;padding:16px}.card b{font-size:25px;display:block;margin-top:7px}input,select{background:#1a2335;color:#fff;border:1px solid #40516e;border-radius:7px;padding:9px;margin:4px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid #2a3650;padding:8px;white-space:nowrap;text-align:left}th{position:sticky;top:0;background:#1a2335;cursor:pointer}.wrap{overflow:auto;max-height:65vh;border:1px solid #30405a;border-radius:10px}a{color:#9ac6ff}.good{color:#65d7a0}.bad{color:#ff858a}details{margin-top:22px}code{color:#9ac6ff}</style><main><h1>Binance · 12 formasyon backtest</h1><p class="muted">USDT spot geçmiş OHLCV · short yön simülasyonu · UTC zamanları · araştırma maliyeti %0,10 komisyon/yön + %0,02 kayma/yön</p><div class="cards">
"""
    for label, value in [("Tamamlanan coin", counts["complete"]), ("Hatalı coin", counts["error"]),
                         ("Kapanan işlemler", n), ("Pozitif sonuç", f"{win:.1f}%"),
                         ("Ortalama net getiri", f"{mean:.3f}%"), ("10 USDT nominal/işlem toplam", f"{cumulative:.2f} USDT"),
                         ("Aynı 5m mumunda çift temas", ambiguous_count)]:
        body += f'<div class="card">{label}<b>{value}</b></div>'
    body += "</div><h2>Strateji özeti</h2><div class='wrap'><table><tr><th>Formasyon</th><th>TF</th><th>Varyant</th><th>İşlem</th><th>Kazanma %</th><th>Ortalama net %</th><th>Medyan net %</th><th>Toplam 10 USDT nominal PnL</th><th>Stop</th><th>Hedef</th><th>Zaman aşımı</th></tr>"
    if n:
        grouped = closed.groupby(["pattern_id", "timeframe", "variant"], dropna=False)
        for (pattern, tf, variant), group in grouped:
            values = [pattern, tf, variant, len(group), f"{(group.net_pnl>0).mean()*100:.1f}",
                      f"{group.net_pnl.mean()*100:.3f}", f"{group.net_pnl.median()*100:.3f}",
                      f"{group.net_pnl_usd_per_10_notional.sum():.2f}",
                      int(group.exit_reason.isin(["stop", "stop_gap"]).sum()),
                      int((group.exit_reason=="target").sum()), int((group.exit_reason=="timeout").sum())]
            body += "<tr>" + "".join(f"<td>{escape(str(x))}</td>" for x in values) + "</tr>"
    body += "</table></div><h2>Coin özeti</h2><div class='wrap' style='max-height:280px'><table><tr><th>Coin</th><th>İşlem</th><th>Kazanma %</th><th>Ortalama net %</th><th>Toplam 10 USDT nominal PnL</th></tr>"
    if n:
        for symbol, group in closed.groupby("symbol"):
            values = [symbol, len(group), f"{(group.net_pnl>0).mean()*100:.1f}",
                      f"{group.net_pnl.mean()*100:.3f}", f"{group.net_pnl_usd_per_10_notional.sum():.2f}"]
            body += "<tr>" + "".join(f"<td>{escape(str(x))}</td>" for x in values) + "</tr>"
    body += "</table></div><h2>İşlemler</h2><p class='muted'>Sütun başlığına tıklayarak sırala. CSV: <a href='all_trades.csv'>tüm kayıtlar</a>.</p><input id='search' placeholder='Ara: BTCUSDT, retest, stop...'><select id='pattern'><option value=''>Tüm formasyonlar</option><option value='65'>AB=CD (65)</option><option value='9'>Üçgen (9)</option><option value='16'>Dikdörtgen (16)</option><option value='21'>Megafon (21)</option></select><select id='status'><option value=''>Tüm durumlar</option><option>closed</option><option>rejected</option></select><div class='wrap'><table id='trades'><thead><tr>"
    columns = ["symbol", "pattern_id", "timeframe", "variant", "known_time", "confirm_time", "retest_time", "entry_time", "entry_price", "stop", "target", "expiry_time", "exit_time", "exit_price", "exit_reason", "status", "reject_reason", "net_pnl", "alt_net_pnl", "ambiguous", "leverage", "margin_usdt", "quantity_per_10_usdt", "pivot_times", "pivot_prices", "trigger", "raw_stop", "atr_at_confirm", "pivot_width"]
    labels = ["Coin", "Formasyon", "TF", "Varyant", "Bilindi", "Teyit", "Retest", "Giriş", "Giriş fiyatı", "Stop", "Hedef", "Vade", "Çıkış", "Çıkış fiyatı", "Çıkış nedeni", "Durum", "Eleme nedeni", "Net %", "Hedef önce net %", "Çift temas", "Kaldıraç", "Teminat USDT", "10 USDT için miktar", "Pivot zamanları", "Pivot fiyatları", "Tetik", "Ham stop", "ATR", "Pivot genişliği"]
    for label in labels:
        body += f"<th>{label}</th>"
    body += "</tr></thead><tbody>"
    def fmt(c, v):
        if pd.isna(v): return ""
        if c.endswith("_time") and c != "pivot_times":
            return datetime.fromtimestamp(float(v)/1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
        if c in ("net_pnl", "alt_net_pnl"): return f"{float(v)*100:.3f}"
        if c in ("entry_price", "stop", "target", "exit_price", "trigger", "raw_stop", "atr_at_confirm", "quantity_per_10_usdt"):
            return f"{float(v):.8g}"
        return str(v)
    for _, row in df.iterrows():
        body += "<tr>" + "".join(f"<td>{escape(fmt(c, row.get(c)))}</td>" for c in columns) + "</tr>"
    body += "</tbody></table></div><details><summary>Kapsam ve yöntem</summary><p>Yaş eşiği ilk Binance günlük USDT spot mumuyla ölçülür: 9 Ekim 2023 veya öncesi. Sadece kapanmış mumlar kullanılır. Stratejiler 1h/4h, gerçekleşme 5m. Bilindi/teyit/retest zamanları mum kapanışıdır; stop/hedef çıkış zamanı gerçekleştiği 5m mumunun başlangıcıdır, tam işlem saniyesi bilinemez. Aynı 5m mumunda stop ve hedef görünürse ana sonuçta stop önce sayılır. Net sonuç 1x nominal getiridir. Funding, mark fiyatı, likidasyon, ortak kasa ve likidite sınırı modellenmemiştir. Kaynak koda erişilemediği için bu uygulama ekli rehberden yeniden kurulmuştur. <a href='coin_inventory.csv'>Tam coin ve yaş envanteri CSV</a>.</p><h3>Coin kapsamı</h3><div class='wrap'><table><tr><th>Coin</th><th>Durum</th><th>Sinyal</th><th>Hata</th></tr>"
    for symbol, state in sorted(progress.items()):
        body += f"<tr><td>{escape(symbol)}</td><td>{escape(state['status'])}</td><td>{state.get('signals','')}</td><td>{escape(state.get('error',''))}</td></tr>"
    body += "</table></div>"
    coverage_path = OUT / "coverage.csv"
    if coverage_path.exists():
        coverage = pd.read_csv(coverage_path)
        coverage = coverage[coverage.symbol.isin(progress)]
        body += "<h3>Mum kapsamı</h3><p><a href='coverage.csv'>Kapsam CSV</a> · İlk ve son mevcut mum; boşluk sayısı ardışık olmayan mum aralıklarıdır.</p><div class='wrap' style='max-height:400px'><table><tr><th>Coin</th><th>TF</th><th>İlk mum UTC</th><th>Son mum UTC</th><th>Mum sayısı</th><th>Boşluk</th><th>Durum</th></tr>"
        for _, c in coverage.iterrows():
            values = [c.get(k, "") for k in ("symbol", "timeframe", "first_open_utc", "last_open_utc", "bars", "gap_count", "status")]
            body += "<tr>"+"".join(f"<td>{escape(str(v)) if not pd.isna(v) else ''}</td>" for v in values)+"</tr>"
        body += "</table></div>"
    body += "</details><script>const q=document.querySelector('#search'),s=document.querySelector('#status'),p=document.querySelector('#pattern'),rows=[...document.querySelectorAll('#trades tbody tr')];function filter(){let z=q.value.toLowerCase(),v=s.value,k=p.value;for(let r of rows)r.hidden=!(r.textContent.toLowerCase().includes(z)&&(!v||r.cells[15].textContent===v)&&(!k||r.cells[1].textContent===k))}q.oninput=filter;s.onchange=filter;p.onchange=filter;document.querySelectorAll('#trades th').forEach((h,i)=>h.onclick=()=>{let body=h.closest('table').tBodies[0],asc=h.dataset.asc!=='1';rows.sort((a,b)=>{let x=a.cells[i].textContent,y=b.cells[i].textContent,an=Number(x),bn=Number(y);return(asc?1:-1)*(x&&y&&!isNaN(an)&&!isNaN(bn)?an-bn:x.localeCompare(y))});rows.forEach(r=>body.append(r));h.dataset.asc=asc?'1':'0'})</script></main></html>"
    (OUT / "dashboard.html").write_text(body, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.inventory:
        inventory()
    if args.run:
        run(args.limit)

"""Point-in-time features for every closed formation-variant signal.

All rolling comparisons use earlier candles only. The current candle is the
last fully closed candle before the signal's entry_time. No exit-derived value
is used as a predictor. Per-symbol parts make long runs resumable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BAR_ROOT = ROOT / "data" / "bars"
OUT = ROOT / "output" / "ml"
PARTS = OUT / "parts"
WINDOWS = (5, 20, 50, 200)
EPS = 1e-8


def roll(s: pd.Series, n: int):
    return s.rolling(n, min_periods=n).mean()


def rma(s: pd.Series, n: int):
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def signal_series(frame: pd.DataFrame, btc_close: pd.Series):
    o, h, l, c, v = (frame[x].astype(float).reset_index(drop=True) for x in
                     ("open", "high", "low", "close", "volume"))
    b = btc_close.reset_index(drop=True)
    prev = c.shift(1)
    tr = pd.concat([(h-l), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    atr = rma(tr, 14).clip(lower=EPS)
    delta = c.diff()
    up, down = rma(delta.clip(lower=0), 14), rma((-delta).clip(lower=0), 14)
    rsi = 100 - 100 / (1 + up / down.clip(lower=EPS))
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12-ema26
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    plus = h.diff().clip(lower=0)
    minus = (-l.diff()).clip(lower=0)
    plus_dm = plus.where(plus > minus, 0)
    minus_dm = minus.where(minus > plus, 0)
    plus_di = 100*rma(plus_dm,14)/atr
    minus_di = 100*rma(minus_dm,14)/atr
    dx = 100*(plus_di-minus_di).abs()/(plus_di+minus_di).clip(lower=EPS)
    span = (h-l).clip(lower=EPS)
    quote_volume = c*v
    vol_prev20 = v.shift(1).rolling(20,min_periods=20).mean()
    q_prev20 = quote_volume.shift(1).rolling(20,min_periods=20).mean()
    bb_mid, bb_std = roll(c,20), c.rolling(20,min_periods=20).std()
    low14, high14 = l.rolling(14,min_periods=14).min(),h.rolling(14,min_periods=14).max()
    ratio = c/b.clip(lower=EPS)
    series = {
        "price_return_1":c.pct_change(),
        "price_ema20_gap":c/c.ewm(span=20,adjust=False).mean()-1,
        "price_ema50_gap":c/c.ewm(span=50,adjust=False).mean()-1,
        "price_ema200_gap":c/c.ewm(span=200,adjust=False).mean()-1,
        "ema20_slope":c.ewm(span=20,adjust=False).mean().pct_change(),
        "rsi14":rsi,
        "stoch_k14":100*(c-low14)/(high14-low14).clip(lower=EPS),
        "macd_hist_atr":hist/atr,
        "adx14":rma(dx,14),
        "atr14_pct":atr/c.clip(lower=EPS),
        "realized_vol20":c.pct_change().rolling(20,min_periods=20).std(),
        "bb_width20":4*bb_std/bb_mid.clip(lower=EPS),
        "candle_range_pct":(h-l)/c.clip(lower=EPS),
        "body_range":(c-o)/span,
        "upper_wick_range":(h-pd.concat([o,c],axis=1).max(axis=1))/span,
        "lower_wick_range":(pd.concat([o,c],axis=1).min(axis=1)-l)/span,
        "close_location":(c-l)/span,
        "log_quote_volume":np.log1p(quote_volume),
        "volume_rvol20":v/vol_prev20.clip(lower=EPS),
        "turnover_rvol20":quote_volume/q_prev20.clip(lower=EPS),
        "signed_turnover_rvol20":np.sign(c-o)*quote_volume/q_prev20.clip(lower=EPS),
        "obv_flow_rvol20":np.sign(c.diff())*quote_volume/q_prev20.clip(lower=EPS),
        "coin_btc_ratio_log":np.log(ratio.clip(lower=EPS)),
    }
    percent_names = {"rsi14","stoch_k14","adx14","atr14_pct","realized_vol20",
                     "bb_width20","candle_range_pct","volume_rvol20","turnover_rvol20"}
    out = {}
    for name,s in series.items():
        s = pd.Series(s.to_numpy(),index=frame.index).replace([np.inf,-np.inf],np.nan)
        out[name] = s
        for n in WINDOWS:
            previous = s.shift(1).rolling(n,min_periods=n).mean()
            if name in percent_names:
                out[f"{name}_vs_prev{n}"] = (s-previous)/previous.abs().clip(lower=EPS)
            else:
                out[f"{name}_vs_prev{n}"] = s-previous
    for n in WINDOWS:
        out[f"prior_high_{n}_distance_pct"] = h.shift(1).rolling(n,min_periods=n).max()/c-1
        out[f"prior_low_{n}_distance_pct"] = c/l.shift(1).rolling(n,min_periods=n).min()-1
        out[f"coin_return_{n}"] = c/c.shift(n)-1
        out[f"btc_return_{n}"] = b/b.shift(n)-1
        out[f"relative_return_btc_{n}"] = out[f"coin_return_{n}"]-out[f"btc_return_{n}"]
        out[f"coin_btc_ratio_gap_{n}"] = ratio/ratio.shift(1).rolling(n,min_periods=n).mean()-1
    for n in (20,50,200):
        out[f"btc_correlation_{n}"] = c.pct_change().rolling(n,min_periods=n).corr(b.pct_change())
    # A pivot is only available after 3 candles to its right have closed.
    pivot_h = h.eq(h.rolling(7,center=True,min_periods=7).max()).shift(3).fillna(False)
    pivot_l = l.eq(l.rolling(7,center=True,min_periods=7).min()).shift(3).fillna(False)
    out["confirmed_pivot_high_distance_pct"] = h.shift(3).where(pivot_h).ffill()/c-1
    out["confirmed_pivot_low_distance_pct"] = c/l.shift(3).where(pivot_l).ffill()-1
    out["volume_vs_price_move"] = quote_volume/(c.pct_change().abs()+1e-4)
    return pd.DataFrame(out,index=frame.index).replace([np.inf,-np.inf],np.nan)


def load_bars(symbol: str, timeframe: str):
    path = BAR_ROOT / timeframe / f"{symbol}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(path)
    x = pd.read_csv(path)
    x = x.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    for name in ("open_time","open","high","low","close","volume","close_time"):
        x[name] = pd.to_numeric(x[name],errors="coerce")
    return x


def build_bar_features(symbol: str, timeframe: str, btc: pd.DataFrame):
    df = load_bars(symbol,timeframe)
    duration = 3_600_000 if timeframe=="1h" else 14_400_000
    segment = df.open_time.diff().ne(duration).cumsum()
    btc_close = btc.set_index("open_time").close.reindex(df.open_time).reset_index(drop=True)
    chunks=[]
    for _,ids in df.groupby(segment,sort=False).groups.items():
        pos=np.asarray(list(ids))
        part=df.iloc[pos].reset_index(drop=True)
        bpart=btc_close.iloc[pos].reset_index(drop=True)
        f=signal_series(part,bpart)
        f.index=pos
        chunks.append(f)
    features=pd.concat(chunks).sort_index()
    return df,features


def geometry(row: pd.Series, last_close: float):
    p=json.loads(row.pivot_prices) if isinstance(row.pivot_prices,str) else []
    t=json.loads(row.pivot_times) if isinstance(row.pivot_times,str) else []
    dur=3_600_000 if row.timeframe=="1h" else 14_400_000
    vals={
        "pivot_width":float(row.pivot_width),
        "pattern_age_bars":(row.entry_time-row.known_time)/dur,
        "confirmation_lag_bars":(row.confirm_time-row.known_time)/dur,
        "retest_lag_bars":(row.retest_time-row.confirm_time)/dur if pd.notna(row.retest_time) else 0.,
        "trigger_gap_pct":last_close/row.trigger-1 if row.trigger>0 else np.nan,
        "raw_stop_gap_pct":row.raw_stop/last_close-1,
        "raw_stop_gap_atr":(row.raw_stop-last_close)/row.atr_at_confirm if row.atr_at_confirm>0 else np.nan,
        "confirm_atr_pct":row.atr_at_confirm/last_close,
        "pivot_span_bars":(t[-1]-t[0])/dur if len(t)>1 else np.nan,
        "pivot_range_atr":(max(p)-min(p))/row.atr_at_confirm if p and row.atr_at_confirm>0 else np.nan,
        "pivot_last_move_atr":(p[-1]-p[-2])/row.atr_at_confirm if len(p)>1 and row.atr_at_confirm>0 else np.nan,
        "is_retest_variant":int(str(row.variant).startswith("retest")),
    }
    if len(p)==4 and int(row.pattern_id)==65:
        ab,bc,cd=p[1]-p[0],p[1]-p[2],p[3]-p[2]
        vals["abcd_bc_ab"]=bc/ab if ab>0 else np.nan
        vals["abcd_cd_ab"]=cd/ab if ab>0 else np.nan
        vals["abcd_cd_bc"]=cd/bc if bc>0 else np.nan
    else:
        vals.update({"abcd_bc_ab":np.nan,"abcd_cd_ab":np.nan,"abcd_cd_bc":np.nan})
    return vals


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--limit",type=int,default=0,help="Smoke test only; 0 means all symbols")
    args=ap.parse_args()
    OUT.mkdir(exist_ok=True,parents=True); PARTS.mkdir(exist_ok=True,parents=True)
    ledger=pd.read_csv(ROOT/"output"/"all_trades.csv",low_memory=False)
    ledger=ledger[ledger.status.eq("closed")].copy()
    symbols=sorted(ledger.symbol.unique())
    if args.limit: symbols=symbols[:args.limit]
    btc={tf:load_bars("BTCUSDT",tf) for tf in ("1h","4h")}
    failures={}
    for i,symbol in enumerate(symbols,1):
        part_path=PARTS/f"{symbol}.parquet"
        if part_path.exists():
            print(f"{i}/{len(symbols)} {symbol}: cached",flush=True)
            continue
        try:
            pieces=[]
            for tf,group in ledger[ledger.symbol.eq(symbol)].groupby("timeframe"):
                bars,feat=build_bar_features(symbol,tf,btc[tf])
                duration=3_600_000 if tf=="1h" else 14_400_000
                idx=np.searchsorted(bars.open_time.to_numpy(),group.entry_time.to_numpy()-duration)
                valid=(idx>=0)&(idx<len(bars))
                idx_safe=np.clip(idx,0,len(bars)-1)
                valid &= bars.open_time.to_numpy()[idx_safe]==group.entry_time.to_numpy()-duration
                valid &= bars.close_time.to_numpy()[idx_safe]<group.entry_time.to_numpy()
                if not valid.all():
                    raise ValueError(f"{symbol}/{tf}: {sum(~valid)} signals lack a closed decision bar")
                rows=feat.iloc[idx].reset_index(drop=True)
                base=group.reset_index(drop=True)
                for col in ("symbol","pattern_id","timeframe","variant","entry_time","exit_time",
                            "exit_reason","known_time","confirm_time","retest_time","horizon_bars",
                            "entry_price","stop","target","net_pnl"):
                    rows[col]=base[col].to_numpy()
                geometry_rows=[geometry(row,float(bars.close.iloc[j])) for row,j in zip(base.itertuples(index=False),idx)]
                rows=pd.concat([rows,pd.DataFrame(geometry_rows)],axis=1)
                rows["net_R"] = base.net_pnl.to_numpy()/((base.stop-base.entry_price)/base.entry_price).to_numpy()
                rows["tp_vs_sl"] = np.where(base.exit_reason.eq("target"),1.,
                                             np.where(base.exit_reason.isin(["stop","stop_gap"]),0.,np.nan))
                keys=[f"{r.symbol}|{r.pattern_id}|{r.entry_time}|{r.known_time}|{r.trigger:.12g}|{r.raw_stop:.12g}|{r.pivot_times}"
                      for r in base.itertuples(index=False)]
                rows["event_id"]=[hashlib.blake2s(x.encode(),digest_size=12).hexdigest() for x in keys]
                pieces.append(rows)
            result=pd.concat(pieces,ignore_index=True)
            numeric=result.select_dtypes(include="number").columns.difference(
                ["pattern_id","entry_time","exit_time","known_time","confirm_time","retest_time","horizon_bars"])
            result[numeric]=result[numeric].astype("float32")
            result.to_parquet(part_path,index=False)
            if i%10==0 or i==len(symbols):print(f"{i}/{len(symbols)} {symbol}: {len(result)} rows, {len(result.columns)} columns",flush=True)
        except Exception as exc:
            failures[symbol]=str(exc)
            print(f"{i}/{len(symbols)} {symbol}: ERROR {exc}",flush=True)
    (OUT/"feature_failures.json").write_text(json.dumps(failures,indent=2),encoding="utf-8")
    files=[PARTS/f"{s}.parquet" for s in symbols if (PARTS/f"{s}.parquet").exists()]
    if not files:raise RuntimeError("No feature parts were built")
    all_rows=pd.concat([pd.read_parquet(p) for p in files],ignore_index=True)
    all_rows=all_rows.sort_values(["entry_time","symbol","pattern_id","variant"]).reset_index(drop=True)
    all_rows["event_weight"]=1/all_rows.groupby("event_id").event_id.transform("size")
    all_rows.to_parquet(OUT/"signal_features.parquet",index=False)
    label_cols={"symbol","pattern_id","timeframe","variant","entry_time","exit_time","exit_reason",
                "known_time","confirm_time","retest_time","event_id","entry_price","stop","target",
                "net_pnl","net_R","tp_vs_sl","event_weight"}
    manifest={"row_count":len(all_rows),"event_count":all_rows.event_id.nunique(),
              "symbol_count":all_rows.symbol.nunique(),"feature_count":len(all_rows.columns.difference(label_cols)),
              "features":sorted(set(all_rows.columns)-label_cols),"windows":list(WINDOWS),
              "definition":"Current=last completed candle before entry. Previous means exclude current candle.",
              "missing_count":all_rows.isna().sum().sort_values(ascending=False).head(25).to_dict(),
              "failed_symbols":failures}
    (OUT/"feature_manifest.json").write_text(json.dumps(manifest,indent=2,default=int),encoding="utf-8")
    print(f"DONE {len(all_rows)} rows / {manifest['event_count']} events / {manifest['feature_count']} features / {len(failures)} failed symbols",flush=True)


if __name__=="__main__":main()

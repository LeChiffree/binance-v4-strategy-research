"""Apply the frozen 2021–24 V4 model to USD-M Futures signal candles.

The fitted training rows and model specification remain unchanged.  New
Futures OHLCV, activity, funding and 525-coin market context are point in time.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

import backtest
import build_filtre_v3_features as v3
import build_signal_features as sf
import leverage_scenarios as lev
import download_d_active_525_funding as active_funding
import v4_feature_build as old

ROOT = Path(__file__).resolve().parents[1]
BAR_ROOT = Path(os.environ.get("BINANCE_DATA_ROOT", ROOT / "data/binance")) / "active_525/futures_bars"
OUT = ROOT / "output/active_525_five_stage"
OUT.mkdir(parents=True, exist_ok=True)
WINDOWS = (5, 20, 50, 200)
CORE = ["variant", "pattern_id", "timeframe"]
COLS = CORE + [c for c in old.d if c not in ["event_id", "symbol", "entry_time"] + CORE]


def market_context(signals: pd.DataFrame) -> pd.DataFrame:
    values = pd.DataFrame(index=signals.index)
    needed = ("market_breadth_vs_btc_5", "market_breadth_vs_btc_20",
              "market_breadth_vs_btc_200", "coin_return_percentile_20",
              "coin_return_percentile_50")
    for c in needed:
        values[c] = np.nan
    all_symbols = sorted(p.stem for p in (BAR_ROOT / "1h").glob("*.parquet"))
    lookup = {s: i for i, s in enumerate(all_symbols)}
    for tf, duration in (("1h", 3_600_000), ("4h", 14_400_000)):
        ix = signals.index[signals.timeframe.eq(tf)]
        if len(ix) == 0:
            continue
        times = np.unique(signals.loc[ix, "entry_time"].to_numpy(dtype=np.int64) - duration)
        idx = np.searchsorted(times, signals.loc[ix, "entry_time"].to_numpy(dtype=np.int64)-duration)
        symbol_idx = np.asarray([lookup[s] for s in signals.loc[ix, "symbol"]])
        mats = {n: np.full((len(times), len(all_symbols)), np.nan, np.float32) for n in WINDOWS}
        for j, symbol in enumerate(all_symbols):
            path = BAR_ROOT / tf / f"{symbol}.parquet"
            if not path.exists():
                continue
            bars = pd.read_parquet(path, columns=["open_time", "close"])
            t = bars.open_time.to_numpy(dtype=np.int64)
            p = bars.close.to_numpy(dtype=float)
            if len(t) == 0:
                continue
            seg = np.cumsum(np.r_[True, np.diff(t) != duration])
            loc = np.searchsorted(t, times)
            safe = np.minimum(loc, len(t)-1)
            valid = (loc < len(t)) & (t[safe] == times)
            for n in WINDOWS:
                good = valid & (safe >= n) & (seg[safe] == seg[np.maximum(safe-n, 0)])
                k = safe[good]
                mats[n][good, j] = (p[k]/p[k-n]-1).astype(np.float32)
        btc_col = lookup["BTCUSDT"]
        for n, mat in mats.items():
            count = np.isfinite(mat).sum(axis=1)
            btc = mat[:, btc_col]
            others = count-np.isfinite(btc).astype(int)
            breadth = np.divide((mat > btc[:, None]).sum(axis=1), others,
                                out=np.full(len(times), np.nan), where=others > 0)
            if n in (5, 20, 200):
                values.loc[ix, f"market_breadth_vs_btc_{n}"] = breadth[idx]
            if n in (20, 50):
                this = mat[idx, symbol_idx]
                below = np.nansum(mat[idx] < this[:, None], axis=1)
                pct = np.divide(below, count[idx],
                                out=np.full(len(ix), np.nan), where=count[idx] > 0)
                pct[~np.isfinite(this)] = np.nan
                values.loc[ix, f"coin_return_percentile_{n}"] = pct
        print("market context", tf, len(times), "times", flush=True)
    return values


def feature_rows(signals: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    btc = {tf: pd.read_parquet(BAR_ROOT / tf / "BTCUSDT.parquet") for tf in ("1h", "4h")}
    for i, ((symbol, tf), group) in enumerate(signals.groupby(["symbol", "timeframe"]), 1):
        duration = 3_600_000 if tf == "1h" else 14_400_000
        bars = pd.read_parquet(BAR_ROOT / tf / f"{symbol}.parquet")
        decision = group.entry_time.to_numpy(dtype=np.int64)-duration
        loc = pd.Index(bars.open_time).get_indexer(decision)
        if (loc < 0).any() or not (bars.close_time.iloc[loc].to_numpy() < group.entry_time.to_numpy()).all():
            raise ValueError(f"invalid decision bar {symbol}/{tf}")
        seg = bars.open_time.diff().ne(duration).cumsum()
        btc_close = btc[tf].set_index("open_time").close.reindex(bars.open_time).reset_index(drop=True)
        chunks = []
        for _, ids in bars.groupby(seg, sort=False).groups.items():
            at = np.asarray(list(ids))
            feat = sf.signal_series(bars.iloc[at].reset_index(drop=True), btc_close.iloc[at].reset_index(drop=True))
            feat.index = at
            chunks.append(feat)
        f = pd.concat(chunks).sort_index().iloc[loc].reset_index(drop=True)
        g = group.reset_index(drop=True)
        geometry = pd.DataFrame([sf.geometry(r, float(bars.close.iloc[k])) for r, k in zip(g.itertuples(index=False), loc)])
        geometry = geometry.drop(columns=[c for c in geometry if c in g.columns])
        f = pd.concat([g, f, geometry], axis=1)
        count = bars.trade_count.astype(float)
        previous = count.groupby(seg).transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
        f["trade_count_gap_20"] = (count / previous.replace(0, np.nan)-1).iloc[loc].to_numpy()
        f["avg_trade_usdt"] = (bars.quote_volume / count.replace(0, np.nan)).iloc[loc].to_numpy()
        f["taker_buy_share"] = (bars.taker_buy_quote / bars.quote_volume.replace(0, np.nan)).iloc[loc].to_numpy()
        _, metrics = v3.bar_metrics(bars, duration)
        for name in metrics:
            f[name] = metrics[name].iloc[loc].to_numpy()
        history = active_funding.load(symbol)
        btc_funding = active_funding.load("BTCUSDT")
        if history is not None and len(history):
            last, mean8, _ = v3.latest_funding(history.sort_values("fundingTime"), g.entry_time.to_numpy(dtype=np.int64))
            f["funding_last_bps"] = last
            f["funding_mean8_bps"] = mean8
        else:
            f["funding_last_bps"] = np.nan
            f["funding_mean8_bps"] = np.nan
        if btc_funding is not None and len(btc_funding):
            btc_last, _, _ = v3.latest_funding(btc_funding.sort_values("fundingTime"), g.entry_time.to_numpy(dtype=np.int64))
            f["funding_vs_btc_bps"] = f.funding_last_bps-btc_last
        else:
            f["funding_vs_btc_bps"] = np.nan
        f.index = group.index
        pieces.append(f)
        if i % 50 == 0:
            print("features", i, "symbol/timeframe groups", flush=True)
    result = pd.concat(pieces).sort_index()
    context = market_context(signals)
    for name in context:
        result[name] = context[name]
    result["market_breadth_change_5_20"] = result.market_breadth_vs_btc_5-result.market_breadth_vs_btc_20
    result["resistance_distance_atr20"] = result.prior_high_20_distance_pct/result.atr14_pct.clip(lower=1e-8)
    result["taker_sell_pressure_rvol"] = (.5-result.taker_buy_share)*result.volume_rvol20
    result["stop_gap_pct"] = (result.raw_stop+.25*result.atr_at_confirm)/(result.signal_open*(1-backtest.SLIP))-1
    result["stop_gap_pct"] = result.stop_gap_pct.clip(lower=1e-6)
    result["log_avg_trade_usdt"] = np.log1p(result.avg_trade_usdt.clip(lower=0))
    result["btc_momentum_accel"] = result.btc_return_20-result.btc_return_200*.1
    result["btc_momentum_per_atr"] = result.btc_return_20/result.atr14_pct.clip(lower=1e-4)
    result["support_room_to_stop"] = result.prior_low_20_distance_pct/result.stop_gap_pct
    result["resistance_room_to_stop"] = result.prior_high_20_distance_pct/result.stop_gap_pct
    result["sell_pressure_liquidity"] = (.5-result.taker_buy_share)*result.log_avg_trade_usdt
    result["atr_times_rvol"] = result.atr14_pct*result.volume_rvol20
    result["funding_times_btc_trend"] = result.funding_last_bps*result.btc_return_20
    return result.replace([np.inf, -np.inf], np.nan)


def main() -> None:
    signals = pd.read_parquet(OUT / "raw_signals.parquet")
    assert len(signals) and not signals.trade_id.duplicated().any()
    f = feature_rows(signals)
    assert set(COLS).issubset(f.columns), set(COLS)-set(f.columns)
    labels = pd.read_parquet(ROOT / "output/ml/all_2x_labels.parquet")
    training = labels.merge(old.d, on=["event_id", "variant"], suffixes=("", "_feature"), validate="one_to_one")
    year = pd.to_datetime(training.entry_time, unit="ms", utc=True).dt.year
    cutoff = pd.Timestamp("2025-01-01", tz="UTC").value // 10**6
    fit = year.between(2021, 2024) & training.base_exit_time.lt(cutoff)
    x = training[COLS].copy()
    x_new = f[COLS].copy()
    for c in CORE:
        categories = sorted(set(x[c].astype(str)) | set(x_new[c].astype(str)))
        dtype = pd.CategoricalDtype(categories=categories)
        x[c] = x[c].astype(str).astype(dtype)
        x_new[c] = x_new[c].astype(str).astype(dtype)
    model = lgb.LGBMRegressor(n_estimators=160, learning_rate=.035, num_leaves=7, max_depth=4,
                              min_child_samples=120, reg_lambda=15, colsample_bytree=.8,
                              random_state=20261011, n_jobs=2, verbosity=-1)
    model.fit(x.loc[fit], training.loc[fit, "net_pnl_usdt"], categorical_feature=CORE)
    f["v4_score_2021_2024"] = model.predict(x_new)
    f[["event_id", "trade_id", "symbol", "variant", "timeframe", "entry_time",
       "btc_return_200", "avg_trade_usdt", "coin_return_200", "body_range",
       "close_location", "v4_score_2021_2024"]].to_parquet(OUT / "stage_features.parquet", index=False)
    f[COLS + ["event_id", "trade_id", "symbol", "entry_time"]].to_parquet(OUT / "v4_features.parquet", index=False)
    old_scores = np.asarray(json.loads((ROOT / "output/v4_threshold_sweep/scores_28_models.json").read_text())["2021-2024"])
    old_minute = pd.read_parquet(ROOT / "output/precise_1m/trades.parquet", columns=["event_id", "variant"])
    check = old_minute.merge(old.d, on=["event_id", "variant"], validate="one_to_one")[COLS].copy()
    for c in CORE:
        check[c] = check[c].astype(str).astype(x[c].dtype)
    diff = float(np.max(np.abs(model.predict(check)-old_scores)))
    (OUT / "v4_reconstruction.json").write_text(json.dumps({"training_rows": int(fit.sum()),
       "legacy_score_max_abs_difference": diff, "new_signal_rows": len(f)}), encoding="utf-8")
    if diff > 1e-5:
        raise ValueError(f"V4 model reconstruction mismatch {diff}")
    print("V4 scored", len(f), "signals; reconstruction max diff", diff, flush=True)


if __name__ == "__main__":
    main()

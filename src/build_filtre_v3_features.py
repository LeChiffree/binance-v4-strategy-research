"""Build a compact, label-free, point-in-time feature table for filtre_v3.

This does not alter the frozen v1/v2 artifacts or fit a predictive model.
Redundancy pruning uses only the training period before 2024-01-01.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import leverage_scenarios as lev

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "ml"
BARS = ROOT / "data" / "bars"
TRAIN_END = 1704067200000
EPS = 1e-8

# Ordered by interpretability; the first member of a correlated pair survives.
BASE = [
    "pattern_id", "variant", "timeframe",
    "pivot_width", "pattern_age_bars", "trigger_gap_pct", "raw_stop_gap_atr",
    "pivot_range_atr", "abcd_cd_ab",
    "price_ema20_gap", "price_ema200_gap", "rsi14", "rsi14_vs_prev20",
    "adx14", "atr14_pct", "atr14_pct_vs_prev50", "body_range", "close_location",
    "volume_rvol20", "volume_rvol20_vs_prev5", "prior_high_20_distance_pct",
    "prior_low_20_distance_pct", "confirmed_pivot_high_distance_pct",
    "confirmed_pivot_low_distance_pct", "relative_return_btc_5",
    "relative_return_btc_20", "relative_return_btc_200", "btc_return_20",
    "btc_return_200", "btc_correlation_50", "market_breadth_vs_btc_20",
    "market_breadth_vs_btc_200", "coin_return_percentile_20",
    "trade_count_gap_20", "taker_buy_share", "avg_trade_usdt",
]
NEW = [
    "market_breadth_change_5_20", "relative_momentum_atr20",
    "resistance_distance_atr20", "taker_sell_pressure_rvol",
    "bearish_candle_share20", "downside_semivol20", "range_compression5_20",
    "funding_last_bps", "funding_mean8_bps", "funding_vs_btc_bps",
]
CATEGORICAL = {"pattern_id", "variant", "timeframe"}


def bar_metrics(bars: pd.DataFrame, duration: int):
    bars = bars.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    segment = bars.open_time.diff().ne(duration).cumsum()
    bearish = bars.close.lt(bars.open).astype(float)
    returns = bars.close.groupby(segment).pct_change()
    candle_range = (bars.high-bars.low)/bars.close.clip(lower=EPS)
    def rolling_mean(series: pd.Series, size: int):
        return series.groupby(segment).transform(lambda part: part.rolling(size, min_periods=size).mean())
    data = pd.DataFrame({
        "bearish_candle_share20": rolling_mean(bearish, 20),
        "downside_semivol20": np.sqrt(rolling_mean(returns.clip(upper=0).pow(2), 20)),
        "range_compression5_20": rolling_mean(candle_range, 5)/rolling_mean(candle_range, 20)-1,
    })
    return bars, data


def latest_funding(history: pd.DataFrame, entries: np.ndarray):
    times = history.fundingTime.to_numpy(dtype=np.int64)
    rates = history.fundingRate.to_numpy(dtype=float)*10000
    previous = np.searchsorted(times, entries, side="left")-1
    last = np.full(len(entries), np.nan)
    mean8 = np.full(len(entries), np.nan)
    used_time = np.full(len(entries), -1, dtype=np.int64)
    for k, j in enumerate(previous):
        if j >= 0:
            last[k] = rates[j]
            mean8[k] = rates[max(0, j-7):j+1].mean()
            used_time[k] = times[j]
    assert np.all((used_time < entries) | (used_time == -1))
    return last, mean8, used_time


def main():
    labels = pd.read_parquet(OUT / "all_2x_labels.parquet",
                             columns=["event_id", "variant", "symbol", "pattern_id", "entry_time"])
    signal = pd.read_parquet(OUT / "signal_features.parquet")
    assert set(BASE).issubset(signal.columns)
    assert not labels.duplicated(["event_id", "variant"]).any()
    cols = list(dict.fromkeys(["event_id", "variant", "symbol", "entry_time"]+BASE+
                              ["market_breadth_vs_btc_5", "atr14_pct", "relative_return_btc_20",
                               "prior_high_20_distance_pct", "volume_rvol20", "taker_buy_share"]))
    table = labels.merge(signal[cols], on=["event_id", "variant"], how="left",
                         validate="one_to_one", suffixes=("", "_source"), indicator=True)
    assert table._merge.eq("both").all()
    assert (table.entry_time == table.entry_time_source).all()
    assert (table.symbol == table.symbol_source).all()
    table.drop(columns=["_merge", "entry_time_source", "symbol_source"], inplace=True)
    for name in NEW:
        table[name] = np.nan
    table["market_breadth_change_5_20"] = (table.market_breadth_vs_btc_5-
                                           table.market_breadth_vs_btc_20)
    table["relative_momentum_atr20"] = (table.relative_return_btc_20/
                                        (table.atr14_pct.clip(lower=EPS)*np.sqrt(20)))
    table["resistance_distance_atr20"] = (table.prior_high_20_distance_pct/
                                           table.atr14_pct.clip(lower=EPS))
    table["taker_sell_pressure_rvol"] = (0.5-table.taker_buy_share)*table.volume_rvol20

    # Same-period bar inputs are taken from the last fully closed candle.
    checked = 0
    for (symbol, timeframe), ids in table.groupby(["symbol", "timeframe"]).groups.items():
        duration = 3_600_000 if timeframe == "1h" else 14_400_000
        bars = pd.read_csv(BARS / timeframe / f"{symbol}.csv.gz",
                           usecols=["open_time", "open", "high", "low", "close", "close_time"])
        bars, metrics = bar_metrics(bars, duration)
        entries = table.loc[ids, "entry_time"].to_numpy(dtype=np.int64)
        idx = pd.Index(bars.open_time.to_numpy(dtype=np.int64)).get_indexer(entries-duration)
        assert (idx >= 0).all(), (symbol, timeframe, "missing decision bar")
        assert (bars.close_time.to_numpy(dtype=np.int64)[idx] < entries).all()
        table.loc[ids, ["bearish_candle_share20", "downside_semivol20",
                        "range_compression5_20"]] = metrics.iloc[idx].to_numpy()
        checked += len(ids)
    assert checked == len(table)

    btc = lev.load_funding("BTCUSDT")
    assert btc is not None and len(btc) > 0
    btc = btc.sort_values("fundingTime")
    for symbol, ids in table.groupby("symbol").groups.items():
        history = lev.load_funding(symbol)
        if history is None or history.empty:
            raise RuntimeError(f"Missing funding for {symbol}")
        entries = table.loc[ids, "entry_time"].to_numpy(dtype=np.int64)
        last, mean8, _ = latest_funding(history.sort_values("fundingTime"), entries)
        btc_last, _, _ = latest_funding(btc, entries)
        table.loc[ids, "funding_last_bps"] = last
        table.loc[ids, "funding_mean8_bps"] = mean8
        table.loc[ids, "funding_vs_btc_bps"] = last-btc_last

    candidates = BASE+NEW
    numeric = [name for name in candidates if name not in CATEGORICAL]
    table[numeric] = table[numeric].apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    prior = table.entry_time.lt(TRAIN_END)
    corr = table.loc[prior, numeric].corr(method="spearman").abs()
    selected = list(CATEGORICAL)
    removed_corr = {}
    for name in numeric:
        duplicate = next((other for other in selected if other not in CATEGORICAL
                          and pd.notna(corr.loc[name, other]) and corr.loc[name, other] >= 0.90), None)
        if duplicate:
            removed_corr[name] = {"kept": duplicate, "spearman_abs_training": float(corr.loc[name, duplicate])}
        else:
            selected.append(name)
    # Pattern 65 geometry is genuinely inapplicable elsewhere, so keep NaN.
    if "abcd_cd_ab" not in selected:
        selected.append("abcd_cd_ab")
        removed_corr.pop("abcd_cd_ab", None)
    table = table[["event_id", "variant", "symbol", "entry_time"]+
                  [name for name in selected if name != "variant"]].copy()
    assert not table.duplicated(["event_id", "variant"]).any()
    assert len(table) == len(labels)
    assert not set(table.columns) & {"net_pnl", "net_pnl_usdt", "exit_time", "loss", "accept"}
    table.to_parquet(OUT / "filtre_v3_features.parquet", index=False)
    v2 = json.loads((OUT / "filtre_v2_feature_audit.json").read_text(encoding="utf-8"))
    manifest = {
        "version": "filtre_v3_feature_set", "status": "features_ready_no_model_trained",
        "rows": len(table), "unique_events": int(table.event_id.nunique()),
        "selection_period": "entry_time < 2024-01-01 UTC; outcome labels not used",
        "redundancy_rule": "Greedy absolute Spearman >= 0.90 on pre-2024 training signals, ordered by interpretability",
        "feature_count": len(selected), "features": selected,
        "new_features_kept": [name for name in NEW if name in selected],
        "removed_from_v2": [name for name in v2["features"] if name not in selected],
        "removed_correlated_candidates": removed_corr,
        "training_missing_pct": {name: float(100*table.loc[prior, name].isna().mean()) for name in selected},
        "point_in_time_checks": {"decision_bars_checked": checked,
            "bar_close_before_entry": True, "funding_time_strictly_before_entry": True,
            "no_outcome_or_model_prediction_columns": True},
        "sources": {"existing_signal_features": "signal_features.parquet",
                    "cached_closed_bars": "data/bars/{timeframe}/{symbol}.csv.gz",
                    "historical_funding": "data/futures/funding/{symbol}.csv.gz"},
        "note": "Feature selection is exploratory because 2025+ was previously inspected for v1/v2; no future performance claim."
    }
    (OUT / "filtre_v3_feature_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"V3 features: {len(table)} rows, {len(selected)} features; "
          f"new kept={len(manifest['new_features_kept'])}, correlated removed={len(removed_corr)}", flush=True)


if __name__ == "__main__":
    main()

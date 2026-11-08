"""Point-in-time feature reconstruction for the frozen V4 model."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import build_signal_features as base_features
import backtest
import market
from config import FOUR_HOURS, ONE_HOUR

EPS = 1e-8


def bar_metrics(bars: pd.DataFrame, duration: int) -> pd.DataFrame:
    bars = bars.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    segment = bars.open_time.diff().ne(duration).cumsum()
    bearish = bars.close.lt(bars.open).astype(float)
    returns = bars.close.groupby(segment).pct_change()
    candle_range = (bars.high - bars.low) / bars.close.clip(lower=EPS)

    def rolling_mean(series: pd.Series, size: int):
        return series.groupby(segment).transform(
            lambda part: part.rolling(size, min_periods=size).mean())

    return pd.DataFrame({
        "bearish_candle_share20": rolling_mean(bearish, 20),
        "downside_semivol20": np.sqrt(rolling_mean(returns.clip(upper=0).pow(2), 20)),
        "range_compression5_20": rolling_mean(candle_range, 5) / rolling_mean(candle_range, 20) - 1,
    })


def _contiguous(frame: pd.DataFrame, duration: int, entry_ms: int) -> pd.DataFrame:
    closed = frame[frame.open_time + duration <= entry_ms].copy()
    if closed.empty:
        raise ValueError("no completed candle")
    last = int(closed.open_time.iloc[-1])
    if last != entry_ms - duration:
        raise ValueError("last decision candle is stale")
    gap = np.flatnonzero(np.diff(closed.open_time.to_numpy(dtype=np.int64)) != duration)
    if len(gap):
        closed = closed.iloc[int(gap[-1]) + 1:].reset_index(drop=True)
    if len(closed) < 420:
        raise ValueError(f"only {len(closed)} contiguous completed candles")
    return closed


def _return(frame: pd.DataFrame, entry: int, duration: int, n: int):
    closed = frame[frame.open_time + duration <= entry]
    if len(closed) <= n or int(closed.open_time.iloc[-1]) != entry - duration:
        return np.nan
    tail = closed.iloc[-n-1:]
    if np.any(np.diff(tail.open_time.to_numpy(dtype=np.int64)) != duration):
        return np.nan
    return float(tail.close.iloc[-1] / tail.close.iloc[0] - 1)


def market_context(frames: dict[str, pd.DataFrame], timeframe: str, entry_ms: int):
    duration = ONE_HOUR if timeframe == "1h" else FOUR_HOURS
    returns = {n: {} for n in (5, 20, 50, 200)}
    for symbol, bars in frames.items():
        for n in returns:
            value = _return(bars, entry_ms, duration, n)
            if np.isfinite(value):
                returns[n][symbol] = value
    if any("BTCUSDT" not in returns[n] for n in returns):
        raise ValueError("BTC context incomplete")
    if len(returns[20]) < 50:
        raise ValueError(f"only {len(returns[20])} context coins")
    return returns


def make_features(signal: dict, frames: dict[str, pd.DataFrame],
                  context: dict[int, dict[str, float]], model_columns: list[str]):
    symbol = signal["symbol"]
    timeframe = signal["timeframe"]
    entry = int(signal["entry_time"])
    duration = ONE_HOUR if timeframe == "1h" else FOUR_HOURS
    coin = _contiguous(frames[symbol], duration, entry)
    btc = frames["BTCUSDT"]
    btc_close = btc.set_index("open_time").close.reindex(coin.open_time).reset_index(drop=True)
    if btc_close.tail(201).isna().any():
        raise ValueError("BTC candles not aligned")
    feat = base_features.signal_series(coin, btc_close).iloc[-1].to_dict()
    row = pd.Series(signal)
    feat.update(base_features.geometry(row, float(coin.close.iloc[-1])))

    # Training cross-section: proportion of other symbols outperforming BTC,
    # and fraction of symbols with a return below this coin's return.
    for n in (5, 20, 50, 200):
        values = context[n]
        btc_return = values["BTCUSDT"]
        others = [v for s, v in values.items() if s != "BTCUSDT"]
        feat[f"market_breadth_vs_btc_{n}"] = sum(v > btc_return for v in others) / len(others)
        if symbol in values:
            feat[f"coin_return_percentile_{n}"] = sum(v < values[symbol] for v in values.values()) / len(values)

    # Binance OHLCV activity fields, observed on the last fully closed candle.
    count = coin.trade_count.astype(float)
    last = coin.iloc[-1]
    if last.trade_count <= 0 or last.quote_volume <= 0:
        raise ValueError("missing last-candle activity")
    feat["avg_trade_usdt"] = float(last.quote_volume / last.trade_count)
    feat["taker_buy_share"] = float(last.taker_buy_quote / last.quote_volume)
    feat["trade_count_gap_20"] = float(count.iloc[-1] / count.iloc[-21:-1].mean() - 1)

    # V3 additions use the same completed signal candle.
    metric = bar_metrics(coin, duration)
    feat.update(metric.iloc[-1].to_dict())
    feat["market_breadth_change_5_20"] = feat["market_breadth_vs_btc_5"] - feat["market_breadth_vs_btc_20"]
    feat["resistance_distance_atr20"] = feat["prior_high_20_distance_pct"] / max(feat["atr14_pct"], EPS)
    feat["taker_sell_pressure_rvol"] = (.5 - feat["taker_buy_share"]) * feat["volume_rvol20"]

    symbol_rates = market.funding_recent(symbol, entry)
    btc_rates = market.funding_recent("BTCUSDT", entry)
    if not symbol_rates or not btc_rates:
        raise ValueError("funding history unavailable")
    feat["funding_last_bps"] = symbol_rates[-1][1] * 10000
    feat["funding_mean8_bps"] = float(np.mean([r for _, r in symbol_rates[-8:]]) * 10000)
    feat["funding_vs_btc_bps"] = feat["funding_last_bps"] - btc_rates[-1][1] * 10000

    # V4 extensions. The risk is computed from the next-bar open, exactly as
    # the historical research's model input was defined.
    entry_price = float(signal["signal_open"]) * (1 - backtest.SLIP)
    stop = float(signal["raw_stop"]) + .25 * float(signal["atr_at_confirm"])
    risk = max(stop / entry_price - 1, 1e-6)
    feat["stop_gap_pct"] = risk
    feat["log_avg_trade_usdt"] = math.log1p(max(feat["avg_trade_usdt"], 0))
    feat["btc_momentum_accel"] = feat["btc_return_20"] - feat["btc_return_200"] * .1
    feat["btc_momentum_per_atr"] = feat["btc_return_20"] / max(feat["atr14_pct"], 1e-4)
    feat["support_room_to_stop"] = feat["prior_low_20_distance_pct"] / risk
    feat["resistance_room_to_stop"] = feat["prior_high_20_distance_pct"] / risk
    feat["sell_pressure_liquidity"] = (.5 - feat["taker_buy_share"]) * feat["log_avg_trade_usdt"]
    feat["atr_times_rvol"] = feat["atr14_pct"] * feat["volume_rvol20"]
    feat["funding_times_btc_trend"] = feat["funding_last_bps"] * feat["btc_return_20"]
    feat.update({"pattern_id": str(signal["pattern_id"]), "variant": signal["variant"],
                 "timeframe": timeframe})
    missing_names = sorted(set(model_columns) - set(feat))
    if missing_names:
        raise ValueError(f"feature columns unavailable: {missing_names}")
    essentials = ["btc_return_200", "avg_trade_usdt", "rsi14", "atr14_pct", "volume_rvol20",
                  "market_breadth_vs_btc_20", "funding_last_bps"]
    if any(not np.isfinite(feat[name]) for name in essentials):
        raise ValueError("essential model feature missing")
    x = pd.DataFrame([{c: feat[c] for c in model_columns}])
    for col in ("pattern_id", "variant", "timeframe"):
        x[col] = x[col].astype(str).astype("category")
    x = x.replace([np.inf, -np.inf], np.nan)
    return x, feat, entry_price, stop

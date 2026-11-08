"""Read-only replay of hourly decisions that fell inside observation gaps."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

import backtest
import live_features
import market
from config import FOUR_HOURS, ONE_HOUR, SLIPPAGE, FEE_RATE, UNIVERSE, MODEL
import joblib

tz = ZoneInfo("Europe/Istanbul")
times = ["2026-10-13 10:00", "2026-10-14 02:00"]
entries = [int(datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp() * 1000)
           for t in times]
universe = json.loads(UNIVERSE.read_text())
model = joblib.load(MODEL)
symbols = sorted(set(universe["context_symbols"]) | {"BTCUSDT"})
frames, errors = market.batch_bars(symbols, "1h")
result = {"fetch_count": len(frames), "fetch_errors": errors, "decisions": []}

for label, entry in zip(times, entries):
    sliced = {symbol: frame[frame.open_time <= entry].reset_index(drop=True)
              for symbol, frame in frames.items()}
    context = live_features.market_context(sliced, "1h", entry)
    btc_return = context[200]["BTCUSDT"]
    btc_pass = btc_return <= model["btc_return_200_max"]
    backtest.NOW = entry + 200 * FOUR_HOURS
    candidates, signal_errors = [], {}
    for symbol in universe["scan_symbols"]:
        bars = sliced.get(symbol)
        if bars is None or len(bars) < 430 or not (bars.open_time == entry).any():
            continue
        try:
            signals = backtest.signals(bars.iloc[-650:].copy(), symbol, "1h")
            candidates.extend(s for s in signals if s["entry_time"] == entry)
        except Exception as exc:
            signal_errors[symbol] = str(exc)

    decisions = []
    for signal in candidates:
        row = {"symbol": signal["symbol"], "pattern": str(signal["pattern_id"]),
               "variant": signal["variant"]}
        try:
            x, feat, entry_price, stop = live_features.make_features(
                signal, sliced, context, model["features"])
            score = float(model["model"].predict(x)[0])
            liquid = bool(feat["avg_trade_usdt"] >= model["avg_trade_usdt_min"])
            model_pass = bool(score >= model["score_threshold"])
            risk = stop - entry_price
            target = entry_price - 2 * risk
            target_fill, stop_fill = target * (1 + SLIPPAGE), stop * (1 + SLIPPAGE)
            net_win = ((entry_price - target_fill) / entry_price -
                       FEE_RATE * (1 + target_fill / entry_price))
            net_loss = ((entry_price - stop_fill) / entry_price -
                        FEE_RATE * (1 + stop_fill / entry_price))
            rr_pass = bool(risk > 0 and target > 0 and net_loss < 0 and
                           net_win / abs(net_loss) >= 1.5)
            row.update({"btc_pass": bool(btc_pass), "liquidity_pass": liquid,
                        "v4_pass": model_pass, "risk_reward_pass": rr_pass,
                        "paper_entry": bool(btc_pass and liquid and model_pass and rr_pass),
                        "score": round(score, 6),
                        "avg_trade_usdt": round(float(feat["avg_trade_usdt"]), 2),
                        "entry_price": entry_price, "stop": stop, "target": target})
        except Exception as exc:
            row["error"] = str(exc)
        decisions.append(row)

    result["decisions"].append({"time_trt": label, "btc_return_200": btc_return,
                                "btc_pass": bool(btc_pass), "context_count": len(context[20]),
                                "signal_count": len(candidates),
                                "paper_entry_count": sum(bool(x.get("paper_entry")) for x in decisions),
                                "signal_errors": signal_errors, "signals": decisions})

print(json.dumps(result, ensure_ascii=False, indent=2))

"""Read-only replay of signals newer than the frozen historical population."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import backtest
import joblib
import live_features
import market
from config import FOUR_HOURS, ONE_HOUR, UNIVERSE, MODEL

TRT = ZoneInfo("Europe/Istanbul")
START_MS = int(datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc).timestamp() * 1000)
NOW_MS = market.server_time()
universe = json.loads(UNIVERSE.read_text())
model = joblib.load(MODEL)
symbols = sorted(set(universe["context_symbols"]) | {"BTCUSDT"})
result = {"start_utc": datetime.fromtimestamp(START_MS / 1000, timezone.utc).isoformat(),
          "end_utc": datetime.fromtimestamp(NOW_MS / 1000, timezone.utc).isoformat(),
          "timeframes": {}, "signals": []}

for tf, duration in (("1h", ONE_HOUR), ("4h", FOUR_HOURS)):
    frames, errors = market.batch_bars(symbols, tf)
    complete = {s: f[f.open_time + duration <= NOW_MS].reset_index(drop=True)
                for s, f in frames.items()}
    candidates, scan_errors = [], {}
    backtest.NOW = NOW_MS + 200 * FOUR_HOURS
    for symbol in universe["scan_symbols"]:
        bars = frames.get(symbol)
        if bars is None or len(bars) < 430:
            continue
        try:
            found = backtest.signals(bars.iloc[-1000:].copy(), symbol, tf)
            candidates.extend(s for s in found if START_MS <= s["entry_time"] <= NOW_MS)
        except Exception as exc:
            scan_errors[symbol] = str(exc)
    result["timeframes"][tf] = {"fetched": len(frames), "fetch_errors": errors,
                                "scan_errors": scan_errors, "candidate_count": len(candidates)}
    print(f"{tf}: {len(frames)} frames, {len(candidates)} candidates", flush=True)
    context_cache = {}
    for index, signal in enumerate(candidates, 1):
        entry = int(signal["entry_time"])
        row = {"symbol": signal["symbol"], "timeframe": tf,
               "pattern_id": str(signal["pattern_id"]), "variant": signal["variant"],
               "entry_time_trt": datetime.fromtimestamp(entry / 1000, TRT).isoformat(),
               "entry_time": entry, "expiry_time": int(signal["expiry_time"])}
        try:
            if entry not in context_cache:
                cut = {s: f[f.open_time <= entry].reset_index(drop=True)
                       for s, f in frames.items()}
                context_cache[entry] = (cut, live_features.market_context(cut, tf, entry))
            cut, context = context_cache[entry]
            x, feat, entry_price, stop = live_features.make_features(
                signal, cut, context, model["features"])
            score = float(model["model"].predict(x)[0])
            btc_pass = context[200]["BTCUSDT"] <= model["btc_return_200_max"]
            liquid_pass = feat["avg_trade_usdt"] >= model["avg_trade_usdt_min"]
            v4_pass = score >= model["score_threshold"]
            row.update({"btc_pass": bool(btc_pass), "liquid_pass": bool(liquid_pass),
                        "v4_pass": bool(v4_pass), "selected": bool(btc_pass and liquid_pass and v4_pass),
                        "score": score, "btc_return_200": context[200]["BTCUSDT"],
                        "avg_trade_usdt": feat["avg_trade_usdt"],
                        "entry_price": entry_price, "stop": stop,
                        "target": entry_price - 2 * (stop - entry_price)})
        except Exception as exc:
            row["error"] = str(exc)
        result["signals"].append(row)
        if index % 10 == 0:
            print(f"{tf}: evaluated {index}/{len(candidates)}", flush=True)

result["counts"] = {"raw_signals": len(result["signals"]),
                    "btc_liquid": sum(x.get("btc_pass") and x.get("liquid_pass") for x in result["signals"]),
                    "v4_selected": sum(x.get("selected", False) for x in result["signals"]),
                    "feature_errors": sum("error" in x for x in result["signals"])}
print("RESULT_JSON=" + json.dumps(result, ensure_ascii=False))

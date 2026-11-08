"""Minute-level 5x replay of V4-selected recent historical signals."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import market
import numpy as np
from config import FEE_RATE, INITIAL_EQUITY, MARGIN, MMR, NOTIONAL, ONE_MIN, SLIPPAGE

log = Path(__file__).with_name("audit_recent_three_months.log")
data = json.loads(next(line[12:] for line in log.open(encoding="utf-8")
                       if line.startswith("RESULT_JSON=")))
selected = [r for r in data["signals"] if r.get("selected")]
now_ms = market.server_time()
cache = {}
out = []

for r in selected:
    symbol, entry, expiry = r["symbol"], int(r["entry_time"]), int(r["expiry_time"])
    key = (symbol, entry)
    end = min(expiry - ONE_MIN, now_ms // ONE_MIN * ONE_MIN - ONE_MIN)
    if key not in cache or cache[key][0] < end:
        minutes = market.recent_minutes(symbol, entry, end)
        rates = market.funding(symbol, entry, end + ONE_MIN)
        cache[key] = (end, minutes, rates)
    _, minutes, rates = cache[key]
    minutes = minutes[minutes.open_time <= end]
    expected = np.arange(entry, end + ONE_MIN, ONE_MIN, dtype=np.int64)
    if not np.array_equal(minutes.open_time.to_numpy(dtype=np.int64), expected):
        raise ValueError(f"incomplete 1m data: {symbol} {entry} {len(minutes)}/{len(expected)}")
    qty = NOTIONAL / r["entry_price"]
    reason, exit_price, exit_time = "open", None, None
    last_mark = r["entry_price"]
    for bar in minutes.itertuples(index=False):
        t = int(bar.open_time)
        fund = NOTIONAL * sum(rate for ft, rate in rates if ft < t + ONE_MIN)
        liq = (MARGIN - FEE_RATE * NOTIONAL + NOTIONAL + fund) / (qty * (1 + MMR))
        if bar.open >= liq:
            reason, exit_price = "approx_liquidation", float(bar.open)
        elif bar.open >= r["stop"]:
            reason, exit_price = "stop_gap", float(bar.open) * (1 + SLIPPAGE)
        elif r["stop"] < liq and bar.high >= r["stop"]:
            reason, exit_price = "stop", r["stop"] * (1 + SLIPPAGE)
        elif bar.high >= liq:
            reason, exit_price = "approx_liquidation", liq
        elif bar.high >= r["stop"]:
            reason, exit_price = "stop", r["stop"] * (1 + SLIPPAGE)
        elif bar.low <= r["target"]:
            reason, exit_price = "target", r["target"] * (1 + SLIPPAGE)
        elif t + ONE_MIN >= expiry:
            reason, exit_price = "timeout", float(bar.close) * (1 + SLIPPAGE)
        last_mark = float(bar.close)
        if reason != "open":
            exit_time = t
            break
    last_t = exit_time + ONE_MIN if exit_time is not None else min(now_ms, end + ONE_MIN)
    funding_usdt = NOTIONAL * sum(rate for ft, rate in rates if ft < last_t)
    if reason != "open":
        pnl = qty * (r["entry_price"] - exit_price)
        pnl -= FEE_RATE * qty * (r["entry_price"] + exit_price)
        pnl += funding_usdt
        if reason == "approx_liquidation" or pnl < -MARGIN:
            pnl = max(-MARGIN, pnl)
            if reason != "approx_liquidation":
                reason = "margin_exhausted_proxy"
    else:
        pnl = qty * (r["entry_price"] - last_mark)
        pnl -= FEE_RATE * qty * (r["entry_price"] + last_mark)
        pnl += funding_usdt
    out.append({"symbol": symbol, "variant": r["variant"], "pattern_id": r["pattern_id"],
                "entry_time": entry, "entry_time_trt": r["entry_time_trt"],
                "exit_time": exit_time, "exit_time_trt": datetime.fromtimestamp(exit_time/1000, ZoneInfo("Europe/Istanbul")).isoformat() if exit_time else None,
                "reason": reason, "entry_price": r["entry_price"], "exit_price": exit_price,
                "funding_usdt": funding_usdt, "pnl_usdt": pnl,
                "minute_count": len(minutes), "score": r["score"]})

# Apply the same isolated-margin admission rule and score priority as live.
book = []
for r in sorted(out, key=lambda x: (x["entry_time"], -x["score"])):
    realized_before = sum(x["pnl_usdt"] for x in book
                          if x["exit_time"] is not None and x["exit_time"] < r["entry_time"])
    active_before = sum(x["entry_time"] <= r["entry_time"] and
                        (x["exit_time"] is None or x["exit_time"] >= r["entry_time"])
                        for x in book)
    r["admitted"] = bool(INITIAL_EQUITY + realized_before - active_before * MARGIN >= MARGIN)
    if r["admitted"]:
        book.append(r)

print(json.dumps({"as_of": data["end_utc"], "selected_variants": len(out),
                  "admitted_variants": len(book), "closed": sum(x["reason"] != "open" for x in book),
                  "net_realized_usdt": sum(x["pnl_usdt"] for x in book if x["reason"] != "open"),
                  "open_unrealized_usdt": sum(x["pnl_usdt"] for x in book if x["reason"] == "open"),
                  "trades": out}, ensure_ascii=False, indent=2))

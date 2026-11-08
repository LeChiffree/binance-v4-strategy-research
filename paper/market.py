"""Public, read-only Binance market data client."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from config import FUTURES

_local = threading.local()
_lock = threading.Lock()
_next_request = 0.0


def _get(base: str, path: str, params: dict | None = None):
    global _next_request
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
    for attempt in range(5):
        with _lock:
            wait = max(0.0, _next_request - time.monotonic())
            _next_request = max(_next_request, time.monotonic()) + .09
        if wait:
            time.sleep(wait)
        try:
            response = _local.session.get(base + path, params=params, timeout=18)
            if response.status_code in (418, 429, 500, 502, 503, 504):
                time.sleep(min(30, 2 ** attempt + 1))
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            if attempt == 4:
                raise
            time.sleep(min(12, 2 ** attempt))
    raise RuntimeError(f"market request failed: {path}")


def server_time() -> int:
    return int(_get(FUTURES, "/time")["serverTime"])


def bars(symbol: str, timeframe: str, limit: int = 1000) -> pd.DataFrame:
    rows = _get(FUTURES, "/klines", {"symbol": symbol, "interval": timeframe, "limit": limit})
    if not rows:
        raise ValueError(f"no klines: {symbol}/{timeframe}")
    result = pd.DataFrame([{
        "open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]),
        "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]),
        "close_time": int(r[6]), "quote_volume": float(r[7]),
        "trade_count": int(r[8]), "taker_buy_quote": float(r[10]),
    } for r in rows])
    return result.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)


def recent_minutes(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    output = []
    cursor = int(start_ms)
    while cursor <= end_ms:
        rows = _get(FUTURES, "/markPriceKlines", {"symbol": symbol, "interval": "1m",
                                      "startTime": cursor, "endTime": end_ms,
                                      "limit": 1000})
        if not rows:
            break
        output.extend((int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows)
        new_cursor = int(rows[-1][0]) + 60_000
        if new_cursor <= cursor:
            raise RuntimeError("minute pagination stalled")
        cursor = new_cursor
        if len(rows) < 1000:
            break
    return pd.DataFrame(output, columns=["open_time", "open", "high", "low", "close"])


def funding(symbol: str, entry_ms: int, exit_ms: int):
    if exit_ms <= entry_ms:
        return []
    rows = _get(FUTURES, "/fundingRate", {"symbol": symbol,
                                         "startTime": int(entry_ms),
                                         "endTime": int(exit_ms - 1), "limit": 1000})
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in rows]


def funding_recent(symbol: str, before_ms: int):
    rows = _get(FUTURES, "/fundingRate", {"symbol": symbol,
                                         "endTime": int(before_ms - 1), "limit": 8})
    return [(int(x["fundingTime"]), float(x["fundingRate"])) for x in rows
            if int(x["fundingTime"]) < before_ms]


def batch_bars(symbols: list[str], timeframe: str, workers: int = 8):
    output, errors = {}, {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {pool.submit(bars, symbol, timeframe): symbol for symbol in symbols}
        for future in as_completed(jobs):
            symbol = jobs[future]
            try:
                output[symbol] = future.result()
            except Exception as exc:
                errors[symbol] = str(exc)
    return output, errors

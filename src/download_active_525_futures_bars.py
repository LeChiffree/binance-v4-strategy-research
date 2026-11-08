"""Resumable hourly and 4-hour USD-M futures bars for the active USDT perpetual universe."""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/active_525/futures_bars"
OUT.mkdir(parents=True, exist_ok=True)
SOURCE = json.loads((ROOT / "output/binance_futures_exchange_info_2026-10-18.json").read_text(encoding="utf-8"))
CONTRACTS = sorted((x for x in SOURCE["symbols"] if x.get("status") == "TRADING"
                    and x.get("contractType") == "PERPETUAL" and x.get("quoteAsset") == "USDT"
                    and x.get("marginAsset") == "USDT"), key=lambda x: x["symbol"])
assert len(CONTRACTS) == 525
DURATIONS = {"1h": 3_600_000, "4h": 14_400_000}
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote"]
LOCK = threading.Lock()
NEXT_REQUEST = 0.0
TLS = threading.local()


def request(symbol: str, tf: str, start: int, end: int):
    global NEXT_REQUEST
    if not hasattr(TLS, "session"):
        TLS.session = requests.Session()
    params = {"symbol": symbol, "interval": tf, "startTime": int(start),
              "endTime": int(end)-1, "limit": 1500}
    for attempt in range(8):
        with LOCK:
            pause = max(0, NEXT_REQUEST-time.monotonic())
            NEXT_REQUEST = max(NEXT_REQUEST, time.monotonic())+.12
        if pause:
            time.sleep(pause)
        try:
            response = TLS.session.get("https://fapi.binance.com/fapi/v1/klines",
                                       params=params, timeout=40)
            if response.status_code in (418, 429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()
        except Exception:
            if attempt == 7:
                raise
            time.sleep(min(40, 2**attempt))


def fetch(contract: dict, tf: str, cutoff: int) -> dict:
    symbol = contract["symbol"]
    folder = OUT / tf
    folder.mkdir(exist_ok=True)
    path = folder / f"{symbol}.parquet"
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLS)
    duration = DURATIONS[tf]
    start = int(contract["onboardDate"] // duration * duration)
    cursor = max(start, int(old.open_time.max())+duration) if len(old) else start
    chunks = [old]
    pages = 0
    while cursor+duration <= cutoff:
        raw = request(symbol, tf, cursor, cutoff)
        if not raw:
            break
        frame = pd.DataFrame([[r[0], r[1], r[2], r[3], r[4], r[5], r[6],
                               r[7], r[8], r[9], r[10]] for r in raw], columns=COLS)
        for col in COLS:
            frame[col] = pd.to_numeric(frame[col])
        frame = frame[frame.close_time < cutoff]
        if frame.empty:
            break
        chunks.append(frame)
        cursor = int(frame.open_time.iloc[-1])+duration
        pages += 1
        if pages % 20 == 0:
            merged = pd.concat(chunks, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
            merged.to_parquet(path, index=False)
            chunks = [merged]
        if len(raw) < 1500:
            break
    merged = pd.concat(chunks, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    merged.to_parquet(path, index=False)
    times = merged.open_time.to_numpy(dtype=np.int64)
    gaps = int(np.count_nonzero(np.diff(times) != duration)) if len(times) else 0
    return {"symbol": symbol, "timeframe": tf, "bars": len(merged), "pages": pages,
            "gaps": gaps, "start_utc": pd.to_datetime(times[0], unit="ms", utc=True).isoformat() if len(times) else "",
            "end_utc": pd.to_datetime(times[-1], unit="ms", utc=True).isoformat() if len(times) else "",
            "status": "complete" if len(merged) else "empty"}


if __name__ == "__main__":
    clock = requests.get("https://fapi.binance.com/fapi/v1/time", timeout=20).json()["serverTime"]
    cutoff = int(clock // 3_600_000 * 3_600_000)
    (OUT.parent / "cutoff.json").write_text(json.dumps({"server_time_ms": clock,
        "closed_bar_cutoff_ms": cutoff}, indent=2), encoding="utf-8")
    jobs = [(c, tf) for c in CONTRACTS for tf in DURATIONS]
    audit_path = OUT.parent / "download_audit.csv"
    existing = pd.read_csv(audit_path).to_dict("records") if audit_path.exists() else []
    completed = {(r["symbol"], r["timeframe"]) for r in existing if r["status"] == "complete"}
    pending = [(c, tf) for c, tf in jobs if (c["symbol"], tf) not in completed]
    print(f"futures bars: {len(jobs)} tasks, {len(pending)} pending, cutoff {cutoff}", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch, c, tf, cutoff): (c["symbol"], tf) for c, tf in pending}
        for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                row = future.result()
            except Exception as exc:
                symbol, tf = futures[future]
                row = {"symbol": symbol, "timeframe": tf, "status": "error", "error": str(exc)[:250]}
            existing = [r for r in existing if (r["symbol"], r["timeframe"]) != (row["symbol"], row["timeframe"])]
            existing.append(row)
            if n % 10 == 0 or n == len(pending):
                pd.DataFrame(existing).sort_values(["symbol", "timeframe"]).to_csv(audit_path, index=False)
                print(f"downloaded {n}/{len(pending)}; latest {row['symbol']} {row['timeframe']} {row['status']}", flush=True)

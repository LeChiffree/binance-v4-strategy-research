"""Resumable historical USD-M funding for all active USDT perpetuals on D."""
from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time
from pathlib import Path

import pandas as pd
import requests

import download_d_futures_1m as archive

ROOT = Path(__file__).resolve().parents[1]
D = Path(os.environ.get("BINANCE_DATA_ROOT", ROOT / "data/binance")) / "active_525/funding"
CUTOFF = json.loads((ROOT / "data/active_525/cutoff.json").read_text())["closed_bar_cutoff_ms"]
TLS = threading.local()
LOCK = threading.Lock()
NEXT = 0.0


def load(symbol: str) -> pd.DataFrame | None:
    path = D / f"{symbol}.parquet"
    return pd.read_parquet(path) if path.exists() else None


def request(symbol: str, start: int, end: int):
    global NEXT
    if not hasattr(TLS, "session"):
        TLS.session = requests.Session()
    for attempt in range(8):
        with LOCK:
            pause = max(0, NEXT-time.monotonic())
            NEXT = max(NEXT, time.monotonic())+.20
        if pause:
            time.sleep(pause)
        try:
            r = TLS.session.get("https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": symbol, "startTime": int(start), "endTime": int(end), "limit": 1000}, timeout=45)
            if r.status_code in (418, 429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == 7:
                raise
            time.sleep(min(40, 2**attempt))


def fetch(contract: dict) -> dict:
    symbol = contract["symbol"]
    path = D / f"{symbol}.parquet"
    old = load(symbol)
    start = int(contract["onboardDate"])
    if old is not None and len(old):
        first = int(old.fundingTime.min())
        last = int(old.fundingTime.max())
        if first <= start+8*3_600_000 and last >= CUTOFF-24*3_600_000:
            return {"symbol": symbol, "status": "cached", "rows": len(old)}
    rows = []
    cursor = start
    while cursor < CUTOFF:
        raw = request(symbol, cursor, CUTOFF)
        if not raw:
            break
        rows.extend({"fundingTime": int(x["fundingTime"]),
                     "fundingRate": float(x["fundingRate"])} for x in raw)
        cursor = int(raw[-1]["fundingTime"])+1
        if len(raw) < 1000:
            break
    frame = pd.DataFrame(rows, columns=["fundingTime", "fundingRate"])
    if old is not None:
        frame = pd.concat([old, frame], ignore_index=True)
    frame = frame.drop_duplicates("fundingTime").sort_values("fundingTime")
    if frame.empty:
        return {"symbol": symbol, "status": "empty", "rows": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temp, index=False)
    temp.replace(path)
    return {"symbol": symbol, "status": "complete", "rows": len(frame),
            "first_time": int(frame.fundingTime.min()), "last_time": int(frame.fundingTime.max())}


def main() -> None:
    audit = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(fetch, c): c["symbol"] for c in archive.CONTRACTS}
        for n, future in enumerate(concurrent.futures.as_completed(jobs), 1):
            try:
                row = future.result()
            except Exception as exc:
                row = {"symbol": jobs[future], "status": "error", "error": str(exc)[:300]}
            audit.append(row)
            if n % 20 == 0 or n == len(jobs):
                pd.DataFrame(audit).to_csv(ROOT / "output/active_525_five_stage/d_funding_audit.csv", index=False)
                print(f"funding {n}/525; latest {row['symbol']} {row['status']}", flush=True)


if __name__ == "__main__":
    main()

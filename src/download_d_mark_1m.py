"""Download historical USD-M mark 1m candles only for signal holding months."""
from __future__ import annotations

import concurrent.futures
import io
import json
import os
import time
import zipfile
from pathlib import Path

import pandas as pd

import download_d_futures_1m as archive

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output/active_525_five_stage"
D = Path("D:/1mDataBinance")


def jobs_for_signals(signals: pd.DataFrame, cutoff: int) -> list[tuple[str, str, str]]:
    now = pd.Timestamp(cutoff, unit="ms", tz="UTC")
    needed = set()
    for r in signals.itertuples(index=False):
        a = pd.Timestamp(int(r.entry_time), unit="ms", tz="UTC")
        b = pd.Timestamp(min(int(r.expiry_time), cutoff)-60_000, unit="ms", tz="UTC")
        for month in pd.period_range(a.tz_localize(None).to_period("M"),
                                     b.tz_localize(None).to_period("M"), freq="M"):
            if month < now.tz_localize(None).to_period("M"):
                needed.add((r.symbol, "monthly", str(month)))
            else:
                for day in pd.date_range(max(a, pd.Timestamp(str(month), tz="UTC")).floor("D"),
                                         b.floor("D"), freq="D"):
                    if day < now.floor("D"):
                        needed.add((r.symbol, "daily", day.strftime("%Y-%m-%d")))
    existing = {}
    pending = []
    for job in sorted(needed):
        symbol, _, stamp = job
        if symbol not in existing:
            folder = D / "mark_1m" / symbol
            existing[symbol] = {p.stem for p in folder.glob("*.parquet")} if folder.exists() else set()
        if f"{symbol}-1m-{stamp}" not in existing[symbol]:
            pending.append(job)
    return pending


def fetch(job: tuple[str, str, str]) -> dict:
    symbol, mode, stamp = job
    url = (f"https://data.binance.vision/data/futures/um/{mode}/markPriceKlines/"
           f"{symbol}/1m/{symbol}-1m-{stamp}.zip")
    content = archive.request_archive(url)
    if content is None:
        return {"symbol": symbol, "mode": mode, "stamp": stamp, "status": "not_found"}
    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        if bundle.testzip() is not None:
            raise ValueError(f"corrupt ZIP {url}")
        name = next(n for n in bundle.namelist() if n.lower().endswith(".csv"))
        frame = pd.read_csv(bundle.open(name), header=0)
    frame = frame.iloc[:, :11]
    frame.columns = archive.CANON
    frame.open_time = pd.to_numeric(frame.open_time).astype("int64")
    frame.close_time = pd.to_numeric(frame.close_time).astype("int64")
    if frame.open_time.duplicated().any() or not frame.open_time.is_monotonic_increasing:
        raise ValueError(f"bad timestamps {url}")
    if not (frame.close_time.to_numpy() == frame.open_time.to_numpy()+59_999).all():
        raise ValueError(f"bad close times {url}")
    path = D / "mark_1m" / symbol / f"{symbol}-1m-{stamp}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".parquet.{os.getpid()}.tmp")
    frame.to_parquet(temp, index=False, compression="zstd")
    for attempt in range(10):
        try:
            temp.replace(path)
            break
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.25*(attempt+1))
    return {"symbol": symbol, "mode": mode, "stamp": stamp, "status": "complete", "rows": len(frame)}


def main() -> None:
    signals = pd.read_parquet(OUT / "raw_signals.parquet")
    cutoff = json.loads((ROOT / "data/active_525/cutoff.json").read_text())["closed_bar_cutoff_ms"]
    jobs = jobs_for_signals(signals, cutoff)
    print("mark 1m archives needed", len(jobs), flush=True)
    audit_path = OUT / "d_mark_1m_download_audit.csv"
    audit = pd.read_csv(audit_path).to_dict("records") if audit_path.exists() else []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        tasks = {pool.submit(fetch, job): job for job in jobs}
        for n, task in enumerate(concurrent.futures.as_completed(tasks), 1):
            try:
                row = task.result()
            except Exception as exc:
                symbol, mode, stamp = tasks[task]
                row = {"symbol": symbol, "mode": mode, "stamp": stamp,
                       "status": "error", "error": str(exc)[:300]}
            audit.append(row)
            if n % 100 == 0 or n == len(jobs):
                pd.DataFrame(audit).to_csv(audit_path, index=False)
                print(f"mark 1m {n}/{len(jobs)}; latest {ascii(row['symbol'])} {row['stamp']} {row['status']}", flush=True)


if __name__ == "__main__":
    main()

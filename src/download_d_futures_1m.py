"""Resumable Binance USD-M 1-minute archive into D:\\1mDataBinance.

Historical monthly files come from Binance's public Futures archive; the
current incomplete month comes from daily archives. Existing canonical CSV
files are retained. New files use Parquet with the same OHLCV field names.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
D = Path("D:/1mDataBinance")
SOURCE = json.loads((ROOT / "output/binance_futures_exchange_info_2026-10-18.json").read_text(encoding="utf-8"))
CONTRACTS = sorted((x for x in SOURCE["symbols"] if x.get("status") == "TRADING"
                    and x.get("contractType") == "PERPETUAL" and x.get("quoteAsset") == "USDT"
                    and x.get("marginAsset") == "USDT"), key=lambda x: x["symbol"])
assert len(CONTRACTS) == 525
CANON = ["open_time", "open", "high", "low", "close", "volume", "close_time",
         "quote_asset_volume", "number_of_trades", "taker_buy_base_asset_volume",
         "taker_buy_quote_asset_volume"]
TLS = threading.local()
LOCK = threading.Lock()
NEXT = 0.0


def request_archive(url: str) -> bytes | None:
    global NEXT
    if not hasattr(TLS, "session"):
        TLS.session = requests.Session()
    for attempt in range(7):
        with LOCK:
            pause = max(0, NEXT-time.monotonic())
            NEXT = max(NEXT, time.monotonic())+.07
        if pause:
            time.sleep(pause)
        try:
            r = TLS.session.get(url, timeout=75)
            if r.status_code == 404:
                return None
            if r.status_code in (418, 429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r.content
        except Exception:
            if attempt == 6:
                raise
            time.sleep(min(50, 2**attempt))
    return None


def tasks(cutoff_ms: int) -> list[tuple[str, str, str]]:
    now = pd.Timestamp(cutoff_ms, unit="ms", tz="UTC")
    jobs = []
    for contract in CONTRACTS:
        symbol = contract["symbol"]
        start = pd.Timestamp(int(contract["onboardDate"]), unit="ms", tz="UTC")
        folder = D / "1m" / symbol
        present = {p.stem for p in folder.iterdir() if p.suffix in (".csv", ".parquet")} if folder.exists() else set()
        last_complete_month = now.replace(day=1).floor("D")-pd.Timedelta(days=1)
        for month in pd.period_range(start.tz_localize(None).to_period("M"),
                                     last_complete_month.tz_localize(None).to_period("M"), freq="M"):
            stamp = str(month)
            if f"{symbol}-1m-{stamp}" in present:
                continue
            jobs.append((symbol, "monthly", stamp))
        first_day = max(start.floor("D"), now.replace(day=1).floor("D"))
        for day in pd.date_range(first_day, now.floor("D")-pd.Timedelta(days=1), freq="D"):
            stamp = day.strftime("%Y-%m-%d")
            if f"{symbol}-1m-{stamp}" in present:
                continue
            jobs.append((symbol, "daily", stamp))
    return jobs


def fetch(job: tuple[str, str, str]) -> dict:
    symbol, mode, stamp = job
    url = (f"https://data.binance.vision/data/futures/um/{mode}/klines/"
           f"{symbol}/1m/{symbol}-1m-{stamp}.zip")
    content = request_archive(url)
    if content is None:
        return {"symbol": symbol, "mode": mode, "stamp": stamp, "status": "not_found"}
    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        if bundle.testzip() is not None:
            raise ValueError(f"corrupt zip {url}")
        names = [n for n in bundle.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"unexpected archive members {names}")
        frame = pd.read_csv(bundle.open(names[0]), header=0)
    if len(frame.columns) not in (11, 12):
        raise ValueError(f"invalid column count {len(frame.columns)} for {url}")
    frame = frame.iloc[:, :11]
    frame.columns = CANON
    frame["open_time"] = pd.to_numeric(frame.open_time, errors="raise").astype("int64")
    frame["close_time"] = pd.to_numeric(frame.close_time, errors="raise").astype("int64")
    if not frame.open_time.is_monotonic_increasing or frame.open_time.duplicated().any():
        raise ValueError(f"non-monotonic timestamps {url}")
    if not (frame.close_time.to_numpy() == frame.open_time.to_numpy()+59_999).all():
        raise ValueError(f"bad 1m close time {url}")
    start = pd.Timestamp(stamp, tz="UTC")
    end = (start+pd.offsets.MonthBegin(1)) if mode == "monthly" else start+pd.Timedelta(days=1)
    if len(frame) and (frame.open_time.iloc[0] < start.value//10**6 or
                       frame.open_time.iloc[-1] >= end.value//10**6):
        raise ValueError(f"out-of-range timestamps {url}")
    path = D / "1m" / symbol / f"{symbol}-1m-{stamp}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temp, index=False, compression="zstd")
    temp.replace(path)
    return {"symbol": symbol, "mode": mode, "stamp": stamp,
            "status": "complete", "rows": len(frame), "bytes": path.stat().st_size}


def main() -> None:
    cutoff = json.loads((ROOT / "data/active_525/cutoff.json").read_text())["closed_bar_cutoff_ms"]
    pending = tasks(cutoff)
    print(f"D archive: {len(pending)} missing 1m months/days across 525 symbols", flush=True)
    audit = ROOT / "output/active_525_five_stage/d_1m_download_audit.csv"
    audit.parent.mkdir(parents=True, exist_ok=True)
    records = pd.read_csv(audit).to_dict("records") if audit.exists() else []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fetch, job): job for job in pending}
        for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                row = future.result()
            except Exception as exc:
                symbol, mode, stamp = futures[future]
                row = {"symbol": symbol, "mode": mode, "stamp": stamp,
                       "status": "error", "error": str(exc)[:300]}
            records.append(row)
            if n % 100 == 0 or n == len(pending):
                pd.DataFrame(records).to_csv(audit, index=False)
                print(f"D 1m {n}/{len(pending)}; latest {ascii(row['symbol'])} {row['stamp']} {row['status']}", flush=True)


if __name__ == "__main__":
    main()

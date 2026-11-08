"""Make D-drive 1h/4h Futures OHLCV from the canonical 1m source.

The last complete UTC day is the frozen cutoff; groups with a missing minute
are excluded and downstream formation scanning resets across any gaps.
"""
from __future__ import annotations

import concurrent.futures
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import download_d_futures_1m as archive

ROOT = Path(__file__).resolve().parent
D = Path("D:/1mDataBinance")
OUT = D / "active_525/futures_bars"
AUDIT_PATH = ROOT / "output/active_525_five_stage/d_derived_bars_audit.csv"
SOURCE_AUDIT = ROOT / "output/active_525_five_stage/d_1m_download_audit.csv"
PRIOR_COMPLETE = (set(pd.read_csv(AUDIT_PATH).query("status == 'complete'").symbol)
                  if AUDIT_PATH.exists() else set())
UNAVAILABLE = (set(map(tuple, pd.read_csv(SOURCE_AUDIT).query("status == 'not_found'")
                         [["symbol", "stamp"]].astype(str).to_numpy()))
               if SOURCE_AUDIT.exists() else set())
CUTOFF = pd.Timestamp("2026-10-18 00:00:00", tz="UTC").value // 10**6
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote"]


def monthly_files(symbol: str) -> list[Path]:
    folder = D / "1m" / symbol
    if not folder.exists():
        return []
    # Monthly and current-month daily files; the filename date sorts in UTC.
    return sorted([p for p in folder.iterdir() if p.suffix in (".parquet", ".csv")], key=lambda p: p.stem)


def read_file(path: Path) -> pd.DataFrame:
    columns = archive.CANON
    if path.suffix == ".parquet":
        return pd.read_parquet(path, columns=columns)
    return pd.read_csv(path, usecols=columns)


def aggregate(frame: pd.DataFrame, duration: int) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=COLS)
    frame = frame.copy()
    frame["bar_time"] = frame.open_time.to_numpy(dtype=np.int64)//duration*duration
    group = frame.groupby("bar_time", sort=True)
    out = group.agg(open=("open", "first"), high=("high", "max"),
                    low=("low", "min"), close=("close", "last"),
                    volume=("volume", "sum"), quote_volume=("quote_asset_volume", "sum"),
                    trade_count=("number_of_trades", "sum"),
                    taker_buy_base=("taker_buy_base_asset_volume", "sum"),
                    taker_buy_quote=("taker_buy_quote_asset_volume", "sum"),
                    first_minute=("open_time", "first"), last_minute=("open_time", "last"),
                    minute_count=("open_time", "size")).reset_index()
    complete = (out.minute_count.eq(duration//60_000) &
                out.first_minute.eq(out.bar_time) &
                out.last_minute.eq(out.bar_time+duration-60_000))
    out = out.loc[complete].rename(columns={"bar_time": "open_time"})
    out["close_time"] = out.open_time+duration-1
    return out[COLS]


def derive(symbol: str) -> dict:
    if symbol in PRIOR_COMPLETE and all((OUT / tf / f"{symbol}.parquet").exists() for tf in ("1h", "4h")):
        return {"symbol": symbol, "status": "complete", "cached": True}
    paths = monthly_files(symbol)
    if not paths:
        return {"symbol": symbol, "status": "missing_1m_files"}
    # The 1m download adds files in symbol order; derive only complete symbols.
    first = pd.Timestamp(int(next(x["onboardDate"] for x in archive.CONTRACTS
                                  if x["symbol"] == symbol)), unit="ms", tz="UTC")
    last_day = pd.Timestamp(CUTOFF-60_000, unit="ms", tz="UTC")
    last_month = last_day.tz_localize(None).to_period("M")
    expected_months = pd.period_range(first.tz_localize(None).to_period("M"), last_month, freq="M")
    found_months = {pd.Period(p.stem[-7:], freq="M") for p in paths if len(p.stem) >= 7 and
                    p.stem[-7:-3].isdigit() and p.stem[-3] == "-" and p.stem[-2:].isdigit()}
    first_found = min(found_months) if found_months else None
    leading_unavailable = [str(m) for m in expected_months[:-1] if first_found is not None and m < first_found]
    unavailable_months = [str(m) for m in expected_months[:-1]
                          if m not in found_months and (symbol, str(m)) in UNAVAILABLE]
    missing_months = [str(m) for m in expected_months[:-1]
                      if m not in found_months and (first_found is None or m >= first_found)
                      and (symbol, str(m)) not in UNAVAILABLE]
    first_day = max(first.floor("D"), pd.Timestamp(str(last_month), tz="UTC"))
    expected_days = pd.date_range(first_day, last_day.floor("D"), freq="D")
    found_days = {p.stem[-10:] for p in paths if len(p.stem) >= 10 and p.stem[-10:-6].isdigit()
                  and p.stem[-6] == "-" and p.stem[-5:-3].isdigit() and p.stem[-3] == "-"
                  and p.stem[-2:].isdigit()}
    unavailable_days = [d for d in expected_days if d.strftime("%Y-%m-%d") not in found_days
                        and (symbol, d.strftime("%Y-%m-%d")) in UNAVAILABLE]
    missing_days = [d for d in expected_days if d.strftime("%Y-%m-%d") not in found_days
                    and (symbol, d.strftime("%Y-%m-%d")) not in UNAVAILABLE]
    if missing_months or missing_days:
        return {"symbol": symbol, "status": "waiting_for_1m", "missing_months": len(missing_months),
                "missing_days": len(missing_days)}
    parts = {"1h": [], "4h": []}
    minute_rows = 0
    for path in paths:
        frame = read_file(path)
        frame = frame[frame.open_time.lt(CUTOFF)]
        minute_rows += len(frame)
        if frame.empty:
            continue
        for tf, duration in (("1h", 3_600_000), ("4h", 14_400_000)):
            parts[tf].append(aggregate(frame, duration))
    summary = {"symbol": symbol, "status": "complete", "minute_rows": minute_rows,
               "leading_unavailable_months": len(leading_unavailable),
               "archive_unavailable_months": len(unavailable_months),
               "archive_unavailable_days": len(unavailable_days)}
    for tf, duration in (("1h", 3_600_000), ("4h", 14_400_000)):
        bars = pd.concat(parts[tf], ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
        # Each day and month ends at UTC midnight, so 1h/4h groups never
        # cross a file boundary. Missing bars remain visible as gaps.
        path = OUT / tf / f"{symbol}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".parquet.tmp")
        bars.to_parquet(temp, index=False)
        for attempt in range(10):
            try:
                temp.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.25 * (attempt + 1))
        t = bars.open_time.to_numpy(dtype=np.int64)
        summary[f"{tf}_bars"] = len(bars)
        summary[f"{tf}_gaps"] = int(np.count_nonzero(np.diff(t) != duration)) if len(t) else 0
        summary[f"{tf}_first"] = int(t[0]) if len(t) else None
        summary[f"{tf}_last"] = int(t[-1]) if len(t) else None
    return summary


def main() -> None:
    audit = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(derive, c["symbol"]): c["symbol"] for c in archive.CONTRACTS}
        for n, future in enumerate(concurrent.futures.as_completed(jobs), 1):
            try:
                row = future.result()
            except Exception as exc:
                row = {"symbol": jobs[future], "status": "error", "error": str(exc)[:300]}
            audit.append(row)
            if n % 20 == 0 or n == len(jobs):
                pd.DataFrame(audit).to_csv(AUDIT_PATH, index=False)
                print(f"derive {n}/525; latest {ascii(row['symbol'])} {row['status']}", flush=True)
    (ROOT / "data/active_525/cutoff.json").write_text(json.dumps({
        "closed_bar_cutoff_ms": CUTOFF,
        "source": "D:/1mDataBinance/1m monthly and daily USD-M Futures archives",
        "rule": "last complete UTC day"}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

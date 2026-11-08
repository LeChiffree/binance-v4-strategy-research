"""Read the D-drive USD-M trade and mark 1m archives by UTC interval."""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

import numpy as np
import pandas as pd

D = Path(os.environ.get("BINANCE_DATA_ROOT", Path(__file__).resolve().parents[1] / "data/binance"))
FIELDS = ["open_time", "open", "high", "low", "close", "close_time"]


@lru_cache(maxsize=320)
def _file(path: str) -> pd.DataFrame:
    file = Path(path)
    if file.suffix == ".parquet":
        return pd.read_parquet(file, columns=FIELDS)
    return pd.read_csv(file, usecols=FIELDS)


def load(symbol: str, kind: str, start: int, end: int) -> pd.DataFrame:
    """Return [start,end) at 1m resolution; fail on absent or gapped minutes."""
    if kind not in ("1m", "mark_1m"):
        raise ValueError(kind)
    if start % 60_000 or end % 60_000 or end <= start:
        raise ValueError((start, end))
    first = pd.Timestamp(start, unit="ms", tz="UTC")
    last = pd.Timestamp(end-60_000, unit="ms", tz="UTC")
    parts = []
    root = D / kind / symbol
    def add_file(path: Path) -> None:
        frame = _file(str(path))
        times = frame.open_time.to_numpy(dtype=np.int64)
        left = int(np.searchsorted(times, start, side="left"))
        right = int(np.searchsorted(times, end, side="left"))
        if right > left:
            parts.append(frame.iloc[left:right])
    for month in pd.period_range(first.tz_localize(None).to_period("M"),
                                 last.tz_localize(None).to_period("M"), freq="M"):
        stem = f"{symbol}-1m-{month}"
        monthly = next((p for ext in (".parquet", ".csv")
                        if (p := root / (stem+ext)).exists()), None)
        if monthly is not None:
            add_file(monthly)
            continue
        month_start = max(first, pd.Timestamp(str(month), tz="UTC"))
        month_end = min(last, pd.Timestamp(str(month+1), tz="UTC")-pd.Timedelta(minutes=1))
        for day in pd.date_range(month_start.floor("D"), month_end.floor("D"), freq="D"):
            stem = f"{symbol}-1m-{day.strftime('%Y-%m-%d')}"
            daily = next((p for ext in (".parquet", ".csv")
                          if (p := root / (stem+ext)).exists()), None)
            if daily is None:
                raise FileNotFoundError(f"{kind}/{symbol}/{stem}")
            add_file(daily)
    if not parts:
        raise FileNotFoundError(f"{kind}/{symbol}: {first}..{last}")
    result = pd.concat(parts, ignore_index=True)
    result = result[(result.open_time >= start) & (result.open_time < end)]
    expected = np.arange(start, end, 60_000, dtype=np.int64)
    if not np.array_equal(result.open_time.to_numpy(dtype=np.int64), expected):
        raise ValueError(f"incomplete {kind}/{symbol}: expected {len(expected)}, got {len(result)}")
    return result.reset_index(drop=True)

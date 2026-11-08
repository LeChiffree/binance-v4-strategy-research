"""Exact 2x Futures minute labels for the new 525-coin V4 training set."""
from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pandas as pd

import backtest_active_525_five_stage as bt
import download_d_active_525_funding as funding_store

OUT = Path(__file__).resolve().parent / "output/v4_new_525_2021_2024"
PARTS = OUT / "labels_2x_parts"
PARTS.mkdir(parents=True, exist_ok=True)
TRAIN_START = pd.Timestamp("2021-01-01", tz="UTC").value // 10**6
TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC").value // 10**6
bt.LEVERAGES = (2,)


def process(symbol: str, group: pd.DataFrame) -> tuple[str, int]:
    path = PARTS / f"{symbol}.parquet"
    if path.exists():
        try:
            saved = pd.read_parquet(path)
            if len(saved) == len(group):
                return symbol, len(saved)
        except Exception:
            pass
    funding = funding_store.load(symbol)
    rows = []
    for signal in group.sort_values("entry_time").itertuples(index=False):
        rows.extend(bt.replay_signal(signal, funding))
    frame = pd.DataFrame(rows)
    temp = path.with_suffix(".parquet.tmp")
    frame.to_parquet(temp, index=False)
    temp.replace(path)
    return symbol, len(frame)


def main() -> None:
    signals = pd.read_parquet(bt.OUT / "raw_signals.parquet")
    signals = signals[signals.entry_time.ge(TRAIN_START) & signals.entry_time.lt(TRAIN_END)].copy()
    features = pd.read_parquet(bt.OUT / "stage_features.parquet")
    signals = signals.merge(features.drop(columns=["symbol", "variant", "timeframe", "entry_time"]),
                            on=["event_id", "trade_id"], validate="one_to_one")
    signals["stage2"] = signals.btc_return_200.le(bt.BTC_MAX) & signals.avg_trade_usdt.ge(bt.LIQUIDITY_MIN)
    signals["stage3"] = signals.stage2 & signals.v4_score_2021_2024.ge(bt.V4_MIN)
    signals["stage4"] = signals.stage3 & signals.coin_return_200.lt(0)
    signals["stage5"] = signals.stage4 & signals.body_range.lt(0) & signals.close_location.le(.4)
    groups = list(signals.groupby("symbol", sort=True))
    print("2x training replay", len(signals), "signals", len(groups), "coins", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        jobs = [pool.submit(process, symbol, group) for symbol, group in groups]
        for n, job in enumerate(concurrent.futures.as_completed(jobs), 1):
            symbol, count = job.result()
            if n % 10 == 0 or n == len(jobs):
                print("training labels", n, "/", len(jobs), symbol, count, flush=True)
    labels = pd.concat([pd.read_parquet(PARTS / f"{symbol}.parquet") for symbol, _ in groups], ignore_index=True)
    assert len(labels) == len(signals)
    labels.to_parquet(OUT / "training_2x_labels_all.parquet", index=False)
    usable = labels[labels.status.eq("resolved") & labels.exit_time.lt(TRAIN_END)].copy()
    usable.to_parquet(OUT / "training_2x_labels_usable.parquet", index=False)
    print("usable labels", len(usable), "coins", usable.symbol.nunique(), flush=True)


if __name__ == "__main__":
    main()

"""Replay the two frozen BTC/liquidity methods with minute-level decisive bars.

The original 5m replay proves that no stop/target was touched before its
decisive 5m bar. We download all five 1m bars in that bar, which identifies
the first minute of a touch without downloading millions of irrelevant bars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests

import v4_feature_build as feature_build

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output" / "precise_1m"
MINUTE = 60_000
FIVE = 300_000
API = "https://api.binance.com/api/v3/klines"
LOCK = threading.Lock()
NEXT_REQUEST = 0.0
THREAD = threading.local()


def population() -> pd.DataFrame:
    source = pd.read_parquet(ROOT / "output/ml/all_2x_labels.parquet")
    bundle = joblib.load(ROOT / "output/ml/filtre_v4_selected.joblib")
    frame = source.merge(feature_build.d, on=["event_id", "variant"],
                         suffixes=("", "_feature"), validate="one_to_one")
    x = frame[bundle["features"]].copy()
    for col in bundle["categorical"]:
        x[col] = x[col].astype(str).astype("category")
    score = bundle["model"].predict(x)
    gate = (frame.btc_return_200.le(bundle["btc_return_200_max"])
            & frame.avg_trade_usdt.ge(bundle["avg_trade_usdt_min"]))
    out = source.loc[gate.to_numpy()].copy()
    out["timeframe"] = frame.loc[gate.to_numpy(), "timeframe"].to_numpy()
    out["v4_selected"] = score[gate.to_numpy()] >= bundle["score_threshold"]
    out["v4_score"] = score[gate.to_numpy()]
    assert not out.duplicated(["event_id", "variant"]).any()
    # The original 5m ledger contains expiry and ambiguity diagnostics.
    spot = pd.read_csv(ROOT / "output/all_trades.csv", low_memory=False)
    spot = spot[spot.status.eq("closed")].copy()
    keys = [f"{r.symbol}|{r.pattern_id}|{r.entry_time}|{r.known_time}|{r.trigger:.12g}|{r.raw_stop:.12g}|{r.pivot_times}"
            for r in spot.itertuples(index=False)]
    spot["event_id"] = [hashlib.blake2s(k.encode(), digest_size=12).hexdigest() for k in keys]
    out = out.merge(spot[["event_id", "variant", "expiry_time", "exit_price", "ambiguous"]],
                    on=["event_id", "variant"], validate="one_to_one")
    out["decisive_5m"] = np.where(out.base_exit_reason.eq("timeout"),
                                  out.base_exit_time - FIVE, out.base_exit_time).astype("int64")
    out.to_parquet(OUT / "selected_population.parquet", index=False)
    return out


def fetch_one(symbol: str, minute: int):
    global NEXT_REQUEST
    session = getattr(THREAD, "session", None)
    if session is None:
        session = requests.Session()
        THREAD.session = session
    for attempt in range(7):
        with LOCK:
            wait = max(0.0, NEXT_REQUEST - time.monotonic())
            NEXT_REQUEST = max(NEXT_REQUEST, time.monotonic()) + .11
        if wait:
            time.sleep(wait)
        try:
            response = session.get(API, params={"symbol": symbol, "interval": "1m",
                                                 "startTime": minute, "endTime": minute + FIVE - 1,
                                                 "limit": 5}, timeout=35)
            if response.status_code in (418, 429, 500, 502, 503, 504):
                time.sleep(min(45, 2 ** attempt + 1))
                continue
            response.raise_for_status()
            raw = response.json()
            if len(raw) != 5 or [int(r[0]) for r in raw] != [minute + i * MINUTE for i in range(5)]:
                raise ValueError(f"incomplete 1m bar: {symbol} {minute} {len(raw)}")
            return [[symbol, minute, int(r[0]), float(r[1]), float(r[2]),
                     float(r[3]), float(r[4])] for r in raw]
        except (requests.RequestException, ValueError):
            if attempt == 6:
                raise
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(f"could not download {symbol} {minute}")


def download(pop: pd.DataFrame, workers: int):
    folder = OUT / "minute_cache"
    folder.mkdir(parents=True, exist_ok=True)
    targets = pop[["symbol", "decisive_5m"]].drop_duplicates()
    total = len(targets)
    failures = {}
    grouped = list(targets.groupby("symbol", sort=True))
    def work(item):
        symbol, group = item
        path = folder / f"{symbol}.csv.gz"
        cols = ["symbol", "decisive_5m", "open_time", "open", "high", "low", "close"]
        old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=cols)
        present = set(old.decisive_5m.astype("int64")) if len(old) else set()
        records = []
        errors = {}
        for minute in group.decisive_5m.astype("int64"):
            if minute in present:
                continue
            try:
                records.extend(fetch_one(symbol, int(minute)))
            except Exception as exc:
                errors[str(minute)] = str(exc)
        if records:
            fresh = pd.DataFrame(records, columns=cols)
            pd.concat([old, fresh], ignore_index=True).drop_duplicates("open_time").sort_values("open_time").to_csv(
                path, index=False, compression="gzip")
        return symbol, len(group), len(records) // 5, errors
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(work, item) for item in grouped]
        for n, future in enumerate(as_completed(jobs), 1):
            symbol, count, fetched, errors = future.result()
            completed += count
            failures.update({f"{symbol}:{k}": v for k, v in errors.items()})
            if n % 15 == 0 or n == len(jobs):
                print(f"minute windows {completed}/{total}; fetched {fetched} for {symbol}; errors {len(failures)}", flush=True)
    (OUT / "download_failures.json").write_text(json.dumps(failures, indent=2), encoding="utf-8")
    return failures


def replay_one(row, minute: pd.DataFrame, funding: pd.DataFrame | None):
    result = {"event_id": row.event_id, "symbol": row.symbol, "pattern_id": row.pattern_id,
              "timeframe": row.timeframe, "strategy": row.strategy, "variant": row.variant,
              "entry_time": int(row.entry_time), "decisive_5m": int(row.decisive_5m),
              "base_exit_time": int(row.base_exit_time), "base_exit_reason": row.base_exit_reason,
              "entry_price": float(row.entry_price), "stop": float(row.stop), "target": float(row.target),
              "v4_selected": bool(row.v4_selected), "v4_score": float(row.v4_score),
              "old_5m_ambiguous": bool(row.ambiguous), "methodology": "5m scan + decisive 5m window replayed with 1m"}
    if len(minute) != 5 or not np.array_equal(minute.open_time.to_numpy(dtype="int64"),
                                               np.arange(int(row.decisive_5m), int(row.decisive_5m) + FIVE, MINUTE)):
        return {**result, "status": "missing_1m"}
    reason = "timeout"
    exit_time = int(row.expiry_time)
    exit_price = float(minute.close.iloc[-1]) * 1.0002
    ambiguous = False
    for bar in minute.itertuples(index=False):
        if bar.open >= row.stop:
            reason, exit_time, exit_price = "stop_gap", int(bar.open_time), bar.open * 1.0002
            break
        stop_hit, target_hit = bar.high >= row.stop, bar.low <= row.target
        if stop_hit:
            reason, exit_time, exit_price = "stop", int(bar.open_time), row.stop * 1.0002
            ambiguous = bool(target_hit)
            break
        if target_hit:
            reason, exit_time, exit_price = "target", int(bar.open_time), row.target * 1.0002
            break
    if row.base_exit_reason != "timeout" and reason == "timeout":
        return {**result, "status": "five_minute_mismatch"}
    if funding is None:
        return {**result, "status": "funding_missing"}
    event = funding[(funding.fundingTime >= row.entry_time) & (funding.fundingTime < exit_time)]
    # Same 2x / $10 independent-margin convention as the frozen comparison.
    notional = 20.0
    quantity = notional / float(row.entry_price)
    fund = notional * float(event.fundingRate.sum())
    net = quantity * (float(row.entry_price) - exit_price) - .001 * quantity * (float(row.entry_price) + exit_price) + fund
    if net < -10:
        net, reason = -10.0, "margin_exhausted_proxy"
    return {**result, "status": "resolved", "exit_time": exit_time, "exit_price": exit_price,
            "exit_reason": reason, "same_1m_ambiguous": ambiguous, "funding_usdt": fund,
            "funding_events": len(event), "net_pnl_usdt": net, "roi_on_margin_pct": 10 * net,
            "duration_hours": (exit_time - int(row.entry_time)) / 3_600_000}


def replay(pop: pd.DataFrame):
    folder = OUT / "minute_cache"
    records = []
    for n, (symbol, group) in enumerate(pop.groupby("symbol", sort=True), 1):
        path = folder / f"{symbol}.csv.gz"
        minute = pd.read_csv(path) if path.exists() else pd.DataFrame()
        minute_by_start = {int(k): v.sort_values("open_time") for k, v in minute.groupby("decisive_5m")} if len(minute) else {}
        funding_path = ROOT / "data/futures/funding" / f"{symbol}.csv.gz"
        funding = pd.read_csv(funding_path) if funding_path.exists() else None
        for row in group.itertuples(index=False):
            records.append(replay_one(row, minute_by_start.get(int(row.decisive_5m), pd.DataFrame()), funding))
        if n % 30 == 0 or n == pop.symbol.nunique():
            print(f"replay {n}/{pop.symbol.nunique()} symbols, {len(records)} rows", flush=True)
    result = pd.DataFrame(records)
    result.to_parquet(OUT / "trades.parquet", index=False)
    result.to_csv(OUT / "trades.csv", index=False)
    return result


def summary(frame: pd.DataFrame):
    rows = []
    holdout_rows = []
    for name, group in (("BTC + likidite", frame), ("BTC + likidite + filtre_v4", frame[frame.v4_selected])):
        resolved = group[group.status.eq("resolved")]
        holdout = resolved[resolved.entry_time >= 1735689600000].sort_values(
            ["exit_time", "entry_time", "symbol", "strategy"])
        hpnl = holdout.net_pnl_usdt.to_numpy()
        heq = 1000 + np.cumsum(hpnl)
        hdd = heq - np.maximum.accumulate(np.r_[1000, heq])[1:]
        holdout_rows.append({"method": name, "entry_period": "2025+", "trades": len(holdout),
                             "coins": holdout.symbol.nunique(), "net_usdt": round(float(hpnl.sum()), 2),
                             "mean_usdt": round(float(hpnl.mean()), 4) if len(hpnl) else 0,
                             "win_pct": round(float((hpnl > 0).mean() * 100), 2) if len(hpnl) else 0,
                             "max_drawdown_usdt": round(float(hdd.min()), 2) if len(hdd) else 0})
        exit_year = pd.to_datetime(resolved.exit_time, unit="ms", utc=True).dt.year
        for year, sample in [("all", resolved)] + [(str(y), g) for y, g in resolved.groupby(exit_year)]:
            sample = sample.sort_values(["exit_time", "entry_time", "symbol", "strategy"])
            pnl = sample.net_pnl_usdt.to_numpy()
            equity = 1000 + np.cumsum(pnl)
            drawdown = equity - np.maximum.accumulate(np.r_[1000, equity])[1:]
            rows.append({"method": name, "exit_year": year, "trades": len(sample),
                         "coins": sample.symbol.nunique(), "net_usdt": round(float(pnl.sum()), 2),
                         "win_pct": round(float((pnl > 0).mean() * 100), 2) if len(pnl) else 0,
                         "max_drawdown_usdt": round(float(drawdown.min()), 2) if len(drawdown) else 0,
                         "unresolved": len(group) - len(resolved) if year == "all" else None})
    pd.DataFrame(rows).to_csv(OUT / "summary.csv", index=False)
    pd.DataFrame(holdout_rows).to_csv(OUT / "holdout_2025plus_summary.csv", index=False)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    pop = population()
    print(f"population {len(pop)} rows, {pop.symbol.nunique()} coins; V4 {int(pop.v4_selected.sum())}; distinct 1m windows {len(pop[['symbol','decisive_5m']].drop_duplicates())}", flush=True)
    if not args.skip_download:
        download(pop, args.workers)
    result = replay(pop)
    print(pd.DataFrame(summary(result)).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()

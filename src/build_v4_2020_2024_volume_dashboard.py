"""Rebuild the frozen 2020–2024 V4 leveraged dashboard from local replays."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/v4_2020_2024_leverage_volume"
OUT.mkdir(parents=True, exist_ok=True)
START = pd.Timestamp("2025-01-01", tz="UTC").value // 1_000_000
END = pd.Timestamp("2026-10-18", tz="UTC").value // 1_000_000
DAY_MS = 86_400_000
HOUR_MS = 3_600_000
BAR_DIR = Path(os.environ.get("BINANCE_DATA_ROOT", ROOT / "data/binance")) / "active_525/futures_bars/1h"


def volume_rank() -> pd.DataFrame:
    info = json.loads((ROOT / "output/binance_futures_exchange_info_2026-10-18.json").read_text(encoding="utf-8"))
    symbols = sorted(s["symbol"] for s in info["symbols"] if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL")
    assert len(symbols) == 525
    rows = []
    for symbol in symbols:
        path = BAR_DIR / f"{symbol}.parquet"
        if not path.exists():
            rows.append((symbol, 0.0, 0, None, None, "missing_bars"))
            continue
        d = pd.read_parquet(path, columns=["open_time", "quote_volume"])
        d = d.loc[(d.open_time >= START) & (d.open_time < END)].copy()
        if d.empty:
            rows.append((symbol, 0.0, 0, None, None, "no_2025_2026_bars"))
            continue
        d["day"] = d.open_time // DAY_MS
        days = d.groupby("day", sort=True).agg(volume=("quote_volume", "sum"), hours=("open_time", "nunique"), first=("open_time", "min"), last=("open_time", "max"))
        days = days[(days.hours == 24) & ((days["last"] - days["first"]) == 23 * HOUR_MS)]
        days = days[np.isfinite(days.volume) & (days.volume >= 0)]
        if len(days):
            rows.append((symbol, float(days.volume.mean()), len(days), str(pd.Timestamp(int(days.index.min()) * DAY_MS, unit="ms", tz="UTC").date()), str(pd.Timestamp(int(days.index.max()) * DAY_MS, unit="ms", tz="UTC").date()), "ok"))
        else:
            rows.append((symbol, 0.0, 0, None, None, "no_full_utc_day"))
    out = pd.DataFrame(rows, columns=["symbol", "avg_daily_quote_usdt", "full_days", "first_day", "last_day", "volume_status"])
    out = out.sort_values(["avg_daily_quote_usdt", "symbol"], ascending=[False, True]).reset_index(drop=True)
    out.insert(0, "volume_rank", np.arange(1, len(out)+1))
    out.to_csv(OUT / "volume_rank.csv", index=False, encoding="utf-8-sig")
    assert out.symbol.is_unique
    return out


def scored_trades(volume: pd.DataFrame) -> pd.DataFrame:
    root = ROOT / "output"
    old = pd.read_parquet(root / "active_525_five_stage/trades.parquet")
    old = old.loc[(old.entry_time >= START) & (old.entry_time < END)].copy()
    ten = pd.read_parquet(root / "v4_new_525_2021_2024/test_10x_all.parquet")
    fifteen = pd.read_parquet(root / "v4_window_sweep_525/replay_15x_all.parquet")
    fifteen = fifteen.loc[(fifteen.entry_time >= START) & (fifteen.entry_time < END)].copy()
    trades = pd.concat([old, ten, fifteen], ignore_index=True)
    expected = int(len(trades) / 6)
    assert len(trades) == 6*19317, len(trades)
    assert trades.groupby("leverage").size().to_dict() == {1: expected, 5: expected, 10: expected, 15: expected, 20: expected, 25: expected}
    assert not trades.duplicated(["trade_id", "leverage"]).any()

    score = pd.read_parquet(root / "v4_window_sweep_525/window_scores.parquet", columns=["trade_id", "2020-2024"]).rename(columns={"2020-2024": "v4_score"})
    feat = pd.read_parquet(root / "active_525_five_stage/stage_features.parquet", columns=["trade_id", "btc_return_200", "avg_trade_usdt", "coin_return_200", "body_range", "close_location"])
    trades = trades.drop(columns=[c for c in trades if c.startswith("stage") or c.startswith("v4_score")])
    trades = trades.merge(score, on="trade_id", how="left", validate="many_to_one").merge(feat, on="trade_id", how="left", validate="many_to_one")
    assert trades.v4_score.notna().all()
    assert trades.avg_trade_usdt.notna().all()
    trades = trades.merge(volume[["symbol", "volume_rank", "avg_daily_quote_usdt", "full_days"]], on="symbol", how="left", validate="many_to_one")
    assert trades.volume_rank.notna().all()
    g2 = (trades.btc_return_200 <= .073452727496624) & (trades.avg_trade_usdt >= 147.6241108493176)
    g3 = trades.v4_score >= .26
    g4 = trades.coin_return_200 < 0
    g5 = (trades.body_range < 0) & (trades.close_location <= .4)
    trades["highest_stage"] = 1 + g2.astype(int) + (g2 & g3).astype(int) + (g2 & g3 & g4).astype(int) + (g2 & g3 & g4 & g5).astype(int)
    # Values 1..5 mean the number of cumulative gates passed.
    selected = trades[(trades.leverage == 15) & (trades.highest_stage == 5) & (trades.status == "resolved")]
    by_year = selected.groupby("entry_year").agg(n=("net_pnl_usdt", "size"), net=("net_pnl_usdt", "sum"))
    print("15x stage5 reconciliation", by_year.to_dict("index"))
    assert len(selected) == 326 and abs(selected.net_pnl_usdt.sum() - 1904.59) < .05
    selected1 = trades[(trades.leverage == 1) & (trades.highest_stage == 5) & (trades.status == "resolved")]
    assert len(selected1) == 283 and abs(selected1.net_pnl_usdt.sum() - 124.27) < .05
    trades.to_parquet(OUT / "trades_scored.parquet", index=False)
    return trades


def payload(trades: pd.DataFrame, volume: pd.DataFrame) -> dict:
    # Compact, row-oriented ledger keeps every chart synchronized with all filters.
    cols = ["symbol", "entry_time", "exit_time", "entry_year", "leverage", "highest_stage", "status", "net_pnl_usdt", "exit_reason", "variant", "timeframe", "funding_usdt", "entry_fee_usdt", "exit_fee_usdt", "used_margin_usdt", "stop_adjusted"]
    d = trades[cols].copy()
    for c in ["net_pnl_usdt", "funding_usdt", "entry_fee_usdt", "exit_fee_usdt", "used_margin_usdt"]:
        d[c] = d[c].round(6)
    d = d.replace({np.nan: None})
    return {"meta": {"model": "V4 2020–2024", "score_threshold": .26, "volume_definition": "2025-01-01–2026-10-17 UTC döneminde, veri bulunan 24 saatlik günlerin quote_volume toplamlarının aritmetik ortalaması", "volume_is_retrospective": True, "signal_count_per_leverage": 19317, "trade_year_definition": "entry_year", "date_cutoff": "2026-10-17 23:59 UTC"}, "volumes": volume.replace({np.nan: None}).to_dict("records"), "columns": cols, "rows": d.values.tolist()}


def main() -> None:
    volume = volume_rank()
    print("Volume rank rows", len(volume), "with days", int((volume.full_days > 0).sum()))
    trades = scored_trades(volume)
    data = payload(trades, volume)
    (OUT / "dashboard_data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print("Rows", len(trades), "data MB", round((OUT / "dashboard_data.json").stat().st_size/1e6, 2))


if __name__ == "__main__":
    main()

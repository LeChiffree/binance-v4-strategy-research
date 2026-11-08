"""Fast, offline integrity and reproducibility checks for the frozen archive."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import backtest

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    expected_variants = {
        "65": [("close_2r", 50), ("retest_2r", 50), ("close_2r_h100", 100)],
        "9": [("close_2r", 50), ("close_2r_h25", 25), ("close_2r_h100", 100)],
        "16": [("close_2r", 50), ("close_2r_h25", 25)],
        "21": [("close_2r", 50), ("retest_2r", 50),
               ("close_2r_trend", 50), ("close_2r_volume", 50)],
    }
    assert backtest.VARIANTS == expected_variants
    assert sum(map(len, backtest.VARIANTS.values())) == 12

    info = json.loads((ROOT / "output/binance_futures_exchange_info_2026-10-18.json").read_text(encoding="utf-8"))
    contracts = [s for s in info["symbols"] if s.get("status") == "TRADING"
                 and s.get("contractType") == "PERPETUAL"
                 and s.get("quoteAsset") == "USDT" and s.get("marginAsset") == "USDT"]
    assert len(contracts) == 525

    feature_path = ROOT / "output/active_525_five_stage/v4_features.parquet"
    features = pd.read_parquet(feature_path)
    frozen = joblib.load(ROOT / "models/v4_2020_2024.joblib")
    rebuilt_path = ROOT / "output/v4_window_sweep_525/models/v4_2020-2024.joblib"
    if not rebuilt_path.exists():
        rebuilt_path = ROOT / "models/window_sweep/v4_2020-2024.joblib"
    rebuilt = joblib.load(rebuilt_path)
    assert len(features) == 32_524 and len(frozen["features"]) == 59
    x = features[frozen["features"]].copy()
    for col in frozen["categorical"]:
        categories = sorted(x[col].astype(str).unique())
        x[col] = x[col].astype(str).astype(pd.CategoricalDtype(categories=categories))
    difference = np.abs(frozen["model"].predict(x) - rebuilt["model"].predict(x))
    assert float(difference.max()) < 1e-12

    trades = pd.read_parquet(ROOT / "output/active_525_five_stage/trades.parquet")
    summary = pd.read_csv(ROOT / "output/active_525_five_stage/stage_leverage_summary.csv")
    assert len(trades) == 130_096
    assert int(trades.status.eq("resolved").sum()) == 122_826
    assert set(summary.leverage) == {1, 5, 20, 25}
    assert set(summary.stage) == {1, 2, 3, 4, 5}
    assert len(summary) == 20
    for row in summary.itertuples(index=False):
        subset = trades[trades.leverage.eq(row.leverage)]
        if row.stage > 1:
            subset = subset[subset[f"stage{row.stage}"].eq(True)]
        resolved = subset[subset.status.eq("resolved")]
        assert len(resolved) == row.trades
        assert abs(float(resolved.net_pnl_usdt.sum()) - float(row.net_usdt)) < 1e-9
        expected_mean = float(resolved.net_pnl_usdt.mean()) if len(resolved) else np.nan
        assert np.isclose(expected_mean, float(row.avg_net_per_trade_usdt), equal_nan=True)

    paper_universe = json.loads((ROOT / "paper/universe.json").read_text(encoding="utf-8"))
    assert len(paper_universe["scan_symbols"]) == 250
    assert len(paper_universe["context_symbols"]) >= 500

    print(json.dumps({
        "status": "PASS",
        "formation_variants": 12,
        "frozen_exchange_universe": len(contracts),
        "feature_rows": len(features),
        "feature_count": len(frozen["features"]),
        "model_prediction_max_abs_difference": float(difference.max()),
        "leverage_trade_rows": len(trades),
        "resolved_trade_rows": int(trades.status.eq("resolved").sum()),
        "paper_scan_symbols": len(paper_universe["scan_symbols"]),
        "paper_context_symbols": len(paper_universe["context_symbols"]),
    }, indent=2))


if __name__ == "__main__":
    main()


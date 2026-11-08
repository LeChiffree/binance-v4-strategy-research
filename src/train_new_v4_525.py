"""Fit the new V4 on 2021–24 525-universe Futures signals only."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "output/active_525_five_stage"
OUT = ROOT / "output/v4_new_525_2021_2024"
OUT.mkdir(parents=True, exist_ok=True)
CORE = ("variant", "pattern_id", "timeframe")
IDS = ("event_id", "trade_id", "symbol", "entry_time")
TRAIN_START = pd.Timestamp("2021-01-01", tz="UTC").value // 10**6
TRAIN_END = pd.Timestamp("2025-01-01", tz="UTC").value // 10**6


def main() -> None:
    features = pd.read_parquet(BASE / "v4_features.parquet")
    assert features.trade_id.is_unique
    cols = [c for c in features if c not in IDS]
    labels = pd.read_parquet(OUT / "training_2x_labels_usable.parquet")
    assert labels.trade_id.is_unique and labels.leverage.eq(2).all()
    rows = features.merge(labels[["trade_id", "symbol", "entry_time", "exit_time", "net_pnl_usdt"]],
                          on="trade_id", suffixes=("", "_label"), validate="one_to_one")
    assert rows.symbol.eq(rows.symbol_label).all()
    assert rows.entry_time.eq(rows.entry_time_label).all()
    assert rows.entry_time.ge(TRAIN_START).all() and rows.entry_time.lt(TRAIN_END).all()
    assert rows.exit_time.lt(TRAIN_END).all()
    assert set(rows.net_pnl_usdt.notna()) == {True}
    x_all = features[cols].copy()
    for name in CORE:
        categories = sorted(x_all[name].astype(str).unique())
        x_all[name] = x_all[name].astype(str).astype(pd.CategoricalDtype(categories=categories))
    fit_index = pd.Index(features.trade_id).get_indexer(rows.trade_id)
    assert (fit_index >= 0).all() and len(set(fit_index)) == len(fit_index)
    x_fit = x_all.iloc[fit_index]
    y_fit = rows.net_pnl_usdt.to_numpy(dtype=float)
    params = dict(n_estimators=160, learning_rate=.035, num_leaves=7, max_depth=4,
                  min_child_samples=120, reg_lambda=15, colsample_bytree=.8,
                  random_state=20261011, n_jobs=2, verbosity=-1)
    model = lgb.LGBMRegressor(**params)
    model.fit(x_fit, y_fit, categorical_feature=list(CORE))
    scores = model.predict(x_all)
    old = pd.read_parquet(BASE / "stage_features.parquet", columns=["trade_id", "v4_score_2021_2024"])
    result = features[list(IDS)].copy()
    result["new_v4_score"] = scores
    result = result.merge(old.rename(columns={"v4_score_2021_2024": "old_v4_score"}),
                          on="trade_id", validate="one_to_one")
    assert len(result) == len(features) and result.new_v4_score.notna().all()
    result.to_parquet(OUT / "old_new_v4_scores.parquet", index=False)
    joblib.dump({"model": model, "features": cols, "categorical": list(CORE),
                 "training_start_ms": TRAIN_START, "training_end_exclusive_ms": TRAIN_END},
                OUT / "new_v4_model.joblib")
    entry_year = pd.to_datetime(rows.entry_time, unit="ms", utc=True).dt.year
    metadata = {"name": "yeni V4", "old_name": "eski V4", "training_start": "2021-01-01 UTC",
                "training_end_exclusive": "2025-01-01 UTC", "training_rows": len(rows),
                "training_coins": int(rows.symbol.nunique()),
                "training_rows_by_year": {str(k): int(v) for k, v in entry_year.value_counts().sort_index().items()},
                "target": "2x 10 USDT isolated margin Futures net PnL, 1m trade/mark, fees, slippage, funding",
                "feature_count": len(cols), "features": cols, "threshold_usdt": .26,
                "parameters": params, "score_correlation_old_new": float(result[["old_v4_score", "new_v4_score"]].corr().iloc[0, 1]),
                "old_model_legacy_reconstruction": json.loads((BASE / "v4_reconstruction.json").read_text())}
    (OUT / "training_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    print("new V4 trained", len(rows), "labels", rows.symbol.nunique(), "coins", len(cols),
          "features", "old/new correlation", metadata["score_correlation_old_new"], flush=True)


if __name__ == "__main__":
    main()

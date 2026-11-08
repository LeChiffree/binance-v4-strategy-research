"""Train every available 2020–24 contiguous V4 window on 525-universe Futures labels."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "output/active_525_five_stage"
OLD = ROOT / "output/v4_new_525_2021_2024"
OUT = ROOT / "output/v4_window_sweep_525"
OUT.mkdir(parents=True, exist_ok=True)
CORE = ("variant", "pattern_id", "timeframe")
IDS = ("event_id", "trade_id", "symbol", "entry_time")
PARAMS = dict(n_estimators=160, learning_rate=.035, num_leaves=7, max_depth=4,
              min_child_samples=120, reg_lambda=15, colsample_bytree=.8,
              random_state=20261011, n_jobs=2, verbosity=-1)


def main() -> None:
    features = pd.read_parquet(BASE / "v4_features.parquet")
    assert features.trade_id.is_unique
    cols = [c for c in features if c not in IDS]
    labels = pd.concat([pd.read_parquet(OUT / "labels_2020_usable.parquet"),
                        pd.read_parquet(OLD / "training_2x_labels_usable.parquet")], ignore_index=True)
    assert labels.trade_id.is_unique and labels.leverage.eq(2).all()
    fit_rows = labels[["trade_id", "symbol", "entry_time", "exit_time", "net_pnl_usdt"]].merge(
        features[list(IDS)], on="trade_id", suffixes=("_label", ""), validate="one_to_one")
    assert fit_rows.symbol.eq(fit_rows.symbol_label).all()
    assert fit_rows.entry_time.eq(fit_rows.entry_time_label).all()
    all_x = features[cols].copy()
    for name in CORE:
        categories = sorted(all_x[name].astype(str).unique())
        all_x[name] = all_x[name].astype(str).astype(pd.CategoricalDtype(categories=categories))
    index = pd.Index(features.trade_id).get_indexer(fit_rows.trade_id)
    assert (index >= 0).all() and len(set(index)) == len(index)
    year = pd.to_datetime(fit_rows.entry_time, unit="ms", utc=True).dt.year.to_numpy()
    scores = features[list(IDS)].copy()
    metadata = []
    models = OUT / "models"
    models.mkdir(exist_ok=True)
    for start in range(2020, 2025):
        for end in range(start, 2025):
            key = f"{start}-{end}"
            cutoff = pd.Timestamp(f"{end+1}-01-01", tz="UTC").value // 10**6
            mask = (year >= start) & (year <= end) & fit_rows.exit_time.lt(cutoff).to_numpy()
            selected = fit_rows.loc[mask]
            if len(selected) < 100:
                raise ValueError(f"Only {len(selected)} labels for {key}")
            model = lgb.LGBMRegressor(**PARAMS)
            model.fit(all_x.iloc[index[mask]], selected.net_pnl_usdt.to_numpy(dtype=float),
                      categorical_feature=list(CORE))
            scores[key] = model.predict(all_x)
            joblib.dump({"model": model, "features": cols, "categorical": list(CORE),
                         "train_start": start, "train_end": end}, models / f"v4_{key}.joblib")
            metadata.append({"model": key, "train_start": start, "train_end": end,
                             "train_rows": len(selected), "train_coins": selected.symbol.nunique(),
                             "train_2020_rows": int((year[mask] == 2020).sum()),
                             "feature_count": len(cols), "threshold_usdt": .26})
            print("trained", key, len(selected), "rows", selected.symbol.nunique(), "coins", flush=True)
            scores.to_parquet(OUT / "scores_checkpoint.parquet", index=False)
            pd.DataFrame(metadata).to_csv(OUT / "training_windows_checkpoint.csv", index=False)
    scores.to_parquet(OUT / "window_scores.parquet", index=False)
    pd.DataFrame(metadata).to_csv(OUT / "training_windows.csv", index=False, encoding="utf-8-sig")
    (OUT / "training_metadata.json").write_text(json.dumps({
        "universe": "525 currently active USDT-M perpetuals, historical signals only when listed",
        "training_label": "2x Futures 1m replay net PnL, at most 10 USDT margin, fees/slippage/funding",
        "year_2019": "No 2019 source bars/signals in the active-525 local store; 2019-start windows are not fitted",
        "threshold_usdt": .26, "parameters": PARAMS, "feature_count": len(cols),
        "training_windows": metadata}, indent=2, ensure_ascii=False), encoding="utf-8")
    print("complete", len(metadata), "models", flush=True)


if __name__ == "__main__":
    main()

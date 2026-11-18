#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Singular-Backtest — Binance V4 Short.

Kural: secilen kapsamda (varsayilan: pattern+variant) ayni coinde ayni anda en
fazla bir acik islem. Onceki islem kapanmadan gelen yeni giris sinyali bloke
edilir. Bes kumulatif asama (stage1..stage5) ayri birer filtre akisi gibi ele
alinir; her asamada tekillik kendi kabul edilen islemleri uzerinde uygulanir.

Normal metrikler islem duzeyindeki parquet kayitlarindan birebir hesaplanir
(leverage=1, status=resolved): retAll = ortalama net / 10 USD * 100.

Kullanim:
  python Singular-Backtest/run_singular.py
  python Singular-Backtest/run_singular.py --scope=pattern
  python Singular-Backtest/run_singular.py --scope=portfolio
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRADES = os.path.join(ROOT, "output", "active_525_five_stage", "trades.parquet")
OUT = os.path.join(HERE, "results")

STAKE = 10.0
Y26 = 1767225600000  # 2026-01-01 00:00 UTC

PATTERN_NAMES = {9: "Simetrik Üçgen", 16: "Yatay Dikdörtgen", 21: "Megafon", 65: "AB=CD"}
STAGES = [
    ("stage1", "1 — Ham formasyon"),
    ("stage2", "12 — BTC + likidite"),
    ("stage3", "123 — + V4 ML"),
    ("stage4", "1234 — + coin düşüşü"),
    ("stage5", "12345 — + kırmızı mum"),
]
SCOPE_LABEL = {
    "variant": "Formasyon + varyant (pattern_id+variant), tum asama ve coinde tek pozisyon",
    "pattern": "Formasyon ailesi (pattern_id), varyantlar arasi tek pozisyon",
    "portfolio": "Tum V4 portfoyu: coin basina tek acik islem",
}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["variant", "pattern", "portfolio"], default="variant")
    ap.add_argument("--out", default=OUT)
    return ap.parse_args()


def fnum(v, default=0.0):
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def metrics_from_df(g):
    """Islem dataframe'inden dashboard metrikleri."""
    n = int(len(g))
    if n == 0:
        return {
            "trades": 0, "wins": 0, "losses": 0, "winRate": 0.0, "retAll": 0.0,
            "ret2026": 0.0, "winRatio": 0.0, "winHoldH": 0.0, "holdH": 0.0,
            "profitDays": 0, "lossDays": 0,
            "profitWeeks": 0, "lossWeeks": 0,
            "profitMonths": 0, "lossMonths": 0, "maxDD": 0.0, "kasaAll": 0.0,
            "sumR": 0.0, "pf": 0.0,
        }
    net = g["net_pnl_usdt"].to_numpy(dtype=np.float64)
    wins_arr = net > 0
    wins = int(wins_arr.sum())
    losses = n - wins
    hold = g["holding_minutes"].to_numpy(dtype=np.float64)
    g26 = g[g["entry_year"].to_numpy(dtype=np.int64) == 2026]
    day_idx = g["exit_time"].to_numpy(dtype=np.int64) // 86400000
    days = pd.Series(net).groupby(day_idx).sum()
    day_numbers = days.index.to_numpy(dtype=np.int64)
    week_idx = day_numbers - ((day_numbers + 3) % 7)  # epoch gun 0 = Persembe, Pazartesi=0
    weekly = days.groupby(week_idx).sum()
    month_idx = pd.to_datetime(day_numbers, unit="D", utc=True).strftime("%Y-%m")
    monthly = days.groupby(month_idx).sum()
    cum = days.cumsum().to_numpy()
    peak = np.maximum.accumulate(cum)
    dd = float((cum - peak).min()) if len(cum) else 0.0
    pos = float(net[wins_arr].sum())
    neg = float(-net[~wins_arr].sum())
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "winRate": wins / n,
        "retAll": float(net.mean()) / STAKE * 100.0,
        "ret2026": (float(g26["net_pnl_usdt"].mean()) / STAKE * 100.0) if len(g26) else 0.0,
        "winRatio": (wins / losses) if losses else float(wins),
        "winHoldH": float(hold[wins_arr].mean()) / 60.0 if wins else 0.0,
        "holdH": float(hold.mean()) / 60.0,
        "profitDays": int((days > 0).sum()),
        "lossDays": int((days < 0).sum()),
        "profitWeeks": int((weekly > 0).sum()),
        "lossWeeks": int((weekly < 0).sum()),
        "profitMonths": int((monthly > 0).sum()),
        "lossMonths": int((monthly < 0).sum()),
        "maxDD": dd,
        "kasaAll": float(net.sum()),
        "sumR": float(net.sum()) / STAKE,
        "pf": (pos / neg) if neg > 1e-12 else (99.0 if pos > 0 else 0.0),
    }


def scope_of(pattern_id, variant, scope):
    if scope == "variant":
        return f"p{pattern_id}|{variant}"
    if scope == "pattern":
        return f"p{pattern_id}"
    return "V4"


def main():
    args = parse_args()
    t0 = time.time()
    os.makedirs(args.out, exist_ok=True)

    df = pd.read_parquet(
        TRADES,
        columns=["trade_id", "symbol", "pattern_id", "timeframe", "variant", "entry_time",
                 "entry_year", "exit_time", "net_pnl_usdt", "holding_minutes",
                 "leverage", "status", "stage1", "stage2", "stage3", "stage4", "stage5"],
    )
    df = df[(df["leverage"] == 1) & (df["status"] == "resolved")].copy()
    df["net_pnl_usdt"] = df["net_pnl_usdt"].astype(np.float64)
    df["holding_minutes"] = df["holding_minutes"].astype(np.float64)
    df["entry_year"] = df["entry_year"].astype(np.int64)
    df["entry_time"] = df["entry_time"].astype(np.int64)
    df["exit_time"] = df["exit_time"].astype(np.int64)
    df["skey"] = [scope_of(p, v, args.scope) for p, v in zip(df["pattern_id"].values, df["variant"].values)]
    print(f"[v4] {len(df):,} islem | {df['symbol'].nunique()} sembol | kapsam={args.scope}")

    # ------------------------------ normal ------------------------------
    rows = []
    for pat in sorted(PATTERN_NAMES):
        for var in sorted(df[df["pattern_id"] == pat]["variant"].unique()):
            for stage, label in STAGES:
                g = df[(df["pattern_id"] == pat) & (df["variant"] == var) & df[stage]]
                m = metrics_from_df(g)
                rows.append({
                    "pattern_id": int(pat),
                    "pattern": PATTERN_NAMES[pat],
                    "variant": var,
                    "strategy": f"{PATTERN_NAMES[pat]} · {var}",
                    "tf": str(g["timeframe"].iloc[0]) if len(g) else "",
                    "stage": stage,
                    "stage_label": label,
                    "filter_id": stage.replace("stage", ""),
                    "filter_label": label,
                    "n_trades": m["trades"],
                })

    # ------------------------------ singular ------------------------------
    sing_by_row = {}
    blocked_by_row = {}
    sing_by_filter = {}
    blocked_by_filter = {}
    sing_by_scope = {}
    blocked_by_scope = {}
    coin_norm_acc = {}
    overlaps = 0

    for stage, label in STAGES:
        sd = df[df[stage]]
        # asama toplami
        s_all, b_all = [], 0
        scopes = {}
        bscopes = {}
        for (skey, sym), h0 in sd.groupby(["skey", "symbol"], sort=False):
            h = h0.sort_values(["entry_time", "variant", "trade_id"], kind="stable")
            mask = np.zeros(len(h), dtype=bool)
            last = -1
            blk = 0
            for i, (et, xt) in enumerate(zip(h["entry_time"].values, h["exit_time"].values)):
                if et < last:
                    blk += 1
                    continue
                last = int(xt)
                mask[i] = True
            acc = h[mask]
            # cakisma denetimi (kabul kumesi kendi icinde tekil olmali)
            last = -1
            for et, xt in zip(acc["entry_time"].values, acc["exit_time"].values):
                if et < last:
                    overlaps += 1
                else:
                    last = int(xt)
            scopes.setdefault(skey, []).append(acc)
            bscopes[skey] = bscopes.get(skey, 0) + blk
            s_all.append(acc)
            b_all += blk
        scopes = {k: pd.concat(v, ignore_index=True) for k, v in scopes.items()}

        # kabul edilen tum asamalar
        acc_all = pd.concat(s_all, ignore_index=True) if s_all else sd.iloc[0:0]
        sing_by_filter[stage] = metrics_from_df(acc_all)
        blocked_by_filter[stage] = b_all

        # satir bazinda
        for r in [x for x in rows if x["stage"] == stage]:
            g = sd[(sd["pattern_id"] == r["pattern_id"]) & (sd["variant"] == r["variant"])]
            if len(g) == 0:
                sm = metrics_from_df(g)
                sing_by_row[(r["pattern_id"], r["variant"], stage)] = sm
                blocked_by_row[(r["pattern_id"], r["variant"], stage)] = 0
                continue
            # kapsam gruplarindan bu satira dusen kabul islemleri
            key = scope_of(r["pattern_id"], r["variant"], args.scope)
            acc = scopes.get(key)
            if acc is None:
                sm = metrics_from_df(g.iloc[0:0])
                blocked_by_row[(r["pattern_id"], r["variant"], stage)] = 0
            else:
                sub = acc[(acc["pattern_id"] == r["pattern_id"]) & (acc["variant"] == r["variant"])]
                sm = metrics_from_df(sub)
                blocked_by_row[(r["pattern_id"], r["variant"], stage)] = int(len(g) - len(sub))
            sing_by_row[(r["pattern_id"], r["variant"], stage)] = sm

        # kapsam ozetleri (asamaya ozel)
        for skey, acc in scopes.items():
            sing_by_scope[(stage, skey)] = acc
            blocked_by_scope[(stage, skey)] = bscopes.get(skey, 0)

        # coin kirilimi (stage1)
        if stage == "stage1":
            for (pat, var), g in sd.groupby(["pattern_id", "variant"], sort=False):
                for sym, h in g.groupby("symbol", sort=False):
                    coin_norm_acc[(int(pat), var, sym)] = metrics_from_df(h)

        # ilerleme
        print(f"  {stage}: normal {len(sd):,} -> singular {len(acc_all):,} | bloke {b_all:,}")

    # coin singular (stage1) - kapsam gruplarindan
    coin_sing = {}
    sd1 = df[df["stage1"]]
    for (skey, sym), h0 in sd1.groupby(["skey", "symbol"], sort=False):
        h = h0.sort_values(["entry_time", "variant", "trade_id"], kind="stable")
        last = -1
        mask = np.zeros(len(h), dtype=bool)
        for i, (et, xt) in enumerate(zip(h["entry_time"].values, h["exit_time"].values)):
            if et < last:
                continue
            last = int(xt)
            mask[i] = True
        acc = h[mask]
        for (pat, var), hh in acc.groupby(["pattern_id", "variant"], sort=False):
            coin_sing[(int(pat), var, sym)] = metrics_from_df(hh)

    coins = []
    for key, nm in sorted(coin_norm_acc.items()):
        pat, var, sym = key
        sm = coin_sing.get(key)
        if sm is None:
            sm = metrics_from_df(sd1.iloc[0:0])
        coins.append({
            "pattern_id": pat,
            "pattern": PATTERN_NAMES[pat],
            "variant": var,
            "strategy": f"{PATTERN_NAMES[pat]} · {var}",
            "symbol": sym,
            "n_trades": nm["trades"],
            "s_trades": sm["trades"],
            "blocked": int(nm["trades"] - sm["trades"]),
            "blocked_pct": ((nm["trades"] - sm["trades"]) / nm["trades"] * 100.0) if nm["trades"] else 0.0,
            "n_retAll": nm["retAll"],
            "s_retAll": sm["retAll"],
            "n_winRate": nm["winRate"],
            "s_winRate": sm["winRate"],
        })

    # ------------------------------ satirlar ------------------------------
    out_rows = []
    for r in rows:
        k = (r["pattern_id"], r["variant"], r["stage"])
        sm = sing_by_row[k]
        b = blocked_by_row[k]
        row = dict(r)
        row["blocked"] = b
        row["blocked_pct"] = (b / r["n_trades"] * 100.0) if r["n_trades"] else 0.0
        for mk, v in sm.items():
            row["s_" + mk] = v
        # normal metrikleri yeniden hesapla (satirdaki normal)
        g = df[(df["pattern_id"] == r["pattern_id"]) & (df["variant"] == r["variant"]) & df[r["stage"]]]
        nm = metrics_from_df(g)
        for mk, v in nm.items():
            row["n_" + mk] = v
        row["delta_retAll"] = row["s_retAll"] - row["n_retAll"]
        row["delta_kasa"] = row["s_kasaAll"] - row["n_kasaAll"]
        out_rows.append(row)

    # ------------------------------ kapsam ozetleri ------------------------------
    scope_groups = {}
    for pat, var, sk in df[["pattern_id", "variant", "skey"]].drop_duplicates().itertuples(index=False):
        scope_groups.setdefault(sk, set()).add((int(pat), var))
    scopes_out = []
    for skey in sorted(scope_groups):
        sids = sorted(scope_groups[skey])
        entry = {
            "key": skey,
            "patterns": sorted(set(p for p, _ in sids)),
            "variants": [f"{PATTERN_NAMES[p]} · {v}" for p, v in sids],
            "filters": [],
        }
        for stage, label in STAGES:
            nm = metrics_from_df(df[df[stage] & (df["skey"] == skey)])
            sm = metrics_from_df(sing_by_scope.get((stage, skey), df.iloc[0:0]))
            b = blocked_by_scope.get((stage, skey), 0)
            e = {
                "filter_id": stage.replace("stage", ""),
                "filter_label": label,
                "blocked": b,
                "blocked_pct": (b / nm["trades"] * 100.0) if nm["trades"] else 0.0,
            }
            for mk, v in nm.items():
                e["n_" + mk] = v
            for mk, v in sm.items():
                e["s_" + mk] = v
            e["delta_retAll"] = e["s_retAll"] - e["n_retAll"]
            e["delta_kasa"] = e["s_kasaAll"] - e["n_kasaAll"]
            entry["filters"].append(e)
        scopes_out.append(entry)

    # ------------------------------ toplamlar ------------------------------
    totals_by_stage = []
    for stage, label in STAGES:
        nm = metrics_from_df(df[df[stage]])
        sm = sing_by_filter[stage]
        b = blocked_by_filter[stage]
        e = {
            "filter_id": stage.replace("stage", ""),
            "filter_label": label,
            "blocked": b,
            "blocked_pct": (b / nm["trades"] * 100.0) if nm["trades"] else 0.0,
        }
        for mk, v in nm.items():
            e["n_" + mk] = v
        for mk, v in sm.items():
            e["s_" + mk] = v
        e["delta_retAll"] = e["s_retAll"] - e["n_retAll"]
        e["delta_kasa"] = e["s_kasaAll"] - e["n_kasaAll"]
        totals_by_stage.append(e)

    tn = metrics_from_df(df)
    # toplam singular = stage1 kabul kumesi (kumulatif asamalarin ilk halkasi)
    sd1_acc = pd.concat([sing_by_scope[(st, sk)] for (st, sk) in sing_by_scope if st == "stage1"], ignore_index=True)
    ts = metrics_from_df(sd1_acc)
    tb = blocked_by_filter["stage1"]
    totals = {
        "normal": tn,
        "singular": ts,
        "blocked": tb,
        "blocked_pct": (tb / tn["trades"] * 100.0) if tn["trades"] else 0.0,
    }

    payload = {
        "repo": "binance-v4-strategy-research",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule": "Secilen kapsamda ayni coinde ayni anda en fazla bir acik islem. Onceki islem kapanmadan gelen yeni giris sinyali bloke edilir.",
        "scope": args.scope,
        "scope_label": SCOPE_LABEL[args.scope],
        "data": {
            "source": "output/active_525_five_stage/trades.parquet",
            "symbols": int(df["symbol"].nunique()),
            "patterns": len(PATTERN_NAMES),
            "variants": int(df.groupby(["pattern_id", "variant"]).ngroups),
            "stages": len(STAGES),
            "trades_all": int(len(df)),
            "stake_usd": STAKE,
            "cash_model": "Islem basina sabit 10 USD; retAll = islem basina ortalama net / 10 USD * 100",
            "priority": "Ayni giris aninda once entry_time, sonra varyant adi, sonra trade_id",
            "note": "Bes kumulatif asama ayri filtre akisi gibi ele alinir; her asamada tekillik kendi kabul kumesinde uygulanir.",
        },
        "totals": totals,
        "filters": totals_by_stage,
        "scopes": scopes_out,
        "rows": out_rows,
        "coins": coins,
        "errors": [],
        "overlap_violations": overlaps,
        "elapsed_sec": round(time.time() - t0, 1),
    }

    def rnd(obj):
        if isinstance(obj, float):
            return round(obj, 6)
        if isinstance(obj, dict):
            return {k: rnd(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [rnd(v) for v in obj]
        return obj

    payload = rnd(payload)
    with open(os.path.join(args.out, "singular_results.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.out, "run_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({
            "repo": payload["repo"],
            "generated_at": payload["generated_at"],
            "scope": args.scope,
            "scope_label": payload["scope_label"],
            "symbols": payload["data"]["symbols"],
            "variants": payload["data"]["variants"],
            "stages": len(STAGES),
            "normal_trades": tn["trades"],
            "singular_trades": ts["trades"],
            "blocked": tb,
            "blocked_pct": round(totals["blocked_pct"], 4),
            "elapsed_sec": payload["elapsed_sec"],
        }, f, ensure_ascii=False, indent=2)

    import csv
    row_cols = ["pattern_id", "pattern", "variant", "strategy", "tf", "stage", "stage_label", "filter_id",
                "filter_label", "blocked", "blocked_pct", "n_trades", "s_trades",
                "n_winRate", "s_winRate", "n_retAll", "s_retAll", "delta_retAll",
                "n_ret2026", "s_ret2026", "n_winRatio", "s_winRatio",
                "n_winHoldH", "s_winHoldH", "n_profitDays", "s_profitDays",
                "n_lossDays", "s_lossDays",
                "n_profitWeeks", "s_profitWeeks", "n_lossWeeks", "s_lossWeeks",
                "n_profitMonths", "s_profitMonths", "n_lossMonths", "s_lossMonths",
                "n_maxDD", "s_maxDD",
                "n_kasaAll", "s_kasaAll", "delta_kasa"]
    with open(os.path.join(args.out, "singular_rows.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=row_cols, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
    with open(os.path.join(args.out, "singular_coins.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["pattern_id", "pattern", "variant", "strategy", "symbol", "n_trades",
                                          "s_trades", "blocked", "blocked_pct", "n_retAll", "s_retAll",
                                          "n_winRate", "s_winRate"], extrasaction="ignore")
        w.writeheader()
        for r in coins:
            w.writerow(r)

    print("")
    print(f"[v4] kapsam    : {args.scope} ({SCOPE_LABEL[args.scope]})")
    print(f"[v4] normal    : {tn['trades']:,} islem | WR {tn['winRate']*100:.2f}% | ort {tn['retAll']:.4f}% | kasa ${tn['kasaAll']:.0f}")
    print(f"[v4] singular  : {ts['trades']:,} islem | WR {ts['winRate']*100:.2f}% | ort {ts['retAll']:.4f}% | kasa ${ts['kasaAll']:.0f}")
    print(f"[v4] bloke     : {tb:,} ({totals['blocked_pct']:.2f}%)")
    print(f"[v4] yazildi   : {os.path.join(args.out, 'singular_results.json')}")


if __name__ == "__main__":
    main()

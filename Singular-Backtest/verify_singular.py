#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Singular-Backtest dogrulama — Binance V4 Short.

Kontroller:
  1. 60 satirin tamami var mi ve her satirda singular + bloke == normal mi?
  2. Normal metrikler all-in-dashboard `allinone.json` V4 satirlariyla birebir mi?
  3. stage1 kabul kumesinde kapsam basina cakisma var mi? (payload + bagimsiz ornek)
  4. Toplamlar: singular + bloke == normal; kasaAll == sumR * 10.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRADES = os.path.join(ROOT, "output", "active_525_five_stage", "trades.parquet")
RESULT = os.path.join(HERE, "results", "singular_results.json")
ALLINONE = r"C:\Users\GIGABAYT\Projects\all-in-dashboard\allinone.json"
STAKE = 10.0
PATTERN_NAMES = {9: "Simetrik Üçgen", 16: "Yatay Dikdörtgen", 21: "Megafon", 65: "AB=CD"}
STAGE_LABEL = {
    "1": "1 — Ham formasyon",
    "12": "12 — BTC + likidite",
    "123": "123 — + V4 ML",
    "1234": "1234 — + coin düşüşü",
    "12345": "12345 — + kırmızı mum",
}
errors = []


def check(cond, msg):
    if cond:
        print(f"  [OK]   {msg}")
    else:
        print(f"  [FAIL] {msg}")
        errors.append(msg)


def close(a, b, tol=1e-5):
    if b is None:
        b = 0.0
    if a is None:
        a = 0.0
    return abs(float(a) - float(b)) <= tol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    with open(RESULT, encoding="utf-8") as f:
        pay = json.load(f)
    rows = pay["rows"]
    scope = pay["scope"]

    print("== Kapsam ==")
    print(f"  scope={scope} | {pay['scope_label']}")

    print("== 1) Satirlar ==")
    check(len(rows) == 60, f"60 satir var ({len(rows)})")
    bad = [r for r in rows if int(r["s_trades"]) + int(r["blocked"]) != int(r["n_trades"])]
    check(not bad, f"singular + bloke == normal ({len(bad)} ihlal)")

    print("== 2) allinone normal metrikleri ==")
    if os.path.exists(ALLINONE):
        allrows = [r for r in json.load(open(ALLINONE, encoding="utf-8"))["rows"] if r["source"] == "V4 Short"]
        index = {}
        for r in allrows:
            parts = r["detail"].split(" · ")
            pat = int(parts[1].split()[-1])
            var = parts[2]
            st = parts[3].replace("stage", "")
            index[(pat, var, st)] = r
        mism = []
        for r in rows:
            ref = index.get((r["pattern_id"], r["variant"], r["filter_id"]))
            if ref is None:
                mism.append((r["pattern_id"], r["variant"], r["filter_id"], "eksik"))
                continue
            for mine, theirs, tol in (
                ("n_trades", "trades", 0),
                ("n_retAll", "retAll", 1e-5),
                ("n_ret2026", "ret2026", 1e-5),
                ("n_winRatio", "winRatio", 1e-4),
                ("n_winHoldH", "winHoldH", 1e-2),
                ("n_profitDays", "profitDays", 0),
                ("n_lossDays", "lossDays", 0),
            ):
                if not close(r[mine], ref[theirs], tol):
                    mism.append((r["pattern_id"], r["variant"], r["filter_id"], mine, r[mine], ref[theirs]))
        check(not mism, f"60 satirda normal metrikler allinone ile ayni ({len(mism)} uyusmazlik)")
        if mism:
            for m in mism[:6]:
                print("    ", m)
    else:
        print("  [SKIP] allinone.json bulunamadi")

    print("== 3) Cakisma denetimi ==")
    check(pay["overlap_violations"] == 0, f"payload cakisma sayaci 0 ({pay['overlap_violations']})")

    df = pd.read_parquet(TRADES, columns=["trade_id", "symbol", "pattern_id", "variant", "entry_time",
                                          "exit_time", "leverage", "status", "stage1"])
    df = df[(df["leverage"] == 1) & (df["status"] == "resolved")].copy()

    def skey_of(p, v):
        if scope == "variant":
            return f"p{p}|{v}"
        if scope == "pattern":
            return f"p{p}"
        return "V4"

    df["skey"] = [skey_of(p, v) for p, v in zip(df["pattern_id"].values, df["variant"].values)]
    rng = random.Random(args.seed)
    groups = sorted(df[["skey", "symbol"]].drop_duplicates().itertuples(index=False, name=None))
    pick = set(rng.sample(groups, min(args.samples * 20, len(groups))))
    coin_ref = {(c["pattern_id"], c["variant"], c["symbol"]): c for c in pay["coins"]}
    bad4 = []
    acc_total = 0
    for (skey, sym), h in df.groupby(["skey", "symbol"], sort=False):
        if (skey, sym) not in pick:
            continue
        h = h.sort_values(["entry_time", "variant", "trade_id"], kind="stable")
        last = -1
        acc = 0
        for et, xt in zip(h["entry_time"].values, h["exit_time"].values):
            if et < last:
                continue
            last = int(xt)
            acc += 1
        acc_total += acc
        pat, var = h["pattern_id"].iloc[0], h["variant"].iloc[0]
        ref = coin_ref.get((int(pat), var, sym))
        if ref is None or int(ref["s_trades"]) != acc:
            bad4.append(((int(pat), var, sym), acc, None if ref is None else ref["s_trades"]))
    check(not bad4, f"{len(pick)} ornek coin-strategy grubunda {acc_total:,} kabul islemi coins payload'i ile ayni ({len(bad4)} uyusmazlik)")

    print("== 4) Toplamlar ==")
    t = pay["totals"]
    check(t["singular"]["trades"] + t["blocked"] == t["normal"]["trades"],
          f"singular {t['singular']['trades']:,} + bloke {t['blocked']:,} == normal {t['normal']['trades']:,}")
    for who in ("normal", "singular"):
        m = t[who]
        check(close(m["kasaAll"], m["sumR"] * STAKE, 1e-3), f"{who}: kasaAll == sumR * 10")
        ok = (m["trades"] == 0) or close(m["retAll"], m["sumR"] / m["trades"] * 100.0, 1e-6)
        check(ok, f"{who}: retAll == ortalama net * 100")

    print("")
    if errors:
        print(f"SONUC: {len(errors)} HATA")
        sys.exit(1)
    print("SONUC: TUM KONTROLLER GECTI")


if __name__ == "__main__":
    main()

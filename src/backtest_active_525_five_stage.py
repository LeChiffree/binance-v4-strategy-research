"""1m USD-M Futures replay for five cumulative gates and four leverages."""
from __future__ import annotations

import concurrent.futures
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import active_525_minute_store as minute
import backtest
import leverage_scenarios as lev
import download_d_active_525_funding as active_funding

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/active_525_five_stage"
PARTS = OUT / "trade_parts"
PARTS.mkdir(parents=True, exist_ok=True)
LEVERAGES = (1, 5, 20, 25)
MARGIN = 10.0
FEE = .001
SLIP = .0002
MMR = .005
BUFFER = .005
BTC_MAX = .073452727496624
LIQUIDITY_MIN = 147.6241108493176
V4_MIN = .26
INFO = json.loads((ROOT / "output/binance_futures_exchange_info_2026-10-18.json").read_text(encoding="utf-8"))
CONTRACTS = {x["symbol"]: x for x in INFO["symbols"]}
CUTOFF = json.loads((ROOT / "data/active_525/cutoff.json").read_text())["closed_bar_cutoff_ms"]


def order_quantity(contract: dict, entry: float, qty: float) -> float | None:
    filters = {x["filterType"]: x for x in contract.get("filters", [])}
    lot = filters.get("MARKET_LOT_SIZE", filters.get("LOT_SIZE", {}))
    step = float(lot.get("stepSize", 0) or 0)
    minimum = float(lot.get("minQty", 0) or 0)
    notional = float(filters.get("MIN_NOTIONAL", {}).get("notional", 0) or 0)
    rounded = math.floor(qty/step+1e-10)*step if step else qty
    if rounded+1e-12 < minimum or rounded*entry+1e-9 < notional:
        return None
    return rounded


def base_row(r, leverage: int, **kw) -> dict:
    return {"event_id": r.event_id, "trade_id": r.trade_id, "symbol": r.symbol,
            "pattern_id": int(r.pattern_id), "timeframe": r.timeframe, "variant": r.variant,
            "horizon_bars": int(r.horizon_bars),
            "entry_time": int(r.entry_time), "entry_year": int(pd.Timestamp(r.entry_time, unit="ms", tz="UTC").year),
            "leverage": leverage, "margin_usdt": MARGIN,
            "source": "Binance USD-M Futures 1m/mark 1m", **kw}


def replay_signal(r, funding: pd.DataFrame | None) -> list[dict]:
    if int(r.expiry_time) > CUTOFF:
        return [base_row(r, l, status="unresolved_data", error="incomplete horizon") for l in LEVERAGES]
    try:
        trade = minute.load(r.symbol, "1m", int(r.entry_time), int(r.expiry_time))
        mark = minute.load(r.symbol, "mark_1m", int(r.entry_time), int(r.expiry_time))
    except Exception as exc:
        return [base_row(r, l, status="unresolved_data", error=str(exc)[:300]) for l in LEVERAGES]
    entry = float(trade.open.iloc[0])*(1-SLIP)
    stop = float(r.raw_stop)+.25*float(r.atr_at_confirm)
    risk = stop-entry
    target = entry-2*risk
    if risk <= 0 or target <= 0:
        return [base_row(r, l, status="rejected", error="invalid_risk_or_target") for l in LEVERAGES]
    net_win = (entry-target*(1+SLIP))/entry-FEE*(1+target*(1+SLIP)/entry)
    net_loss = (entry-stop*(1+SLIP))/entry-FEE*(1+stop*(1+SLIP)/entry)
    if net_win/abs(net_loss) < 1.5:
        return [base_row(r, l, status="rejected", error="net_reward_risk_below_1_5") for l in LEVERAGES]
    t_open, t_high, t_low, t_close = [trade[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close")]
    m_open, m_high = [mark[c].to_numpy(dtype=float) for c in ("open", "high")]
    mt = trade.open_time.to_numpy(dtype=np.int64)
    if funding is None:
        return [base_row(r, l, status="unresolved_data", error="missing historical funding") for l in LEVERAGES]
    ft = funding.fundingTime.to_numpy(dtype=np.int64)
    fr = funding.fundingRate.to_numpy(dtype=float)
    # A missing recent month must never be interpreted as zero funding.
    if len(ft) == 0 or (int(r.expiry_time)-int(ft[-1]) > 24*3_600_000):
        return [base_row(r, l, status="unresolved_data", error="funding history ends before holding window") for l in LEVERAGES]
    in_window = ft[(ft >= int(r.entry_time)-24*3_600_000) & (ft < int(r.expiry_time)+24*3_600_000)]
    if int(r.expiry_time)-int(r.entry_time) > 24*3_600_000 and len(in_window) == 0:
        return [base_row(r, l, status="unresolved_data", error="no funding observations near holding window") for l in LEVERAGES]
    if len(in_window) > 1 and np.any(np.diff(in_window) > 24*3_600_000):
        return [base_row(r, l, status="unresolved_data", error="funding history gap in holding window") for l in LEVERAGES]
    rows = []
    for leverage in LEVERAGES:
        intended_quantity = MARGIN*leverage/entry
        quantity = order_quantity(CONTRACTS[r.symbol], entry, intended_quantity)
        liq = entry*(1+1/leverage-FEE)/(1+MMR)
        guard = liq-BUFFER*entry
        adjusted = stop >= guard
        effective_stop = min(stop, guard)
        common = dict(entry_price=entry, original_stop=stop, original_target=target,
                      effective_stop=effective_stop, stop_adjusted=adjusted,
                      liquidation_initial=liq, quantity=quantity,
                      used_margin_usdt=(quantity*entry/leverage if quantity is not None else None),
                      v4_score_2021_2024=float(r.v4_score_2021_2024),
                      stage1=True, stage2=bool(r.stage2), stage3=bool(r.stage3),
                      stage4=bool(r.stage4), stage5=bool(r.stage5))
        if quantity is None:
            rows.append(base_row(r, leverage, status="current_min_order_fail", **common))
            continue
        trigger_open = m_open if adjusted else t_open
        trigger_high = m_high if adjusted else t_high
        stop_touch = (trigger_open >= effective_stop) | (trigger_high >= effective_stop)
        target_touch = t_low <= target
        hits = np.flatnonzero(stop_touch | target_touch)
        if len(hits):
            i = int(hits[0])
            exit_time = int(mt[i])
            if stop_touch[i]:
                gap = trigger_open[i] >= effective_stop
                fill = max(effective_stop, t_open[i]) if gap else effective_stop
                exit_price = fill*(1+SLIP)
                reason = ("protective_stop_gap" if gap else "protective_stop") if adjusted else ("stop_gap" if gap else "stop")
            else:
                exit_price = target*(1+SLIP)
                reason = "target"
        else:
            i = len(mt)-1
            exit_time = int(r.expiry_time)
            exit_price = float(t_close[i])*(1+SLIP)
            reason = "timeout"
        funding_mask = (ft >= int(r.entry_time)) & (ft < exit_time)
        funding_usdt = quantity*entry*float(fr[funding_mask].sum())
        entry_fee = FEE*quantity*entry
        exit_fee = FEE*quantity*exit_price
        gross = quantity*(entry-exit_price)
        uncapped = gross-entry_fee-exit_fee+funding_usdt
        used_margin = quantity*entry/leverage
        net = max(-used_margin, uncapped)
        earlier_liq = bool(np.any(m_high[:i] >= liq))
        same_minute_liq = bool(m_high[i] >= liq)
        risk_flag = "earlier_mark_liq_cross" if earlier_liq else (
            "same_minute_liq_possible" if same_minute_liq else ("margin_exhausted_proxy" if uncapped < -MARGIN else ""))
        if len(hits) and bool(stop_touch[i] and target_touch[i]):
            risk_flag = (risk_flag + ";" if risk_flag else "") + "same_1m_tp_stop_touch"
        rows.append(base_row(r, leverage, status="resolved", exit_time=exit_time,
                             exit_price=exit_price, exit_reason=reason, gross_price_pnl_usdt=gross,
                             funding_usdt=funding_usdt, entry_fee_usdt=entry_fee,
                             exit_fee_usdt=exit_fee, uncapped_net_usdt=uncapped,
                             net_pnl_usdt=net, liquidation_risk_flag=risk_flag,
                             holding_minutes=(exit_time-int(r.entry_time))/60_000, **common))
    return rows


def process_symbol(symbol: str, signals: pd.DataFrame) -> tuple[str, int]:
    path = PARTS / f"{symbol}.parquet"
    if path.exists():
        try:
            existing = pd.read_parquet(path)
            if len(existing) == len(signals)*len(LEVERAGES):
                return symbol, len(existing)
        except Exception:
            pass
    funding = active_funding.load(symbol)
    rows = []
    # Keep adjacent trades in the same archive months to reuse 1m file cache.
    for r in signals.sort_values("entry_time").itertuples(index=False):
        rows.extend(replay_signal(r, funding))
    result = pd.DataFrame(rows)
    temp = path.with_suffix(".parquet.tmp")
    result.to_parquet(temp, index=False)
    temp.replace(path)
    return symbol, len(rows)


def main() -> None:
    signal = pd.read_parquet(OUT / "raw_signals.parquet")
    feature = pd.read_parquet(OUT / "stage_features.parquet")
    signal = signal.merge(feature.drop(columns=["symbol", "variant", "timeframe", "entry_time"]),
                          on=["event_id", "trade_id"], validate="one_to_one")
    signal["stage2"] = signal.btc_return_200.le(BTC_MAX) & signal.avg_trade_usdt.ge(LIQUIDITY_MIN)
    signal["stage3"] = signal.stage2 & signal.v4_score_2021_2024.ge(V4_MIN)
    signal["stage4"] = signal.stage3 & signal.coin_return_200.lt(0)
    signal["stage5"] = signal.stage4 & signal.body_range.lt(0) & signal.close_location.le(.4)
    signal[["trade_id", "event_id", "symbol", "pattern_id", "timeframe", "variant", "entry_time",
            "stage2", "stage3", "stage4", "stage5"]].to_parquet(OUT / "stage_masks_raw.parquet", index=False)
    groups = list(signal.groupby("symbol", sort=True))
    print(len(signal), "raw signals", len(groups), "signal coins", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        jobs = {pool.submit(process_symbol, symbol, group): symbol for symbol, group in groups}
        for n, future in enumerate(concurrent.futures.as_completed(jobs), 1):
            symbol, count = future.result()
            if n % 10 == 0 or n == len(jobs):
                print(f"replay {n}/{len(jobs)} coins; latest {ascii(symbol)} {count} rows", flush=True)
    trades = pd.concat([pd.read_parquet(PARTS / f"{symbol}.parquet") for symbol, _ in groups], ignore_index=True)
    trades = trades.drop(columns=[f"stage{i}" for i in range(2, 6)], errors="ignore")
    trades = trades.merge(signal[["trade_id", "stage2", "stage3", "stage4", "stage5"]],
                          on="trade_id", validate="many_to_one")
    trades["stage1"] = True
    trades.to_parquet(OUT / "trades.parquet", index=False)
    trades.to_csv(OUT / "trades.csv", index=False, encoding="utf-8-sig")
    rows = []
    for leverage in LEVERAGES:
        x = trades[trades.leverage.eq(leverage)]
        for stage in range(1, 6):
            y = x if stage == 1 else x[x[f"stage{stage}"].eq(True)]
            z = y[y.status.eq("resolved")]
            pnl = z.net_pnl_usdt.to_numpy(dtype=float)
            rows.append({"leverage": leverage, "stage": stage, "signal_rows": len(y),
                         "trades": len(z), "unresolved": int(y.status.eq("unresolved_data").sum()),
                         "min_order_fail": int(y.status.eq("current_min_order_fail").sum()),
                         "rejected": int(y.status.eq("rejected").sum()),
                         "net_usdt": float(pnl.sum()), "avg_net_per_trade_usdt": float(pnl.mean()) if len(pnl) else None,
                         "gross_profit_usdt": float(pnl[pnl>0].sum()),
                         "gross_loss_usdt": float(pnl[pnl<0].sum()),
                         "risk_flagged_trades": int(z.liquidation_risk_flag.fillna("").ne("").sum())})
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "stage_leverage_summary.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()

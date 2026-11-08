"""One-way market observation and isolated 5x paper execution."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import backtest
import live_features
import market
import store
import universe
from config import (AVG_TRADE_USDT_MIN, BTC_RETURN_200_MAX, ENTRY_MAX_LAG_MS,
                    FEE_RATE, FOUR_HOURS, HEALTH_MAX_AGE_MS, INITIAL_EQUITY,
                    LEVERAGE, MARGIN, MMR, MODEL, MODEL_THRESHOLD, NOTIONAL,
                    ONE_HOUR, ONE_MIN, SLIPPAGE)

LOG = logging.getLogger(__name__)
# Paper candidate set: only the three explicitly approved 4h branches.
SELECTED_VARIANTS={(65,"close_2r"),(65,"retest_2r"),(9,"close_2r")}


class PaperEngine:
    def __init__(self):
        store.init()
        self.universe = universe.load_or_build()
        self.scan_symbols = sorted(set(self.universe["scan_symbols"]))
        self.context_symbols = sorted(set(self.universe["context_symbols"]) | {"BTCUSDT"})
        self.model = joblib.load(MODEL)
        self.model_features = list(self.model["features"])
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.last_heartbeat_ms = 0
        self.last_market_ms = 0
        self.last_scan = {"1h": None, "4h": None}
        self.last_error = None
        self.scan_errors = {}
        self.filter_state = {
            "formation": {"enabled": True, "ready": False, "detail": "İlk tarama bekleniyor"},
            "btc": {"enabled": True, "ready": False, "detail": "BTC verisi bekleniyor"},
            "liquidity": {"enabled": True, "ready": False, "detail": "Hacim verisi bekleniyor"},
            "v4": {"enabled": True, "ready": bool(self.model_features),
                   "detail": f"Yeni V4 2020–2024 · {len(self.model_features)} girdi · eşik {MODEL_THRESHOLD:.2f}"},
            "execution": {"enabled": True, "ready": True, "detail": f"Sadece paper · {MARGIN:g} USDT teminat · {LEVERAGE}x · max 10"},
        }
        self._last_period = {}
        self._last_attempt = {}

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="paper-v4-5x", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=8)

    def _set_filter(self, name: str, ready: bool, detail: str):
        with self.lock:
            self.filter_state[name] = {"enabled": True, "ready": ready, "detail": detail}

    def _loop(self):
        store.event("system", "Yeni V4 2020–2024 paper izleme başlatıldı",
                    {"scan_coins": len(self.scan_symbols),"leverage":LEVERAGE,"margin":MARGIN,
                     "stage":"123","selected_variants":sorted(SELECTED_VARIANTS)})
        while self.running:
            cycle_start = time.monotonic()
            try:
                now = market.server_time()
                self.last_heartbeat_ms = now
                self.last_market_ms = now
                self._monitor_positions(now)
                for tf, duration in (("4h", FOUR_HOURS),):
                    period = now // duration
                    if self._last_period.get(tf) != period and time.monotonic() - self._last_attempt.get(tf, 0) >= 60:
                        self._last_attempt[tf] = time.monotonic()
                        try:
                            self._scan(tf, period * duration, now)
                            self._last_period[tf] = period
                        except Exception as exc:
                            LOG.exception("scan %s failed", tf)
                            self.scan_errors[tf] = str(exc)
                            self._set_filter("formation", False, f"{tf} tarama hatası: {str(exc)[:110]}")
                            store.event("error", f"{tf} tarama hatası", {"error": str(exc)[:300]})
                store.snapshot(now)
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                LOG.exception("engine cycle failed")
                store.event("error", "Canlı döngü hatası", {"error": str(exc)[:300]})
            time.sleep(max(1, 15 - (time.monotonic() - cycle_start)))

    def _scan(self, tf: str, entry_ms: int, scan_started_ms: int):
        symbols = self.context_symbols
        frames, errors = market.batch_bars(symbols, tf)
        self.scan_errors[tf] = errors
        if "BTCUSDT" not in frames:
            raise ValueError("BTC market data unavailable")
        context = live_features.market_context(frames, tf, entry_ms)
        btc_200 = context[200]["BTCUSDT"]
        btc_pass = btc_200 <= BTC_RETURN_200_MAX
        self._set_filter("btc", True,
                         f"200 mum BTC getirisi {btc_200 * 100:+.2f}% · sınır +{BTC_RETURN_200_MAX * 100:.2f}% · {'geçiyor' if btc_pass else 'geçmiyor'}")
        self._set_filter("liquidity", True,
                         f"Ortalama işlem ≥ {AVG_TRADE_USDT_MIN:.2f} USDT · sinyal bazında denetleniyor")
        self._set_filter("formation", True,
                         f"{tf} kapalı mumlar güncel · {len(frames)}/{len(symbols)} veri serisi")
        self.last_scan[tf] = entry_ms
        can_enter = market.server_time() - entry_ms <= ENTRY_MAX_LAG_MS
        # Replaying a 25–100 bar expiry must not suppress a currently new signal.
        backtest.NOW = entry_ms + 200 * FOUR_HOURS
        candidates = []
        for symbol in self.scan_symbols:
            bars = frames.get(symbol)
            if bars is None or len(bars) < 430:
                continue
            # Current, still-open bar is used only as the next-bar entry open.
            if not (bars.open_time == entry_ms).any():
                continue
            try:
                signals = backtest.signals(bars.iloc[-650:].copy(), symbol, tf)
                candidates.extend(s for s in signals if s["entry_time"] == entry_ms)
            except Exception as exc:
                LOG.warning("signal %s/%s: %s", symbol, tf, exc)
        if not candidates:
            store.event("scan", f"{tf} taraması: yeni formasyon sinyali yok",
                        {"coins": len(frames), "btc_pass": btc_pass, "entry_allowed": can_enter})
            return
        accepted = []
        failures = []
        for signal in candidates:
            try:
                variant_key=(int(signal["pattern_id"]),signal["variant"])
                if variant_key not in SELECTED_VARIANTS:
                    store.event("reject",f"{signal['symbol']} {signal['pattern_id']}/{signal['variant']} seçili varyant değil",
                                {"reason":"unselected_variant"})
                    continue
                x, feat, entry_price, stop = live_features.make_features(
                    signal, frames, context, self.model_features)
                score = float(self.model["model"].predict(x)[0])
                liquid = feat["avg_trade_usdt"] >= AVG_TRADE_USDT_MIN
                model_pass = score >= MODEL_THRESHOLD
                if not (btc_pass and liquid and model_pass):
                    store.event("reject", f"{signal['symbol']} {signal['pattern_id']}/{signal['variant']} filtre dışında",
                                {"btc": btc_pass, "liquidity": liquid, "v4": model_pass,
                                 "score": score, "avg_trade_usdt": feat["avg_trade_usdt"]})
                    continue
                risk = stop - entry_price
                target = entry_price - 2 * risk
                if risk <= 0 or target <= 0:
                    continue
                target_fill = target * (1 + SLIPPAGE)
                stop_fill = stop * (1 + SLIPPAGE)
                net_win = (entry_price - target_fill) / entry_price - FEE_RATE * (1 + target_fill / entry_price)
                net_loss = (entry_price - stop_fill) / entry_price - FEE_RATE * (1 + stop_fill / entry_price)
                if net_loss >= 0 or net_win / abs(net_loss) < 1.5:
                    continue
                accepted.append((score, signal, entry_price, stop, target, feat))
            except Exception as exc:
                failures.append(f"{signal['symbol']}/{signal['variant']}: {exc}")
        if failures:
            self._set_filter("v4", False, f"{len(failures)} sinyalde feature/model hatası")
            store.event("error", "Model girdisi üretilemeyen sinyal", {"examples": failures[:8]})
        else:
            self._set_filter("v4", True,
                             f"Model çalışıyor · son taramada {len(candidates)} sinyal değerlendirildi")
        if not can_enter:
            store.event("scan", f"{tf} sinyalleri geçmişte kaldı; yeni işlem açılmadı",
                        {"accepted": len(accepted), "lag_ms": market.server_time() - entry_ms})
            return
        for score, signal, entry_price, stop, target, feat in sorted(accepted, reverse=True, key=lambda z: z[0]):
            key = f"{signal['symbol']}|{signal['pattern_id']}|{signal['variant']}|{signal['entry_time']}"
            position_id = hashlib.sha256(key.encode()).hexdigest()[:24]
            row = {"id": position_id, "symbol": signal["symbol"],
                   "strategy": f"{signal['pattern_id']}/{signal['variant']}",
                   "pattern_id": str(signal["pattern_id"]), "timeframe": tf,
                   "variant": signal["variant"], "entry_time": entry_ms,
                   "entry_price": entry_price, "stop": stop, "target": target,
                   "expiry_time": int(signal["expiry_time"]), "quantity": NOTIONAL / entry_price,
                   "margin": MARGIN, "score": score, "status": "open",
                   "mark_price": entry_price, "mark_time": entry_ms,
                   "last_checked": entry_ms - ONE_MIN,
                   "created_at": market.server_time()}
            if store.insert_position(row):
                store.event("entry", f"{signal['symbol']} {LEVERAGE}x short açıldı",
                            {"strategy": row["strategy"], "entry": entry_price,
                             "stop": stop, "target": target, "score": score})
            else:
                store.event("reject", "Kasa/tekrar sınırı: işlem açılmadı",
                            {"symbol": signal["symbol"], "strategy": row["strategy"]})

    def _monitor_positions(self, now_ms: int):
        worst_equity = None
        for p in store.positions("open"):
            first = max(p["entry_time"], (p["last_checked"] or p["entry_time"] - ONE_MIN) + ONE_MIN)
            end = (now_ms // ONE_MIN) * ONE_MIN - ONE_MIN
            if first > end:
                continue
            minutes = market.recent_minutes(p["symbol"], first, end)
            if minutes.empty:
                continue
            expected = np.arange(first, int(minutes.open_time.iloc[-1]) + ONE_MIN, ONE_MIN)
            if not np.array_equal(minutes.open_time.to_numpy(dtype=np.int64), expected):
                raise ValueError(f"1m gap for active {p['symbol']} from {first}")
            for minute in minutes.itertuples(index=False):
                t = int(minute.open_time)
                if t + ONE_MIN > now_ms:
                    break
                funding_events = market.funding(p["symbol"], p["entry_time"], t + ONE_MIN)
                fund = NOTIONAL * sum(rate for _, rate in funding_events)
                qty = p["quantity"]
                entry_fee = FEE_RATE * NOTIONAL
                liq = (MARGIN - entry_fee + NOTIONAL + fund) / (qty * (1 + MMR))
                reason = None
                price = float(minute.close)
                if minute.open >= liq:
                    reason, price = "approx_liquidation", float(minute.open if minute.open >= liq else liq)
                elif minute.open >= p["stop"]:
                    reason, price = "stop_gap", float(minute.open) * (1 + SLIPPAGE)
                elif p["stop"] < liq and minute.high >= p["stop"]:
                    reason, price = "stop", float(p["stop"]) * (1 + SLIPPAGE)
                elif minute.high >= liq:
                    reason, price = "approx_liquidation", liq
                elif minute.high >= p["stop"]:
                    reason, price = "stop", float(p["stop"]) * (1 + SLIPPAGE)
                elif minute.low <= p["target"]:
                    reason, price = "target", float(p["target"]) * (1 + SLIPPAGE)
                elif t + ONE_MIN >= p["expiry_time"]:
                    reason, price = "timeout", float(minute.close) * (1 + SLIPPAGE)
                if reason:
                    pnl = qty * (p["entry_price"] - price) - FEE_RATE * qty * (p["entry_price"] + price) + fund
                    if reason == "approx_liquidation" or pnl < -MARGIN:
                        pnl = max(-MARGIN, pnl)
                        reason = "approx_liquidation" if reason == "approx_liquidation" else "margin_exhausted_proxy"
                    store.close_position(p["id"], t, price, reason, fund, pnl)
                    store.event("exit", f"{p['symbol']} {reason} · {pnl:+.2f} USDT",
                                {"position_id": p["id"], "price": price, "pnl": pnl})
                    store.snapshot(t + ONE_MIN)
                    break
                store.update_mark(p["id"], float(minute.close), t, fund)
                # A short's intra-minute worst mark is its high. Update the
                # observed low-water mark without pretending to know tick order.
                active = store.snapshot(t + ONE_MIN)
                worst_unrealized = qty * (p["entry_price"] - float(minute.high))
                worst_unrealized -= FEE_RATE * qty * (p["entry_price"] + float(minute.high))
                worst_equity = min(worst_equity or float("inf"),
                                   active["equity"] - active["unrealized_pnl"] + worst_unrealized + fund)
        if worst_equity is not None:
            store.snapshot(now_ms, worst_equity)

    def state(self):
        now = int(time.time() * 1000)
        with self.lock:
            filters = {k: dict(v) for k, v in self.filter_state.items()}
        scans_current = self.last_scan["4h"] == now // FOUR_HOURS * FOUR_HOURS
        if not scans_current:
            filters["formation"] = {"enabled": True, "ready": False,
                                    "detail": "4h güncel taraması bekleniyor"}
        feed_fresh = bool(self.last_market_ms and now - self.last_market_ms <= HEALTH_MAX_AGE_MS)
        if not feed_fresh:
            filters["market_feed"] = {"enabled": True, "ready": False,
                                      "detail": self.last_error or "Binance veri akışı bekleniyor"}
        else:
            filters["market_feed"] = {"enabled": True, "ready": True,
                                      "detail": f"Son Binance kontrolü {max(0,(now-self.last_market_ms)//1000)} saniye önce"}
        active = store.positions("open")
        closed = store.positions("closed")
        samples = store.recent_samples()
        latest = samples[-1] if samples else store.snapshot(now)
        latest["net_pnl"] = latest["realized_pnl"] + latest["unrealized_pnl"]
        latest["reserved_margin"] = latest["active_count"] * MARGIN
        latest["initial_equity"] = INITIAL_EQUITY
        return {"mode": "PAPER_ONLY", "leverage": LEVERAGE, "margin_per_trade": MARGIN,
                "notional_per_trade": NOTIONAL, "initial_equity": INITIAL_EQUITY,
                "engine_running": self.running and bool(self.thread and self.thread.is_alive()),
                "last_heartbeat_ms": self.last_heartbeat_ms, "last_scan": self.last_scan,
                "last_error": self.last_error, "filters": filters,
                "scan_symbol_count": len(self.scan_symbols),
                "context_symbol_count": len(self.context_symbols),
                "universe_asof_month": self.universe.get("asof_month"),
                "universe_ranking_period": [self.universe.get("ranking_start"),self.universe.get("ranking_end")],
                "model_train_period": "2020-2024", "model_threshold": MODEL_THRESHOLD,
                "selected_variants": sorted(f"{p}/{v}" for p,v in SELECTED_VARIANTS),
                "equity": latest, "open_positions": active, "closed_count": len(closed),
                "recent_closed": closed[:50], "events": store.recent_events(60),
                "equity_series": samples}

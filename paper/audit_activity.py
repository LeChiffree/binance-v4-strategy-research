"""Read-only audit of paper service activity and observation gaps."""
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

db = sqlite3.connect(Path(__file__).with_name("paper.sqlite3"))
db.row_factory = sqlite3.Row


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, ZoneInfo("Europe/Istanbul")).isoformat() if ms else None


samples = [dict(r) for r in db.execute("SELECT * FROM equity_samples ORDER BY timestamp")]
events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY timestamp")]
positions = [dict(r) for r in db.execute("SELECT * FROM positions ORDER BY entry_time")]
gaps = []
for a, b in zip(samples, samples[1:]):
    delta = b["timestamp"] - a["timestamp"]
    if delta >= 120_000:
        gaps.append({"from": iso(a["timestamp"]), "to": iso(b["timestamp"]),
                     "minutes": round(delta / 60_000, 2)})

print(json.dumps({
    "first_sample": iso(samples[0]["timestamp"]) if samples else None,
    "last_sample": iso(samples[-1]["timestamp"]) if samples else None,
    "sample_count": len(samples),
    "first_event": iso(events[0]["timestamp"]) if events else None,
    "last_event": iso(events[-1]["timestamp"]) if events else None,
    "event_count": len(events),
    "event_kinds": dict(Counter(e["kind"] for e in events)),
    "errors_first": iso(next((e["timestamp"] for e in events if e["kind"] == "error"), None)),
    "errors_last": iso(next((e["timestamp"] for e in reversed(events) if e["kind"] == "error"), None)),
    "error_types": dict(Counter(e["message"] for e in events if e["kind"] == "error")),
    "scans_by_day": dict(Counter(iso(e["timestamp"])[:10] for e in events if e["kind"] == "scan")),
    "systems": [{"at": iso(e["timestamp"]), "message": e["message"]} for e in events if e["kind"] == "system"],
    "gaps_over_2min_count": len(gaps),
    "largest_gaps": sorted(gaps, key=lambda x: x["minutes"], reverse=True)[:20],
    "positions": [{"symbol": p["symbol"], "status": p["status"],
                   "entry": iso(p["entry_time"]), "exit": iso(p["exit_time"]),
                   "pnl": p["net_pnl_usdt"]} for p in positions],
    "last_equity": samples[-1]["equity"] if samples else None,
    "last_min_equity": samples[-1]["min_equity"] if samples else None,
}, ensure_ascii=False, indent=2))

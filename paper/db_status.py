import json, sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

db=sqlite3.connect(Path(__file__).with_name('paper.sqlite3'))
def iso(ms):
    return datetime.fromtimestamp(ms/1000,ZoneInfo('Europe/Istanbul')).isoformat() if ms else None
sample=db.execute('select max(timestamp),count(*) from equity_samples').fetchone()
event=db.execute('select max(timestamp),count(*) from events').fetchone()
positions=db.execute('select status,count(*),coalesce(sum(net_pnl_usdt),0) from positions group by status').fetchall()
print(json.dumps({'last_sample_ms':sample[0],'last_sample':iso(sample[0]),'samples':sample[1],
                  'last_event_ms':event[0],'last_event':iso(event[0]),'events':event[1],
                  'positions':[{'status':x[0],'count':x[1],'net':x[2]} for x in positions]},indent=2))

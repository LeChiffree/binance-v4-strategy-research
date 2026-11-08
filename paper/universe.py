"""Point-in-time Binance USD-M universe and prior-30-day volume ranking."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import market
from config import FUTURES, UNIVERSE


def build() -> dict:
    now=datetime.now(timezone.utc)
    cutoff=datetime(now.year,now.month,1,tzinfo=timezone.utc)
    start=cutoff-timedelta(days=30)
    info=market._get(FUTURES,"/exchangeInfo")
    symbols=sorted(x["symbol"] for x in info["symbols"]
                   if x.get("status")=="TRADING" and x.get("quoteAsset")=="USDT"
                   and x.get("contractType")=="PERPETUAL")

    def volume(symbol):
        rows=market._get(FUTURES,"/klines",{"symbol":symbol,"interval":"1d",
                         "startTime":int(start.timestamp()*1000),
                         "endTime":int(cutoff.timestamp()*1000)-1,"limit":40})
        vals=[float(r[7]) for r in rows if int(r[0])<int(cutoff.timestamp()*1000)]
        return symbol,len(vals),sum(vals)/len(vals) if vals else 0.0

    ranked=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs={pool.submit(volume,s):s for s in symbols}
        for job in as_completed(jobs):
            try:
                sym,days,avg=job.result()
                if days>=20: ranked.append({"symbol":sym,"full_days":days,"avg_daily_quote_usdt":avg})
            except Exception:
                pass
    ranked.sort(key=lambda r:r["avg_daily_quote_usdt"],reverse=True)
    result={"asof_month":cutoff.strftime('%Y-%m'),"ranking_start":start.strftime('%Y-%m-%d'),
            "ranking_end":(cutoff-timedelta(days=1)).strftime('%Y-%m-%d'),
            "active_symbols":symbols,"context_symbols":[r["symbol"] for r in ranked],
            "scan_symbols":[r["symbol"] for r in ranked[:250]],"ranking":ranked}
    UNIVERSE.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def load_or_build() -> dict:
    month=datetime.now(timezone.utc).strftime('%Y-%m')
    if UNIVERSE.exists():
        data=json.loads(UNIVERSE.read_text(encoding='utf-8'))
        if data.get('asof_month')==month and len(data.get('scan_symbols',[]))==250:
            return data
    return build()


if __name__=='__main__':
    x=build()
    print(json.dumps({k:x[k] for k in ('asof_month','ranking_start','ranking_end')},ensure_ascii=False))
    print('active',len(x['active_symbols']),'context',len(x['context_symbols']),'scan',len(x['scan_symbols']))

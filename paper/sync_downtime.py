"""Replay the latest observation gap and add explicitly tagged paper backfill rows."""
from __future__ import annotations

import hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib, numpy as np
import backtest, live_features, market, store
from config import (DB, MODEL, UNIVERSE, ONE_HOUR, FOUR_HOURS, ONE_MIN,
                    BTC_RETURN_200_MAX, AVG_TRADE_USDT_MIN, MODEL_THRESHOLD,
                    SLIPPAGE, FEE_RATE, MARGIN, NOTIONAL, LEVERAGE, MMR,
                    MAX_POSITIONS)

TRT=ZoneInfo('Europe/Istanbul')
SELECTED={(65,'close_2r'),(65,'retest_2r'),(9,'close_2r')}

def iso(ms): return datetime.fromtimestamp(ms/1000,TRT).isoformat()

def latest_gap(now):
    with sqlite3.connect(DB) as db:
        xs=[x[0] for x in db.execute('select timestamp from equity_samples order by timestamp')]
    gaps=[(a,b,b-a) for a,b in zip(xs,xs[1:]) if b-a>10*ONE_MIN]
    if not gaps:
        if xs and now-xs[-1]>10*ONE_MIN:
            return xs[-1],now,now-xs[-1]
        raise RuntimeError('10 dakikadan büyük senkronizasyon boşluğu yok')
    return max(gaps,key=lambda x:x[1])

def outcome(signal,entry,stop,target,now):
    end=min(int(signal['expiry_time']),now//ONE_MIN*ONE_MIN-ONE_MIN)
    mins=market.recent_minutes(signal['symbol'],int(signal['entry_time']),end)
    qty=NOTIONAL/entry; entry_fee=FEE_RATE*NOTIONAL
    funding_events=market.funding(signal['symbol'],int(signal['entry_time']),end+ONE_MIN)
    last_funding=0.0
    for m in mins.itertuples(index=False):
        t=int(m.open_time)
        fund=NOTIONAL*sum(rate for ft,rate in funding_events if ft<t+ONE_MIN);last_funding=fund
        liq=(MARGIN-entry_fee+NOTIONAL+fund)/(qty*(1+MMR))
        reason=None;price=float(m.close)
        if m.open>=liq: reason,price='approx_liquidation',float(m.open)
        elif m.open>=stop: reason,price='stop_gap',float(m.open)*(1+SLIPPAGE)
        elif stop<liq and m.high>=stop: reason,price='stop',stop*(1+SLIPPAGE)
        elif m.high>=liq: reason,price='approx_liquidation',liq
        elif m.high>=stop: reason,price='stop',stop*(1+SLIPPAGE)
        elif m.low<=target: reason,price='target',target*(1+SLIPPAGE)
        elif t+ONE_MIN>=int(signal['expiry_time']): reason,price='timeout',float(m.close)*(1+SLIPPAGE)
        if reason:
            pnl=qty*(entry-price)-FEE_RATE*qty*(entry+price)+fund
            if reason=='approx_liquidation' or pnl < -MARGIN:
                pnl=max(-MARGIN,pnl);reason='approx_liquidation' if reason=='approx_liquidation' else 'margin_exhausted_proxy'
            return {'status':'closed','exit_time':t,'exit_price':price,'exit_reason':reason,
                    'funding_usdt':fund,'net_pnl_usdt':pnl,'mark_price':price,'mark_time':t,'last_checked':t}
    mark=float(mins.close.iloc[-1]) if len(mins) else entry
    return {'status':'open','exit_time':None,'exit_price':None,'exit_reason':None,
            'funding_usdt':last_funding,'net_pnl_usdt':None,'mark_price':mark,
            'mark_time':int(mins.open_time.iloc[-1]) if len(mins) else int(signal['entry_time']),
            'last_checked':int(mins.open_time.iloc[-1]) if len(mins) else int(signal['entry_time'])-ONE_MIN}

def main():
    now=market.server_time();gap_start,gap_end,gap_ms=latest_gap(now)
    universe=json.loads(UNIVERSE.read_text(encoding='utf-8'));model=joblib.load(MODEL)
    symbols=sorted(set(universe['context_symbols'])|{'BTCUSDT'})
    accepted=[];audit=[]
    for tf,duration in [('1h',ONE_HOUR),('4h',FOUR_HOURS)]:
        frames,errors=market.batch_bars(symbols,tf)
        print(tf,'frames',len(frames),'errors',len(errors),flush=True)
        candidates=[];backtest.NOW=now+200*FOUR_HOURS
        for symbol in universe['scan_symbols']:
            bars=frames.get(symbol)
            if bars is None or len(bars)<430: continue
            try:
                candidates.extend(s for s in backtest.signals(bars.iloc[-1000:].copy(),symbol,tf)
                                  if gap_start < int(s['entry_time']) < gap_end and
                                  (int(s['pattern_id']),s['variant']) in SELECTED)
            except Exception: pass
        print(tf,'selected candidates',len(candidates),flush=True)
        cache={}
        for i,signal in enumerate(sorted(candidates,key=lambda x:x['entry_time']),1):
            entry_ms=int(signal['entry_time']);row={'symbol':signal['symbol'],'timeframe':tf,
                 'pattern_id':int(signal['pattern_id']),'variant':signal['variant'],'entry_time':entry_ms}
            try:
                if entry_ms not in cache:
                    cut={s:f[f.open_time<=entry_ms].reset_index(drop=True) for s,f in frames.items()}
                    cache[entry_ms]=(cut,live_features.market_context(cut,tf,entry_ms))
                cut,context=cache[entry_ms]
                x,feat,entry,stop=live_features.make_features(signal,cut,context,model['features'])
                score=float(model['model'].predict(x)[0]);target=entry-2*(stop-entry)
                passes={'btc':context[200]['BTCUSDT']<=BTC_RETURN_200_MAX,
                        'liquidity':feat['avg_trade_usdt']>=AVG_TRADE_USDT_MIN,
                        'v4':score>=MODEL_THRESHOLD,'risk':stop>entry and target>0}
                row.update(score=score,avg_trade_usdt=feat['avg_trade_usdt'],passes=passes)
                if all(passes.values()):
                    result=outcome(signal,entry,stop,target,now)
                    key=f"backfill|{signal['symbol']}|{signal['pattern_id']}|{signal['variant']}|{entry_ms}"
                    pos={'id':hashlib.sha256(key.encode()).hexdigest()[:24],'symbol':signal['symbol'],
                         'strategy':f"{signal['pattern_id']}/{signal['variant']}",
                         'pattern_id':str(signal['pattern_id']),'timeframe':tf,'variant':signal['variant'],
                         'entry_time':entry_ms,'entry_price':entry,'stop':stop,'target':target,
                         'expiry_time':int(signal['expiry_time']),'quantity':NOTIONAL/entry,
                         'margin':MARGIN,'score':score,'created_at':entry_ms,**result}
                    accepted.append(pos);row['outcome']=result
            except Exception as exc: row['error']=str(exc)
            audit.append(row)
            if i%10==0: print(tf,'evaluated',i,'/',len(candidates),flush=True)

    # Apply live portfolio rules in chronological order, using computed exit times.
    chosen=[];suppressed=[]
    for p in sorted(accepted,key=lambda x:(x['entry_time'],-x['score'])):
        active=[x for x in chosen if x['entry_time']<=p['entry_time'] and
                (x['status']=='open' or x['exit_time']>p['entry_time'])]
        if len(active)>=MAX_POSITIONS or any(x['symbol']==p['symbol'] for x in active):
            suppressed.append(p);continue
        chosen.append(p)
    with sqlite3.connect(DB) as db:
        cols=['id','symbol','strategy','pattern_id','timeframe','variant','entry_time','entry_price','stop','target','expiry_time','quantity','margin','score','status','exit_time','exit_price','exit_reason','funding_usdt','net_pnl_usdt','mark_price','mark_time','last_checked','created_at']
        for p in chosen:
            db.execute(f"insert or ignore into positions ({','.join(cols)}) values ({','.join('?' for _ in cols)})",[p[c] for c in cols])
        payload={'gap_start':iso(gap_start),'gap_end':iso(gap_end),'gap_hours':gap_ms/3600000,
                 'candidate_count':len(audit),'passed_count':len(accepted),'inserted_count':len(chosen),
                 'suppressed_count':len(suppressed),'closed':sum(x['status']=='closed' for x in chosen),
                 'open':sum(x['status']=='open' for x in chosen),'net_closed':sum(x['net_pnl_usdt'] or 0 for x in chosen)}
        db.execute("insert into events(timestamp,kind,message,payload) values (?,?,?,?)",
                   (now,'backfill','Kapalı dönem backtest senkronizasyonu',json.dumps(payload,ensure_ascii=False)))
    store.snapshot(now)
    report={'gap_start':iso(gap_start),'gap_end':iso(gap_end),'generated_at':iso(now),
            'summary':payload,'inserted':chosen,'suppressed':suppressed,'audit':audit}
    Path(__file__).with_name('downtime_sync_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT='+json.dumps(payload,ensure_ascii=False),flush=True)

if __name__=='__main__': main()

from pathlib import Path
import numpy as np
import pandas as pd
import json
O=Path(__file__).resolve().parents[1]/'output/ml'
S=['price_ema20_gap_vs_prev5','rsi14_vs_prev5','adx14_vs_prev20','volume_rvol20_vs_prev20','trade_count_gap_20','btc_correlation_200','coin_return_percentile_50']
a=pd.read_parquet(O/'filtre_v3_features.parquet')
b=pd.read_parquet(O/'signal_features.parquet',columns=['event_id','variant']+S)
e=pd.read_parquet(O/'all_2x_labels.parquet',columns=['event_id','variant','entry_price','stop'])
d=a.merge(b,on=['event_id','variant'],validate='one_to_one').merge(e,on=['event_id','variant'],validate='one_to_one')
assert len(d)==len(a)==len(e)
risk=((d.stop/d.entry_price)-1).clip(lower=1e-6)
d['stop_gap_pct']=risk
d['log_avg_trade_usdt']=np.log1p(d.avg_trade_usdt.clip(lower=0))
d['btc_momentum_accel']=d.btc_return_20-d.btc_return_200*.1
d['btc_momentum_per_atr']=d.btc_return_20/d.atr14_pct.clip(lower=1e-4)
d['support_room_to_stop']=d.prior_low_20_distance_pct/risk
d['resistance_room_to_stop']=d.prior_high_20_distance_pct/risk
d['sell_pressure_liquidity']=(.5-d.taker_buy_share)*d.log_avg_trade_usdt
d['atr_times_rvol']=d.atr14_pct*d.volume_rvol20
d['funding_times_btc_trend']=d.funding_last_bps*d.btc_return_20
d=d.drop(columns=['entry_price','stop']).replace([np.inf,-np.inf],np.nan)
assert not d.duplicated(['event_id','variant']).any()
assert not set(d.columns)&{'net_pnl_usdt','exit_time','loss','accept'}
# d stays in memory to fit disk quota
new=S+['stop_gap_pct','log_avg_trade_usdt','btc_momentum_accel','btc_momentum_per_atr','support_room_to_stop','resistance_room_to_stop','sell_pressure_liquidity','atr_times_rvol','funding_times_btc_trend']
# manifest is written by evaluation script
print(len(d),len(new))


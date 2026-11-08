# V4 Makine Öğrenmesi Filtresi

## Amaç

V4 yeni sinyal üretmez; 12 formasyon algoritmasının ürettiği adayları kaliteye göre süzer. Model çıktısı olasılık değil, **2× kaldıraç ve 10 USDT teminat altında beklenen net PnL (USDT)** tahminidir. Paper eşiği `score >= 0.26` olarak dondurulmuştur.

## Eğitim seti

- Evren: araştırma anında aktif 525 Binance USD-M perpetual; yalnızca coinin gerçekten listeli olduğu tarih aralığı.
- Seçilen model penceresi: `2020-01-01 UTC`–`2024-12-31 UTC`.
- Eğitim satırı: `11.286` sinyal/varyant.
- Eğitimde temsil edilen coin: `234`.
- Target: 2× izole kaldıraç, işlem başına en fazla 10 USDT teminat; 1m Futures trade/mark mumlarıyla çözülen, fee + slippage + funding sonrası `net_pnl_usdt`.
- Eğitim döneminde açılıp dönem sonrasına taşan sonuçlar sızıntı yaratmaması için eğitim bitişinden önce kapanmış olmalıdır.
- 2025–2026 model eğitiminde kullanılmaz; out-of-sample test dönemidir.

## Model

`LightGBM LGBMRegressor`:

```text
n_estimators       160
learning_rate      0.035
num_leaves         7
max_depth          4
min_child_samples  120
reg_lambda         15
colsample_bytree   0.8
random_state       20261011
n_jobs             2
```

Kategorik featurelar: `variant`, `pattern_id`, `timeframe`.

## 59 feature

### Kimlik ve formasyon geometrisi

`variant`, `pattern_id`, `timeframe`, `pivot_width`, `pattern_age_bars`, `trigger_gap_pct`, `raw_stop_gap_atr`, `pivot_range_atr`, `abcd_cd_ab`.

### Fiyat/trend/momentum

`price_ema20_gap`, `price_ema200_gap`, `rsi14`, `rsi14_vs_prev20`, `adx14`, `atr14_pct`, `atr14_pct_vs_prev50`, `body_range`, `close_location`.

### Hacim ve işlem akışı

`volume_rvol20`, `volume_rvol20_vs_prev5`, `taker_buy_share`, `avg_trade_usdt`, `trade_count_gap_20`, `taker_sell_pressure_rvol`, `bearish_candle_share20`, `range_compression5_20`.

### Destek/direnç mesafeleri

`prior_high_20_distance_pct`, `prior_low_20_distance_pct`, `confirmed_pivot_high_distance_pct`, `confirmed_pivot_low_distance_pct`, `resistance_distance_atr20`.

### BTC ve piyasa bağlamı

`relative_return_btc_5`, `relative_return_btc_20`, `relative_return_btc_200`, `btc_return_20`, `btc_return_200`, `btc_correlation_50`, `btc_correlation_200`, `market_breadth_vs_btc_20`, `market_breadth_vs_btc_200`, `market_breadth_change_5_20`, `coin_return_percentile_20`, `coin_return_percentile_50`.

### Funding

`funding_last_bps`, `funding_mean8_bps`, `funding_vs_btc_bps`.

### Anlık değerin geçmiş ortalamaya göre konumu

`price_ema20_gap_vs_prev5`, `rsi14_vs_prev5`, `adx14_vs_prev20`, `volume_rvol20_vs_prev20`.

### V4 türetilmiş etkileşimler

`stop_gap_pct`, `log_avg_trade_usdt`, `btc_momentum_accel`, `btc_momentum_per_atr`, `support_room_to_stop`, `resistance_room_to_stop`, `sell_pressure_liquidity`, `atr_times_rvol`, `funding_times_btc_trend`.

## Point-in-time kuralı

Tüm featurelar giriş kararından önce tamamen kapanmış mumlardan hesaplanır. Piyasa breadth ve coin yüzdelikleri aynı timestamp’te mevcut coinlerden çıkarılır. Giriş mumunun gelecekteki high/low/close bilgisi feature değildir. Bu kural `paper/live_features.py` ve `src/build_active_525_v4_features.py` içinde uygulanır.

## Eğitimi tekrarlama

1. Sinyal defterini 12 yöntemle üret.
2. Her sinyali 1m trade/mark ve funding ile 2× senaryoda etiketle.
3. Giriş anındaki 59 point-in-time featureı üret.
4. Eğitim penceresindeki ve pencere bitmeden kapanan satırları seç.
5. Yukarıdaki sabit LightGBM parametreleriyle regresyonu fit et.
6. Modeli `{model, features, categorical, train_start, train_end}` sözlüğü olarak joblib ile kaydet.
7. Eğitim dışı 2025–2026 döneminde `score >= 0.26` filtresini değerlendir.

Ana dondurulmuş dosya: `models/v4_2020_2024.joblib`.


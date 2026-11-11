# Üç Short Metodu Sıfırdan Kurma Rehberi

Bu belge, projeyi ve kullanılan yöntemleri hiç bilmeyen bir geliştiricinin aşağıdaki üç short sistemini sıfırdan kurabilmesi, V4 filtresini yeniden eğitebilmesi ve 1 dakikalık Futures verisiyle backtest edebilmesi için hazırlanmıştır.

1. Alternate AB=CD · 4h · `close_2r_h100`
2. Simetrik üçgen · 4h · `close_2r`
3. Yatay dikdörtgen · 1h · `close_2r_h25`

Belge yalnızca teknik yeniden üretim tarifidir; yatırım tavsiyesi değildir.

---

## 1. Sistemin genel akışı

Her coin ve uygun timeframe için işlem sırası şöyledir:

1. Yalnızca tamamen kapanmış mumları al.
2. Veri boşluklarını ayır; boşluğun iki tarafındaki mumlarla tek formasyon kurma.
3. ATR(14) hesapla.
4. Sağ ve sol tarafı kapanmış pivotları tespit et.
5. Üç formasyondan birini ara.
6. Formasyonun bilindiği anda tetik ve ham stop seviyelerini sabitle.
7. Tetik seviyesinin altında kapanış bekle.
8. Aşama 2 BTC ve likidite filtrelerini uygula.
9. Giriş anındaki 59 featureı üret ve V4 skorunu hesapla.
10. V4 skoru en az `0.26 USDT` ise sinyali kabul et.
11. İsteniyorsa Aşama 4 ve Aşama 5 filtrelerini uygula.
12. Sonraki ana timeframe mumunun açılışında short giriş fiyatını belirle.
13. Doğal stop ve 2R hedefini sabitle.
14. İşlemi 1 dakikalık trade ve mark-price mumlarıyla kronolojik çöz.
15. Fee, slippage ve funding sonrası net PnL kaydet.

V4 yeni sinyal üretmez. Önce geometrik formasyon sinyali oluşur; V4 yalnızca bu sinyali kabul veya reddetmek için kalite puanı üretir.

---

## 2. Zaman ve veri kuralları

### 2.1 UTC kullan

Bütün timestamp alanları UTC epoch milisaniye olmalıdır. Lokal saat ile mum birleştirme yapılmamalıdır.

- 1h mum süresi: `3_600_000 ms`
- 4h mum süresi: `14_400_000 ms`
- 1m mum süresi: `60_000 ms`

### 2.2 Kapanmamış mum kullanma

Karar timestamp'i `T` ise yalnızca şu koşulu sağlayan ana timeframe mumları kullanılabilir:

```text
open_time + timeframe_duration <= T
```

Giriş için kullanılacak mumun open/high/low/close değerleri sinyal featurelarına karıştırılmamalıdır. Giriş fiyatı sonraki mumun açılışıdır.

### 2.3 Kesintisiz segment

Ardışık iki mum arasında beklenen timeframe süresinden farklı bir boşluk varsa yeni segment başlat:

```text
segment[i] = segment[i-1] + (open_time[i] - open_time[i-1] != duration)
```

Pivot, ATR, formasyon ve rolling featurelar tek bir kesintisiz segment içinde hesaplanmalıdır. Paper feature üreticisi güvenli ısınma için sinyalden önce en az 420 kesintisiz kapanmış mum ister.

### 2.4 Ham ana timeframe veri şeması

Her coin ve BTC için en az şu sütunlar gerekir:

| Alan | Açıklama |
|---|---|
| `open_time` | UTC epoch ms |
| `open` | Açılış fiyatı |
| `high` | En yüksek fiyat |
| `low` | En düşük fiyat |
| `close` | Kapanış fiyatı |
| `volume` | Base-asset hacmi |
| `quote_volume` | USDT cinsinden toplam hacim |
| `trade_count` | Mum içindeki işlem sayısı |
| `taker_buy_quote` | Taker alışlarının USDT hacmi |

### 2.5 1m backtest verileri

Çıkış çözümlemesi için iki ayrı veri serisi gerekir:

- Futures trade 1m: open/high/low/close
- Futures mark-price 1m: open/high/low/close

Funding için:

| Alan | Açıklama |
|---|---|
| `fundingTime` | UTC epoch ms |
| `fundingRate` | Ondalık funding oranı; örneğin `0.0001` |

---

## 3. Ortak indikatör ve pivot motoru

### 3.1 ATR(14)

Bu sistem Wilder EMA değil, 14 dönemlik basit ortalama kullanır.

```text
TR[i] = max(
    high[i] - low[i],
    abs(high[i] - close[i-1]),
    abs(low[i] - close[i-1])
)

ATR14[i] = mean(TR[i-13 : i])
```

Yeni segmentin ilk mumunda TR yalnızca `high-low` olarak alınır.

### 3.2 Teyit edilmiş pivot

Pivot genişlikleri ayrı ayrı denenir:

```text
width ∈ {3, 6, 12}
```

`i` mumunun pivot tepe olması için:

```text
high[i] == max(high[i-width : i+width])
```

ve bu pencere içinde aynı maksimum yalnızca bir kez görülmelidir.

Pivot dip için:

```text
low[i] == min(low[i-width : i+width])
```

ve minimum benzersiz olmalıdır.

Önemli zamanlama:

```text
pivotun oluştuğu indeks = i
pivotun bilindiği indeks = i + width
```

Dolayısıyla sağdaki `width` mum kapanmadan pivot kullanılamaz. Bu kural geleceği görmeyi engeller.

Her pivot şu yapıda tutulabilir:

```text
(pivot_index, known_index, type, price)
```

`type` değeri tepe için `H`, dip için `L` olmalıdır.

### 3.3 Trend çizgisi

Üç tepe ve üç dip için ayrı en küçük kareler doğrusu hesaplanır:

```text
price = slope * bar_index + intercept
```

Ayrıca pivotların kendi çizgilerine maksimum mutlak uzaklığı hesaplanır. Formasyon kabulü için bu hata en fazla `0.5 × ATR[known]` olmalıdır.

---

## 4. Metot 1 — Alternate AB=CD · 4h · close_2r_h100

### 4.1 Zaman dilimi ve kimlik

```text
pattern_id = 65
variant    = close_2r_h100
timeframe  = 4h
horizon    = 100 adet 4h mum = 400 saat
side       = short
```

### 4.2 Pivot sırası

Aynı genişlikte üretilen son dört pivot kesinlikle şu sırada olmalıdır:

```text
L → H → L → H
A   B   C   D
```

Fiyat mesafeleri:

```text
AB = B - A
BC = B - C
CD = D - C
```

Şunların hepsi pozitif olmalıdır:

```text
AB > 0
BC > 0
CD > 0
C > A
```

### 4.3 Oran koşulları

Kesin kod sınırları:

```text
0.382 * 0.95 <= BC/AB <= 0.886 * 1.05
1.13  * 0.95 <= CD/BC <= 2.618 * 1.05
```

Ek olarak `CD/AB`, `1.27` veya `1.618` uzatmalarından birine yaklaşmalıdır. Bir oran `r` için iki koşul birlikte aranır:

```text
abs(CD/AB - r) <= 0.05 * r
abs(CD - r*AB) <= 0.5 * ATR[known]
```

### 4.4 Yapının bilindiği an

Yapı D mumunda değil, D tepesinin sağ tarafındaki teyit mumları kapandıktan sonra bilinir:

```text
known_index = D pivotunun known_index değeri
```

Formasyonun seviyeleri o anda sabitlenir:

```text
trigger  = low[known_index]
raw_stop = D pivot fiyatı
```

Trigger daha sonraki mumlarda değiştirilmez.

### 4.5 Kırılım teyidi

`known_index + 1` sonrasındaki en fazla 10 adet 4h mum taranır.

Her aday mumda önce iptal kontrolü:

```text
high[q] > raw_stop + 0.25 * ATR[known_index]
```

olursa yapı iptal edilir ve daha sonraki kırılım beklenmez.

İlk şu koşulu sağlayan mum kırılım teyididir:

```text
close[q] < trigger
ATR[q] mevcut ve sonlu
```

Yalnızca fitilin trigger altına inmesi yeterli değildir.

### 4.6 Giriş, stop, hedef ve timeout

```text
entry_index = q + 1
reference_entry = open[entry_index]
short_entry = reference_entry * (1 - slippage)
stop = raw_stop + 0.25 * ATR[q]
risk = stop - short_entry
target = short_entry - 2 * risk
expiry = entry_time + 100 * 4h
```

`risk <= 0` veya `target <= 0` ise işlem reddedilir.

---

## 5. Metot 2 — Simetrik üçgen · 4h · close_2r

### 5.1 Zaman dilimi ve kimlik

```text
pattern_id = 9
variant    = close_2r
timeframe  = 4h
horizon    = 50 adet 4h mum = 200 saat
side       = short
```

### 5.2 Pivot dizilimi

Aynı pivot genişliğinde son altı pivot kullanılmalıdır. Pivot tipleri sırayla değişmelidir; iki ardışık tepe veya iki ardışık dip kabul edilmez.

Son altı pivot içinden:

- Üç `H` pivotuna üst doğru,
- Üç `L` pivotuna alt doğru

uydurulur.

Tanımlar:

```text
x0 = ilk pivot indeksi
x1 = son pivot indeksi
span = x1 - x0

top0 = upper_slope*x0 + upper_intercept
top1 = upper_slope*x1 + upper_intercept
bot0 = lower_slope*x0 + lower_intercept
bot1 = lower_slope*x1 + lower_intercept

width0 = top0 - bot0
width1 = top1 - bot1
```

### 5.3 Geometri koşulları

Yapının bilindiği andaki `ATR = ATR[known]` kullanılır.

```text
max(upper_line_error, lower_line_error) <= 0.5 * ATR
min(width0, width1) >= 1.0 * ATR
upper_slope * span < -0.5 * ATR
lower_slope * span >  0.5 * ATR
width0 - width1 > 0.5 * ATR
```

Bu kurallar sırasıyla pivotların çizgiye yakın olmasını, yeterli yapı genişliğini, daha alçak tepeleri, daha yüksek dipleri ve daralmayı zorunlu kılar.

### 5.4 Sabitlenen seviyeler

```text
trigger = lower_slope * known_index + lower_intercept
raw_stop = son teyit edilmiş H pivotunun fiyatı
```

Alt çizgi teorik olarak ileri uzatılmaz; tespit anındaki trigger sabit tutulur.

### 5.5 Kırılım teyidi

Yapının bilinmesinden sonra en fazla 20 adet 4h mum aranır.

İptal koşulu:

```text
high[q] > raw_stop + 0.25 * ATR[known]
```

Kırılım teyidi:

```text
close[q] < trigger
```

### 5.6 Giriş ve çıkış seviyeleri

```text
entry_index = q + 1
short_entry = open[entry_index] * (1 - slippage)
stop = raw_stop + 0.25 * ATR[q]
risk = stop - short_entry
target = short_entry - 2 * risk
expiry = entry_time + 50 * 4h
```

Üçgen yüksekliği hedef olarak kullanılmaz; hedef yalnızca 2R'dir.

---

## 6. Metot 3 — Yatay dikdörtgen · 1h · close_2r_h25

### 6.1 Zaman dilimi ve kimlik

```text
pattern_id = 16
variant    = close_2r_h25
timeframe  = 1h
horizon    = 25 adet 1h mum = 25 saat
side       = short
```

### 6.2 Pivot ve çizgiler

Simetrik üçgen gibi son altı dönüş noktası kullanılır:

- Üç tepe,
- Üç dip,
- Pivot tipleri dönüşümlü.

Tepelere ve diplere ayrı doğrular uydurulur. `x0`, `x1`, `span`, `width0` ve `width1` üçgen bölümündeki şekilde hesaplanır.

### 6.3 Geometri koşulları

```text
max(upper_line_error, lower_line_error) <= 0.5 * ATR[known]
min(width0, width1) >= 1.0 * ATR[known]
abs(upper_slope * span) <= 0.5 * ATR[known]
abs(lower_slope * span) <= 0.5 * ATR[known]
```

Yani hem direnç hem destek çizgisi ATR'ye göre yatay olmalıdır.

### 6.4 Tetik ve stop

```text
trigger = lower_slope * known_index + lower_intercept
raw_stop = son teyit edilmiş H pivotunun fiyatı
```

### 6.5 Kırılım, giriş ve çıkış

Yapı bilindikten sonraki en fazla 20 adet 1h mumda:

```text
close[q] < trigger
```

aranır. Teyitten önce fiyat şu seviyeyi aşarsa aday iptal edilir:

```text
high[q] > raw_stop + 0.25 * ATR[known]
```

İşlem seviyeleri:

```text
entry_index = q + 1
short_entry = open[entry_index] * (1 - slippage)
stop = raw_stop + 0.25 * ATR[q]
risk = stop - short_entry
target = short_entry - 2 * risk
expiry = entry_time + 25 * 1h
```

---

## 7. Aynı yapının tekrar sayılmasını önleme

Her yapı için deterministik bir anahtar üret:

```text
AB=CD: (pattern_id, known_index, round(trigger, 10))
Çizgisel yapılar: (pattern_id, known_index, round(trigger, 10))
```

Pivot genişlikleri 3, 6 ve 12 aynı yapıyı üretirse aynı anahtardaki son/tek kayıt kullanılmalıdır. Aynı sinyali farklı varyantlarla test etmek isteniyorsa varyantlar ayrı kayıttır; bu üç-metot sisteminde yalnızca yukarıdaki üç varyant tutulur.

---

## 8. Kümülatif filtreler: 1, 12, 123, 1234, 12345

Filtreler bağımsız isimler değil, soldan sağa kümülatiftir.

### Aşama 1 — Formasyon

Yukarıdaki üç algoritmadan biri geçerli short sinyali üretmiştir.

### Aşama 2 — BTC rejimi ve likidite

Aşama 1'e ek olarak iki koşul birlikte aranır.

#### BTC filtresi

Sinyalin kendi timeframe'inde BTC'nin son 200 kapanmış mum getirisi:

```text
btc_return_200 = BTC_close[-1] / BTC_close[-201] - 1
```

Kabul:

```text
btc_return_200 <= 0.073452727496624
```

Yani BTC son 200 mumda yaklaşık `%7.345` değerinden daha güçlü yükselmişse short sinyal reddedilir.

#### Likidite filtresi

Sinyal kararından önceki son tamamen kapanmış coin mumunda:

```text
avg_trade_usdt = quote_volume / trade_count
```

Kabul:

```text
avg_trade_usdt >= 147.6241108493176
```

`trade_count <= 0` veya `quote_volume <= 0` ise feature geçersizdir; sıfırla doldurulmaz.

Bu eşik model araştırmasından türetilmiş sabit bir eşiktir. Canlıda zamanla dağılım kayması açısından izlenmelidir.

### Aşama 3 — V4 skoru

Aşama 1 ve 2'ye ek olarak:

```text
V4_predict(features) >= 0.26 USDT
```

V4 çıktısı olasılık değildir. Çıktı, eğitim tanımında 2x kaldıraç ve en fazla 10 USDT izole teminat için tahmin edilen net PnL'dir.

### Aşama 4 — 200 mum trendi

Aşama 1–3'e ek olarak coin kendi timeframe'inde son 200 mumda düşmüş olmalıdır:

```text
coin_return_200 = close[-1] / close[-201] - 1
coin_return_200 < 0
```

### Aşama 5 — Kırmızı ve alt yüzde 40 kapanış

Aşama 1–4'e ek olarak son kapanmış sinyal mumu:

```text
close < open
close_location = (close - low) / (high - low)
close_location <= 0.40
```

`high == low` ise oran tanımsızdır ve sinyal reddedilmelidir.

Bu proje için denetimde öne çıkan aday Aşama `123` idi. Aşama 4 ve 5 ayrıca test edilebilir; otomatik olarak daha iyi oldukları varsayılmamalıdır.

---

## 9. V4 için gerekli 59 feature

### Kimlik ve formasyon geometrisi

```text
variant
pattern_id
timeframe
pivot_width
pattern_age_bars
trigger_gap_pct
raw_stop_gap_atr
pivot_range_atr
abcd_cd_ab
```

AB=CD dışındaki yapılarda `abcd_cd_ab` eğitim hattındaki tanımlı eksik-değer politikasına göre `NaN` kalabilir. Rastgele sıfırla doldurulmamalıdır.

### Fiyat, trend ve momentum

```text
price_ema20_gap
price_ema200_gap
rsi14
rsi14_vs_prev20
adx14
atr14_pct
atr14_pct_vs_prev50
body_range
close_location
```

### Hacim ve işlem akışı

```text
volume_rvol20
volume_rvol20_vs_prev5
taker_buy_share
avg_trade_usdt
trade_count_gap_20
taker_sell_pressure_rvol
bearish_candle_share20
range_compression5_20
```

Temel tanımlardan bazıları:

```text
taker_buy_share = taker_buy_quote / quote_volume
avg_trade_usdt = quote_volume / trade_count
trade_count_gap_20 = current_trade_count / mean(previous_20_trade_count) - 1
taker_sell_pressure_rvol = (0.5 - taker_buy_share) * volume_rvol20
```

### Destek ve direnç uzaklıkları

```text
prior_high_20_distance_pct
prior_low_20_distance_pct
confirmed_pivot_high_distance_pct
confirmed_pivot_low_distance_pct
resistance_distance_atr20
```

```text
resistance_distance_atr20 = prior_high_20_distance_pct / max(atr14_pct, epsilon)
```

### BTC ve çapraz piyasa bağlamı

```text
relative_return_btc_5
relative_return_btc_20
relative_return_btc_200
btc_return_20
btc_return_200
btc_correlation_50
btc_correlation_200
market_breadth_vs_btc_20
market_breadth_vs_btc_200
market_breadth_change_5_20
coin_return_percentile_20
coin_return_percentile_50
```

Her lookback `n` için aynı timestamp'te piyasadaki coinlerin getirisi hesaplanır:

```text
return_n(symbol) = close[-1] / close[-n-1] - 1
market_breadth_vs_btc_n =
    count(other_coin_return_n > btc_return_n) / available_other_coin_count

coin_return_percentile_n =
    count(market_return_n < this_coin_return_n) / available_coin_count
```

En az 50 coinlik geçerli 20-mum bağlamı yoksa satır modele verilmez.

### Funding

```text
funding_last_bps
funding_mean8_bps
funding_vs_btc_bps
```

```text
funding_last_bps = last_coin_funding_rate * 10_000
funding_mean8_bps = mean(last_8_coin_funding_rates) * 10_000
funding_vs_btc_bps = funding_last_bps - last_btc_funding_rate*10_000
```

### Anlık değerin geçmiş ortalamasına göre konumu

```text
price_ema20_gap_vs_prev5
rsi14_vs_prev5
adx14_vs_prev20
volume_rvol20_vs_prev20
```

### V4 etkileşimleri

```text
stop_gap_pct
log_avg_trade_usdt
btc_momentum_accel
btc_momentum_per_atr
support_room_to_stop
resistance_room_to_stop
sell_pressure_liquidity
atr_times_rvol
funding_times_btc_trend
```

Tanımlar:

```text
stop_gap_pct = max(stop / entry_price - 1, 1e-6)
log_avg_trade_usdt = log1p(max(avg_trade_usdt, 0))
btc_momentum_accel = btc_return_20 - 0.1 * btc_return_200
btc_momentum_per_atr = btc_return_20 / max(atr14_pct, 1e-4)
support_room_to_stop = prior_low_20_distance_pct / stop_gap_pct
resistance_room_to_stop = prior_high_20_distance_pct / stop_gap_pct
sell_pressure_liquidity = (0.5-taker_buy_share) * log_avg_trade_usdt
atr_times_rvol = atr14_pct * volume_rvol20
funding_times_btc_trend = funding_last_bps * btc_return_20
```

Model feature isimleri ve sırası model dosyasındaki `features` manifestiyle birebir aynı olmalıdır.

---

## 10. V4 eğitim seti ve model

### 10.1 Eğitim satırı

Bir eğitim satırı bir formasyon-varyant sinyalidir. Mevcut ana V4 yalnızca bu üç yöntemle değil, 12 varyantın tamamıyla eğitilmiştir. Üç yöntemi skorlayabilmesinin nedeni `pattern_id`, `variant` ve `timeframe` alanlarının kategorik feature olmasıdır.

Ana model özeti:

```text
Eğitim dönemi: 2020-01-01 UTC – 2024-12-31 UTC
Satır: 11,286 sinyal/varyant
Coin: 234
Feature: 59
Test dönemi: 2025–2026
```

2025 ve 2026 eğitim veya threshold seçimi için kullanılmamalıdır.

### 10.2 Target

Her eğitim sinyali 1m Futures trade/mark mumları ve funding ile 2x kaldıraçta çözülür.

```text
target = net_pnl_usdt
```

Target şunlardan sonradır:

- Fiyat hareketi PnL'i,
- Giriş komisyonu,
- Çıkış komisyonu,
- Giriş/çıkış slippage'i,
- İşlem açıkken gerçekleşen funding.

Eğitim döneminde açılıp 2025'e taşan işlem eğitim satırı olmamalıdır. İşlem eğitim penceresi bitmeden kapanmış olmalıdır.

### 10.3 Model

```python
LGBMRegressor(
    n_estimators=160,
    learning_rate=0.035,
    num_leaves=7,
    max_depth=4,
    min_child_samples=120,
    reg_lambda=15,
    colsample_bytree=0.8,
    random_state=20261011,
    n_jobs=2,
)
```

Kategorik sütunlar:

```text
variant, pattern_id, timeframe
```

Model paketi şu alanları içermelidir:

```text
model
features
categorical
train_start
train_end
```

Ana model dosyası:

```text
models/v4_2020_2024.joblib
```

### 10.4 Yalnızca üç yöntemle yeni model eğitilecekse

İki modeli karıştırmamak için ayrı isim kullanılmalıdır:

```text
V4-12M = mevcut, 12 varyantla eğitilmiş model
V4-3M  = yalnızca bu üç varyantla yeniden eğitilecek deneysel model
```

V4-3M eğitiminde satır sayısı azalacağı için aynı hiperparametrelerin otomatik olarak uygun olduğu varsayılmamalıdır. Threshold `0.26` da V4-12M'e aittir; V4-3M için eğitim dışı validasyonla yeniden seçilmelidir.

---

## 11. 1 dakikalık işlem çözümlemesi

### 11.1 Pozisyon büyüklüğü

Azami teminat `M = 10 USDT`, kaldıraç `L` ise teorik nominal:

```text
notional = M * L
quantity_raw = notional / entry_price
```

Quantity, kontratın Binance `stepSize` değerine aşağı yuvarlanır. Güncel `minQty`, `minNotional` ve `tickSize` kuralları denetlenmelidir.

### 11.2 Slippage

Mevcut backtest varsayımı:

```text
SLIP = 0.0002
```

Short giriş satışı aleyhe daha düşükten dolar:

```text
entry_fill = reference_open * (1 - SLIP)
```

Short kapama alışları aleyhe daha yüksekten dolar:

```text
exit_fill = reference_exit * (1 + SLIP)
```

### 11.3 Komisyon

Dashboard backtestindeki muhafazakâr varsayım:

```text
FEE = 0.001
entry_fee = FEE * quantity * entry_fill
exit_fee  = FEE * quantity * exit_fill
```

Fee teminat üzerinden değil kaldıraçlı nominal üzerinden hesaplanır.

### 11.4 Funding

Short pozisyon için mevcut işaret tanımı:

```text
funding_usdt = quantity * reference_notional_price *
               sum(fundingRate where entry_time <= fundingTime < exit_time)
```

Pozitif funding short için gelir, negatif funding giderdir. En doğru üretimde her funding timestamp'indeki mark fiyatı kullanılmalıdır; giriş fiyatını vekil kullanmak yaklaşık sonuç üretir.

### 11.5 Doğal stop ve likidasyon

Doğal formasyon stopu:

```text
natural_stop = raw_stop + 0.25 * ATR_at_confirmation
```

Güvenilirlik denetiminde önerilen kural:

> Doğal stop likidasyon güvenlik tamponunun dışında kalıyorsa stopu yapay biçimde daraltma; işlemi atla.

Bu yaklaşım geçmiş sonucu iyileştirmek için stopu kaldıraç seviyesine uydurma yanlılığını azaltır.

### 11.6 Mum içi olay sırası

Her 1m mum kronolojik taranır:

1. Mark price likidasyon/koruyucu sınırı geçmiş mi?
2. Mum açılışı stopun üzerinde mi? Öyleyse gap stop.
3. Trade high doğal stopa değmiş mi?
4. Trade low hedefe değmiş mi?
5. Hiçbiri değilse sonraki dakikaya geç.
6. Horizon biterse timeout kapanışı yap.

Aynı 1m mumda hem stop hem hedef görünürse mum içi sıra bilinmez. Muhafazakâr ana sonuçta stop önce sayılır ve kayıt `same_1m_tp_stop_touch` bayrağı alır.

### 11.7 Net PnL

Short için:

```text
gross_price_pnl = quantity * (entry_fill - exit_fill)
net_pnl_usdt = gross_price_pnl
               - entry_fee
               - exit_fee
               + funding_usdt
```

Backtest motorunda toplam zarar teminatı aşmayacak şekilde ayrıca sınırlandırılmış olabilir. Gerçek borsada likidasyon ücreti ve gap kaybı bu sınırı değiştirebileceği için bu varsayım açıkça raporlanmalıdır.

---

## 12. Ana pseudocode

```python
for symbol in universe:
    for timeframe in ["1h", "4h"]:
        bars = load_closed_futures_bars(symbol, timeframe)
        bars = split_on_gaps(bars)
        bars = calculate_atr14(bars)

        for width in [3, 6, 12]:
            confirmed_pivots = find_confirmed_pivots(bars, width)

            if timeframe == "4h":
                candidates += detect_abcd(confirmed_pivots, bars)
                candidates += detect_symmetric_triangle(confirmed_pivots, bars)

            if timeframe == "1h":
                candidates += detect_rectangle(confirmed_pivots, bars)

        candidates = deduplicate(candidates)

        for pattern in candidates:
            confirmation = find_first_close_below_frozen_trigger(pattern)
            if confirmation is None:
                continue

            signal = build_signal(pattern, confirmation)
            if signal.variant not in THREE_SELECTED_VARIANTS:
                continue

            # Aşama 2
            if btc_return_200(signal.timeframe, signal.entry_time) > BTC_MAX:
                continue
            if avg_trade_usdt(last_closed_signal_candle) < LIQUIDITY_MIN:
                continue

            # Aşama 3
            features = make_59_point_in_time_features(signal, market_state)
            score = v4_model.predict(features)
            if score < 0.26:
                continue

            # İsteğe bağlı Aşama 4 ve 5
            # if coin_return_200 >= 0: continue
            # if not bearish_lower_40_percent_close: continue

            trade = simulate_with_1m_trade_mark_funding(signal)
            save(trade)
```

---

## 13. Kaydedilmesi gereken işlem alanları

Her aday ve işlem için en az:

```text
trade_id
symbol
pattern_id
variant
timeframe
pivot_width
pivot_times
pivot_prices
known_time
trigger
raw_stop
confirm_time
atr_at_confirm
entry_time
entry_price
natural_stop
target
expiry_time
exit_time
exit_price
exit_reason
leverage
quantity
used_margin_usdt
gross_price_pnl_usdt
entry_fee_usdt
exit_fee_usdt
funding_usdt
net_pnl_usdt
v4_score
highest_stage
liquidation_risk_flag
same_1m_tp_stop_touch
data_quality_status
```

Reddedilen sinyaller silinmemeli; `reject_reason` ile saklanmalıdır. Örnek nedenler:

```text
missing_entry_1m
data_gap_before_exit
missing_funding
invalid_risk_or_target
net_reward_risk_below_1_5
liquidation_buffer_violation
missing_market_context
missing_model_feature
```

---

## 14. Look-ahead ve seçim yanlılığı kontrolleri

1. Pivot, sağ teyit mumları kapanmadan kullanılmamalı.
2. Trigger yapı bilindiği anda sabitlenmeli; gelecekte çizgi güncellenmemeli.
3. Featurelar girişten önce kapanmış mumlarla hesaplanmalı.
4. 2025–2026 verisi V4-2020–2024 eğitiminde kullanılmamalı.
5. Threshold test sonucuna bakarak sürekli değiştirilmemeli.
6. Bugün aktif 525 coinle geçmişe gitmenin survivor bias yarattığı raporlanmalı.
7. Delist olmuş coinlerin dışarıda kalması sonucu olduğundan iyi gösterebilir.
8. Hacme göre ilk N coin geçmiş dönemin gelecekte bilinen ortalama hacmiyle seçilmemeli. Canlıya benzer testte sıralama yalnızca o tarihten önceki 30 günle yapılmalı.
9. Aynı 1m mumda TP ve SL temasları muhafazakâr çözülmeli.
10. Eksik funding veya eksik mark verisi sıfır kabul edilmemeli.
11. Aynı formasyonun farklı pivot genişliklerinden gelen kopyaları temizlenmeli.
12. Eğitim satırı ile test satırı `trade_id` veya aynı sinyal üzerinden çakışmamalı.

---

## 15. Birebir yeniden üretim kontrol listesi

- [ ] Zamanlar UTC epoch ms.
- [ ] Yalnızca kapanmış mumlar kullanılıyor.
- [ ] Mum boşlukları segmentleri ayırıyor.
- [ ] ATR, 14 TR'nin basit ortalaması.
- [ ] Pivot genişlikleri 3, 6, 12.
- [ ] Pivot `i+width` anında biliniyor.
- [ ] AB=CD oran ve ATR yakınsama kuralları birebir.
- [ ] Üçgen ve dikdörtgen çizgi toleransları ATR tabanlı.
- [ ] Trigger tespit anında donduruluyor.
- [ ] AB=CD teyit araması 10, diğerleri 20 mum.
- [ ] Giriş teyitten sonraki mum açılışı.
- [ ] Stop `raw_stop + 0.25 ATR[q]`.
- [ ] Hedef 2R.
- [ ] Horizonlar 100×4h, 50×4h ve 25×1h.
- [ ] BTC ve likidite filtresi doğru timestamp'te.
- [ ] Model tam 59 featureı manifest sırasıyla alıyor.
- [ ] 2020–2024 train; 2025–2026 yalnızca test.
- [ ] 1m trade ve mark verisi ayrı kullanılıyor.
- [ ] Funding gerçek timestamp aralığında.
- [ ] Fee kaldıraçlı nominal üzerinden.
- [ ] Aynı dakika TP+SL için stop önce.
- [ ] Doğal stop likidasyon tamponuna uymuyorsa işlem atlanıyor.
- [ ] Her reddetme ve veri problemi kayıt altında.

---

## 16. Projedeki referans uygulamalar

```text
src/backtest.py
src/build_active_525_v4_features.py
src/backtest_active_525_five_stage.py
paper/live_features.py
models/v4_2020_2024.joblib
docs/ML_V4.md
docs/METHODS_TR.md
```

Bu dosyalar arasında farklı araştırma nesillerinden kalan spot/5m ve Futures/1m uygulamaları bulunabilir. Nihai yeniden üretimde bu belgedeki Futures/1m, point-in-time ve maliyetli çözümleme esas alınmalı; kullanılan veri kaynağı ve varsayım her sonuç dosyasına metadata olarak yazılmalıdır.

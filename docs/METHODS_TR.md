# 12 Formasyon Yöntemi

## Ortak altyapı

Pivotlar `3`, `6` ve `12` mum sağ/sol genişlikleriyle bulunur. Yapılar yalnızca pivotun sağ tarafındaki doğrulama mumları kapandıktan sonra bilinir; böylece gelecek bilgisi kullanılmaz. ATR(14), trend çizgisi toleransı ve kesintisiz mum segmentleriyle geometrik kalite denetlenir.

Short giriş fiyatı sonraki mum açılışıdır. Başlangıç stopu formasyon tepesine `0.25 ATR` tampon eklenerek bulunur. TP, stop mesafesinin iki katı aşağıdadır (`2R`). Timeout çıkışı varyantın `horizon` değerindedir.

## Formasyon aileleri

### 65 — AB=CD, 4h

Son dört pivot `L-H-L-H` olmalıdır. `BC/AB` yaklaşık `0.382–0.886`, `CD/BC` yaklaşık `1.13–2.618`; ayrıca `CD/AB`, `1.27` veya `1.618` oranına tolerans içinde yaklaşmalıdır. D ayağından sonra aşağı kırılım aranır.

1. **`65 · close_2r`** — AB=CD destek seviyesi altında 4h kapanış gelince sonraki mumda short açar; 2R, stop veya 50 mum timeout ile kapanır.
2. **`65 · retest_2r`** — İlk aşağı kırılımdan sonra en fazla 10 mum içinde seviyeye geri dokunup altında kırmızı kapanış yapan başarısız retesti bekler; sonraki mumda short açar ve 50 mum izler.
3. **`65 · close_2r_h100`** — `close_2r` ile aynı giriş/TP/SL’yi kullanır fakat timeout süresini 100 adet 4h muma uzatır.

### 9 — Simetrik üçgen, 4h

Üç tepe ve üç dip dönüşümlüdür; üst trend çizgisi aşağı, alt trend çizgisi yukarı eğimli ve yapı daralıyor olmalıdır. Alt trend çizgisi altında kapanış short onayıdır.

4. **`9 · close_2r`** — Üçgen alt çizgisi altında kapanıştan sonraki mumda short açar; 2R/stop/50 mum timeout kullanır.
5. **`9 · close_2r_h25`** — Aynı sinyali yalnızca 25 adet 4h mum boyunca taşır.
6. **`9 · close_2r_h100`** — Aynı sinyali 100 adet 4h mum boyunca taşır.

### 16 — Yatay dikdörtgen, 1h

Üç tepe ve üç dip çizgisinin eğimi ATR’ye göre yatay olmalı, fiyat iki paralel bant arasında sıkışmalı ve alt bant altında kapanmalıdır.

7. **`16 · close_2r`** — Dikdörtgen desteği altında 1h kapanıştan sonraki mumda short açar; 2R/stop/50 mum timeout kullanır.
8. **`16 · close_2r_h25`** — Aynı girişi kullanır fakat 25 adet 1h mum sonunda timeout eder.

### 21 — Megafon, 1h

Üç tepe giderek yükselirken üç dip giderek alçalır; iki çizgi dışa doğru açılır. Alt genişleyen çizgi altında kapanış short onayıdır.

9. **`21 · close_2r`** — Megafon alt çizgisi altında kapanıştan sonraki mumda short açar; 2R/stop/50 mum timeout kullanır.
10. **`21 · retest_2r`** — Kırılım sonrası en fazla 10 mum içinde alt çizgiye geri dokunup altında kırmızı kapanan başarısız retestten sonra short açar.
11. **`21 · close_2r_trend`** — Normal kırılıma ek olarak son 10 mumdaki fiyat düşüşünün en az yaklaşık `1 ATR` olmasını ister.
12. **`21 · close_2r_volume`** — Normal kırılıma ek olarak sinyal mumu hacminin önceki 20 mum ortalamasının en az `1.5×` olmasını ister.

## Çıkış sırası

Her açık short için 1m mumlar kronolojik taranır:

1. Mark fiyatı likidasyon/koruyucu stop sınırına ulaşırsa risk çıkışı.
2. Trade high stopa ulaşırsa SL.
3. Trade low hedefe ulaşırsa TP.
4. Süre dolarsa timeout fiyatından çıkış.

Aynı 1m mum içinde hem TP hem SL görünüyorsa sıralama bilinmediğinden stop önce kabul edilir. Net PnL’den fee, slippage ve funding düşülür.

## `1 → 12 → 123 → 1234 → 12345`

- **1:** Yukarıdaki 12 yöntemden biri geçerli sinyal üretir.
- **2:** BTC rejimi aşırı pozitif değildir ve coinin son kapanmış mumundaki `quote_volume / trade_count` likiditesi minimum eşiği geçer.
- **3:** 2020–2024 V4 modeli beklenen net getiriyi en az `0.26 USDT` tahmin eder.
- **4:** Coin son 200 ana zaman dilimi mumunda net düşmüştür.
- **5:** Son sinyal mumu kırmızıdır (`close < open`) ve kapanışı `(close-low)/(high-low) ≤ 0.40` koşulunu sağlar.


# Binance USD-M Short Formasyon + V4 Araştırması

Bu repo, Binance USD-M perpetual piyasasında geliştirilen dört formasyon ailesinin 12 short varyantını, beş kümülatif filtre aşamasını, 2020–2024 V4 makine öğrenmesi filtresini, 1 dakikalık hassas TP/SL çözümlemesini ve interaktif dashboardları yeniden üretilebilir biçimde arşivler.

> Araştırma/paper-trade projesidir; yatırım tavsiyesi değildir. Geçmiş performans geleceği garanti etmez.

## Hızlı bağlantılar

- [12 yöntem ve sinyal/çıkış mantığı](docs/METHODS_TR.md)
- [V4 modeli, 59 feature, target ve eğitim ayrıntıları](docs/ML_V4.md)
- [Baştan üretim rehberi](REPRODUCE.md)
- [Yeniden üretilebilirlik denetimi ve sınırlar](docs/REPRODUCIBILITY_AUDIT.md)
- [Dashboard merkezi](docs/index.html)

## Araştırmanın sabit tanımı

- Yön: yalnızca short.
- Ana zaman dilimleri: AB=CD ve simetrik üçgen 4h; dikdörtgen ve megafon 1h.
- Giriş: formasyon kırılım/onayından sonraki mumun açılışı; retest varyantında kırılım sonrası başarısız geri testten sonraki mum.
- Formasyon stopu: son formasyon tepesi + `0.25 × ATR(14)`.
- Hedef: giriş riskinin iki katı aşağıda, yani `2R`.
- Timeout: varyanta göre 25, 50 veya 100 ana zaman dilimi mumu.
- Hassas sonuç: girişten sonra 1m Futures trade/mark verisi; aynı dakikada TP ve SL varsa korumacı olarak stop önce.
- Maliyetler: giriş/çıkış fee, slippage ve pozisyon süresindeki funding dâhil.
- Her işlemde en fazla 10 USDT izole teminat; kaldıraç senaryoya göre değişir.

## Beş kümülatif aşama

| Gösterim | Açıklama |
|---|---|
| `1` | Ham 12 formasyon/varyant sinyali |
| `12` | 1 + BTC 200 mum getirisi ≤ `%7.3453` ve son kapanmış mumda ortalama işlem hacmi ≥ `147.6241 USDT` |
| `123` | 12 + V4 tahmini net PnL skoru ≥ `0.26 USDT` |
| `1234` | 123 + coin fiyatı 200 mum öncesine göre düşük (`coin_return_200 < 0`) |
| `12345` | 1234 + sinyal mumu kırmızı ve aralığının alt `%40` bölümünde kapanmış |

Filtreler kümülatiftir; örneğin `1234`, önceki üç koşulu da geçmek zorundadır.

## Repo düzeni

```text
docs/                 Açıklamalar ve GitHub Pages dashboardları
models/               Ana 2020–2024 model ve eğitim-penceresi modelleri
metadata/             Eğitim pencereleri, eşikler ve paper karar kayıtları
src/                  Veri indirme, feature, eğitim, backtest ve dashboard kodu
paper/                Salt paper-trade servisinin kaynak kodu
```

Ham 1m/1h/4h mum arşivi repo boyutunu aşacağı için eklenmemiştir. `REPRODUCE.md`, Binance’tan aynı veri setinin nasıl oluşturulacağını açıklar.

Ana eğitim feature/target snapshotları ve 1m backtest sonuç defteri ise repoya eklenmiştir. Böylece ham arşiv olmadan model eğitimi ve sonuç mutabakatı yapılabilir.

## Seçilen paper sürümü

Son paper yapılandırması aşama `123` ve şu üç 4h dalını kullanır:

1. `65 / close_2r`
2. `65 / retest_2r`
3. `9 / close_2r`

Ana model `models/v4_2020_2024.joblib` dosyasıdır.


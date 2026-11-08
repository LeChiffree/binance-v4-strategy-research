# Yeniden Üretilebilirlik Sınırları

## Repo içinde dondurulmuş kanıtlar

- 18 Ekim 2026 tarihli 525 aktif USD-M perpetual `exchangeInfo` snapshotı.
- 32.524 satırlık V4 feature matrisi ve 59 sütunluk model şeması.
- 2020 ve 2021–2024 2×/10 USDT eğitim targetları.
- 15 eğitim-penceresi modelinin tamamı.
- 130.096 kaldıraç-senaryosu satırı; 122.826 çözülen 1m işlem.
- Beş aşama × dört kaldıraç özet tablosu.
- Dashboardlarda kullanılan gömülü sonuç snapshotları.
- Güncel paper evreni: önceki 30 tamamlanmış günlük hacme göre 250 tarama sembolü ve 500+ piyasa-context sembolü.

Bu dosyalar sayesinde başka bir araştırmacı ham arşivi tekrar indirmeden ana 2020–2024 modelini byte düzeyinde olmasa da **tahmin düzeyinde birebir** yeniden eğitebilir ve dashboard sonuçlarını denetleyebilir.

## Repo içinde olmayan büyük kaynak

Binance’ın yıllara yayılan trade-price 1m ve mark-price 1m arşivi GitHub’a eklenmemiştir. Tam 1m replay’i sıfırdan tekrar yapmak isteyen kişi `BINANCE_DATA_ROOT` altında bu arşivi indirmelidir. İndirici kodlar, frozen kontrat snapshotı ve cutoff repodadır.

Windows örneği:

```powershell
$env:BINANCE_DATA_ROOT='D:\1mDataBinance'
```

macOS/Linux örneği:

```bash
export BINANCE_DATA_ROOT=/mnt/data/binance
```

Değişken verilmezse varsayılan `data/binance/` kullanılır.

## İki farklı evreni karıştırmayın

- **Frozen backtest evreni:** 18 Ekim 2026 snapshotında aktif 525 kontrat. Araştırma sonuçları bu evrene aittir.
- **Canlı paper evreni:** her ay yenilenen mevcut aktif kontratlar içinden önceki 30 tamamlanmış günlük hacme göre ilk 250; context listesi ayrıca güncellenir.

Dolayısıyla ileriki bir tarihte aktif kontrat sayısının 525 olmaması geçmiş backtestin hatası değildir.

## Hızlı doğrulama

Önce eğitim pencerelerini yeniden üretin, sonra offline audit çalıştırın:

```bash
python src/train_v4_window_sweep_525.py
python src/verify_repository.py
```

Beklenen önemli değerler:

- 12 varyant
- 525 frozen kontrat
- 32.524 feature satırı
- 59 feature
- 2020–2024 model tahmin farkı `0.0`
- 130.096 kaldıraç-senaryosu satırı
- 122.826 çözülen işlem

## Bilinen metodolojik sınırlar

- Aynı 1m mumda TP ve SL görülürse stop önce kabul edilir; gerçek tick sırası bilinmez.
- Tarihsel likidasyon, mark 1m high ve sabit MMR proxy’sidir; borsanın bütün tarihsel risk kademeleri yoktur.
- 2026 kısmi yıldır.
- Evren “araştırma tarihinde aktif kontratlar” üzerinden kurulduğu için delist olmuş eski kontratlarda survivorship etkisi vardır.
- Bazı eski kontratlarda Binance arşivi eksiktir; bunlar `unresolved_data` olarak ayrılır ve sıfır PnL sayılmaz.
- Threshold ve paper aday seçimi çok sayıda geçmiş karşılaştırmadan sonra yapılmıştır; seçim yanlılığı tamamen ortadan kalkmaz.


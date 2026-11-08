# Baştan Yeniden Üretim

## 1. Ortam

Python 3.11 önerilir.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Binance veri evreni

`src/download_active_525_futures_bars.py`, Binance USD-M `exchangeInfo` üzerinden aktif USDT perpetual listesini alır ve her kontratı yalnızca listeli olduğu geçmişte kullanır. Ham veriyi repo dışında bir veri dizininde saklayın.

```bash
python src/download_active_525_futures_bars.py
python src/download_d_futures_1m.py
python src/download_d_mark_1m.py
python src/download_d_active_525_funding.py
python src/derive_d_1h_4h_from_1m.py
```

Binance eski/delist olmuş bazı kontratlar için 1m veya mark arşivi sunmayabilir. Kapsam raporunda bu satırlar çözülemeyen olarak tutulmalı; sessizce kârlı/zararlı kabul edilmemelidir.

## 3. 12 sinyali üret

Formasyon tanımları `src/backtest.py` içindeki `VARIANTS`, `structures()` ve `signals()` fonksiyonlarındadır. Sinyal timestamp’i yalnızca onay mumu kapandıktan sonra oluşur.

## 4. 1m target üret

`src/build_new_v4_training_labels.py` ve `src/precise_1m_backtest.py` ile 2×/10 USDT etiketleri oluşturun. Fee, slippage, funding ve mark-price likidasyon kontrolünü kapatmayın.

## 5. Featureları üret

```bash
python src/build_active_525_v4_features.py
```

Beklenen model girdisi 59 sütundur. Eksik point-in-time BTC, breadth, funding veya aktivite verisi bulunan satır modele verilmemelidir.

## 6. V4’ü eğit

```bash
python src/train_v4_window_sweep_525.py
```

2020–2024 penceresinin beklenen özeti `metadata/training_windows.json` içindedir: 11.286 satır, 234 coin, 59 feature. Üretilen modelin feature sırası `models/v4_2020_2024.joblib` ile aynı olmalıdır.

## 7. Beş aşamayı ve kaldıraçları test et

```bash
python src/backtest_active_525_five_stage.py
python src/build_active_525_dashboard.py
```

Backtestte test yılı eğitim penceresinin içinde olmamalıdır. Seçilen ana model için 2025 ve 2026 yalnızca testtir.

## 8. Paper servisi

`paper/` klasöründe salt paper servis bulunur. Modeli `paper/v4_2020_2024.joblib` adıyla yerleştirip:

```bash
cd paper
pip install -r requirements.txt
python -m uvicorn app:app --host 0.0.0.0 --port 8766
```

Son seçim `paper/engine.py` içindeki `SELECTED_VARIANTS` sabitidir. Mevcut sürüm yalnızca 4h `65/close_2r`, `65/retest_2r`, `9/close_2r` ve kümülatif `123` filtresini kullanır.

## Doğrulama kontrol listesi

- Eğitim/test yılları çakışmıyor.
- Sinyal featureları yalnızca kapanmış mumları görüyor.
- Aynı 1m mumda TP+SL varsa stop önce.
- Funding timestamp’e göre birikiyor.
- Fee ve slippage hem girişte hem çıkışta uygulanıyor.
- Çözülemeyen 1m kapsamı sonuçtan ayrı raporlanıyor.
- Model feature adları ve sırası joblib manifestiyle aynı.


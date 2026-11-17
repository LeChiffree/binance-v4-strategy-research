# Singular-Backtest — Binance V4 Short

Bu klasör, V4 Short formasyon araştırmasının **tekil işlem** (Singular-Backtest)
varyantını üretir. Normal çıktılara (`output/`, `docs/`) dokunulmaz; tüm yeni
dosyalar bu klasörde tutulur.

## Kural

Seçilen kapsamda (varsayılan: formasyon + varyant) **aynı coinde aynı anda en
fazla bir açık işlem** bulunabilir. Önceki işlem kapanmadan gelen yeni giriş
sinyali bloke edilir. Aynı giriş anında sıra: `entry_time`, varyant adı,
`trade_id`.

Beş kümülatif aşama (`stage1` … `stage5`) ayrı birer filtre akışı gibi ele
alınır; her aşamada tekillik kendi kabul edilen işlem kümesi üzerinde uygulanır.

## Kapsamlar

| Kapsam | Anlam | stage1 bloke |
|---|---|---|
| `variant` (varsayılan) | Aynı formasyon + varyant, tüm aşamalar ve coinde tek pozisyon | %14,67 |
| `pattern` | Aynı formasyon ailesi, varyantlar arası tek pozisyon | %66,76 |
| `portfolio` | Tüm V4 portföyü, coin başına tek işlem | %68,76 |

## Sonuçlar (varsayılan `variant`, 511 sembol)

| Aşama | Normal | Singular | Bloke |
|---|---|---|---|
| 1 — Ham formasyon | 29.730 | 25.370 | 4.360 (%14,67) |
| 12 — BTC + likidite | 6.937 | 6.040 | 897 (%12,93) |
| 123 — + V4 ML | 1.447 | 1.273 | 174 (%12,02) |
| 1234 — + coin düşüşü | 906 | 787 | 119 (%13,13) |
| 12345 — + kırmızı mum | 710 | 624 | 86 (%12,11) |

Aşama 1 toplamı: ortalama getiri **%0,4590 → %0,3994**, kasa **1.365$ → 1.013$**.
Tekil varyant, işlemlerin ~%14,7'sini keser; kalan işlemlerin kalitesi bir miktar
düşer (ortalama getiri −0,06 puan). Bu, aynı coinde üst üste binen işlemlerin
ortalamada hafif pozitif olduğunu gösterir.

## Dosyalar

- `run_singular.py` — üretici; `output/active_525_five_stage/trades.parquet`
  içinden `leverage==1`, `status=='resolved'` satırlarını kullanır.
- `verify_singular.py` — 60 satırın tamamını, normal metriklerin
  `allinone.json` ile birebir eşleşmesini, bloke muhasebesini ve kasa
  formülünü doğrular.
- `results/singular_results.json` — dashboard payload'ı.
- `results/singular_rows.csv` — 60 satır (12 varyant × 5 aşama).
- `results/singular_coins.csv` — aşama 1 için strateji × coin kırılımı.
- `results/run_manifest.json`, `run.log`.

## Kullanım

```bash
python Singular-Backtest/run_singular.py
python Singular-Backtest/run_singular.py --scope=pattern
python Singular-Backtest/run_singular.py --scope=portfolio
python Singular-Backtest/verify_singular.py
```

## Metrik tanımları

- `retAll` = işlem başına ortalama `net_pnl_usdt` / 10$ × 100
- `ret2026` = 2026'da **giriş** yapan işlemlerin ortalama getirisi
- `winRatio` = kazanan / kaybeden (win rate değil)
- `winHoldH` = kazanan işlemlerin ortalama tutma süresi (saat)
- `kasaAll` = toplam net USD (10$ sabit marjin), `sumR` = `kasaAll / 10`
- Kâr/zarar günleri çıkış gününe göre sayılır; `maxDD` günlük kümülatif
  eğrinin en büyük düşüşüdür.

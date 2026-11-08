# 525 USD-M kontrat backtest yöntemi

- Evren: 2026-10-16 08:01 UTC exchangeInfo anındaki 525 aktif USDT teminatlı USD-M perpetual kontrat. Önceki 218 coinin 173'ü bu evrende; 352 coin yeni. Bu çalışmada üç yıl yaş koşulu yoktur.
- Veri kesimi: 2026-10-18 00:00 UTC. Son tam gün 17 Ekim 2026'dır. 2019 başlangıcı kontratın ilk erişilebilir Futures mumuna bağlıdır.
- Ana kaynak: `D:\1mDataBinance`. İşlem 1m ve mark 1m Binance'in resmî USD-M arşivinden; 1s ve 4s mumlar 1m işlem verisinden, yalnızca 60/240 dakikası tam olan aralıklar kullanılarak üretilir. Funding geçmişi sembol bazında saklanır.
- Kapsam: Beklenen 24.650 işlem fiyatı arşiv dosyasının 24.633'ü mevcut. Binance arşivinde bulunmayan 17 eski dosya `d_1m_coverage.csv` içinde listelenir. Sinyal takip süresi için gerekli 20 mark arşivi de bulunamadı; `d_mark_1m_missing.csv` içinde listelenir. Eksik 1m verisi sonuç uydurularak doldurulmaz.
- Aşama 1: Önceki çalışmadaki 12 short formasyon varyantı, 1s/4s. Ham sinyalden giriş bir sonraki mum açılışında yapılır. Orijinal stop formasyon tepesine 0,25 ATR eklenerek, hedef stop mesafesinin 2 katı aşağıya konur. Net ödül/risk 1,5 altı sinyal reddedilir.
- Aşama 2: BTC 200 mum getirisi en çok %7,3452727496624 ve coin son kapanmış mumunda işlem başına ortalama hacim en az 147,6241108493176 USDT.
- Aşama 3: Spot temelli tarihsel etiketlerle 2021–2024'te eğitilen sabit V4 regresyon puanı en az 0,26 USDT. Yeni Futures evreninde yeniden kalibre edilmedi.
- Aşama 4: Coin kapanışı aynı zaman dilimindeki 200 mum öncesi kapanıştan düşük.
- Aşama 5: Son kapanmış sinyal mumu düşüş mumu ve kapanış mum aralığının alt %40'ında.
- Her aşama önceki aşamaları da içerir. Her sinyal bağımsız olarak 1x, 5x, 20x, 25x kaldıraç ve en çok 10 USDT izole teminatla oynatılır. Adet Binance'in güncel lot adımına aşağı yuvarlanır; fiilen kullanılan teminat bazı işlemlerde 10 USDT'den düşüktür. Çakışan işlemler için kasa kısıtı uygulanmaz.
- Giriş/çıkış: Futures 1m mumları. Aynı 1m içinde hem hedef hem stop görülürse stop önce kabul edilir. Giriş ve çıkışa ayrı ayrı %0,1 komisyon ve %0,02 kayma uygulanır. Gerçek tarihsel funding gözlemleri pozisyon süresine eklenir.
- Koruyucu stop: Orijinal stop yaklaşık likidasyon sınırının üzerine çıkacaksa, stop yaklaşık likidasyon fiyatının giriş fiyatına göre 0,5 yüzde puan altına çekilir. Yaklaşık likidasyon %0,5 bakım marjına dayanır. Tarihsel teminat kademeleri ve 1m içindeki gerçek olay sırası bilinmediğinden “likidasyon kesinlikle olmadı” sonucu çıkarılamaz; olası çakışmalar işlem düzeyinde bayraklanır. Net zarar fiilen kullanılan izole teminatla sınırlandırılır (en çok 10 USDT).
- Güncel Binance emir alt sınırları uygulanır; tarihsel alt sınırlar farklı olabilir. Verisi eksik, henüz ufku bitmemiş veya reddedilmiş sinyaller kâr hesaplamasından ayrılır ve özet tabloda sayılır.
- Bu evren yalnızca bugün aktif olan kontratları içerir; geçmişte listeden çıkanlar dışarıda kaldığı için hayatta kalma yanlılığı vardır. V4'ün 2021–2024 sonuçları eğitim içi, 2019–2020 sonuçları geriye dönük uygulama; eğitim sonrası değerlendirme 2025–2026'dır. Önceki Spot vekil 218 coin çalışmasıyla rakamlar doğrudan karşılaştırılmamalıdır.

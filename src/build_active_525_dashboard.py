"""Interactive 525-coin five-stage USD-M Futures research dashboard."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from plotly.offline import get_plotlyjs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/active_525_five_stage"
SOURCE = OUT / "trades.parquet"


def main() -> None:
    trades = pd.read_parquet(SOURCE)
    universe = pd.read_csv(OUT / "universe_comparison.csv")
    old_symbols = set(universe.loc[universe.active_525 & universe.old_218, "symbol"])
    good = trades[trades.status.eq("resolved")].copy()
    assert len(good) and set(good.leverage.unique()) == {1, 5, 20, 25}
    t = pd.to_datetime(good.entry_time, unit="ms", utc=True)
    good["year"] = t.dt.year
    good["month"] = t.dt.strftime("%Y-%m")
    good["date"] = t.dt.strftime("%Y-%m-%d")
    good["hour"] = t.dt.hour
    good["period"] = np.select([good.year.lt(2021), good.year.le(2024)],
                                ["Eğitim öncesi", "V4 eğitim"], default="Eğitim sonrası")
    good["stage"] = 1+sum(good[f"stage{i}"].fillna(False).astype(int) for i in range(2, 6))
    good["fee_usdt"] = good.entry_fee_usdt+good.exit_fee_usdt
    good["risk_flagged"] = good.liquidation_risk_flag.fillna("").ne("")
    good["cohort"] = np.where(good.symbol.isin(old_symbols), "old", "new")
    fields = {"symbol": "c", "pattern_id": "p", "variant": "v", "timeframe": "tf",
              "leverage": "l", "stage": "s", "horizon_bars": "hz", "year": "y", "month": "m", "date": "d",
              "hour": "h", "period": "period", "net_pnl_usdt": "n", "exit_reason": "r", "entry_time": "t",
              "exit_time": "e", "holding_minutes": "dur", "v4_score_2021_2024": "sc",
              "funding_usdt": "f", "fee_usdt": "fee", "gross_price_pnl_usdt": "g",
              "risk_flagged": "flag", "cohort": "co"}
    compact = good[list(fields)].rename(columns=fields)
    compact["p"] = compact.p.astype(str)
    rows = json.loads(compact.to_json(orient="records"))
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028")
    summary = pd.read_csv(OUT / "stage_leverage_summary.csv")
    active_symbols = sorted(universe.loc[universe.active_525, "symbol"].astype(str))
    coverage = pd.read_csv(OUT / "d_1m_coverage.csv")
    archive_unavailable = coverage.loc[coverage.status.eq("archive_unavailable"), ["symbol", "stamp"]]
    mark_missing = pd.read_csv(OUT / "d_mark_1m_missing.csv")
    meta = {"universe": 525, "universe_exchange_info_server_time": "2026-10-16 08:01 UTC",
            "old_overlap": 173, "new_active": 352,
            "old_no_longer_active": 45, "three_year_age_filter": False,
            "price_source": "Binance USD-M Futures 1m",
            "training": "V4: 2021–2024, mevcut Spot temelli etiketlerle dondurulmuş model",
            "cutoff": "2026-10-17 23:59 UTC", "fee_per_side_pct": .1,
            "slippage_per_side_pct": .02, "margin_per_trade_usdt": 10,
            "btc_max_pct": 7.3452727496624, "liquidity_min_usdt": 147.6241108493176,
            "v4_threshold_usdt": .26,
            "signals": int(trades.trade_id.nunique()),
            "resolved_scenarios": len(good),
            "unresolved_scenarios": int(trades.status.eq("unresolved_data").sum()),
            "archive_files_available": int(coverage.status.eq("available").sum()),
            "archive_files_unavailable": len(archive_unavailable),
            "archive_unavailable_coins": sorted(archive_unavailable.symbol.unique().tolist()),
            "mark_archive_files_unavailable": len(mark_missing),
            "mark_archive_unavailable_coins": sorted(mark_missing.symbol.unique().tolist())}
    meta["active_symbols"] = active_symbols
    meta["old_symbols"] = sorted(old_symbols)
    (OUT / "dashboard_manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    plotly = OUT / "plotly.min.js"
    if not plotly.exists():
        plotly.write_text(get_plotlyjs(), encoding="utf-8")
    html = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>525 Coin · 5 Aşama · Binance USD-M Backtest</title><script src="plotly.min.js"></script>
<style>
:root{--bg:#08111d;--nav:#0d1928;--panel:#111f30;--border:#20354a;--text:#eaf2fb;--muted:#9aabc0;--green:#31d3a0;--red:#ff6e78;--blue:#6cb8ff;--amber:#e9bc62}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px/1.45 Inter,Segoe UI,system-ui,sans-serif}aside{position:fixed;inset:0 auto 0 0;width:260px;background:var(--nav);border-right:1px solid var(--border);padding:20px 16px;overflow:auto;z-index:4}main{margin-left:260px;padding:28px;max-width:2200px}.brand{font-size:14px;letter-spacing:.14em;font-weight:800;color:var(--blue);text-transform:uppercase}.subbrand{color:var(--muted);font-size:12px;margin:10px 0 22px}.filter{margin:0 0 15px}.filter label{display:block;font-size:11px;color:var(--muted);font-weight:700;letter-spacing:.07em;text-transform:uppercase;margin-bottom:6px}.filter select,.filter input{width:100%;height:35px;border-radius:7px;border:1px solid #35516a;background:#15263a;color:var(--text);padding:5px 9px;font:inherit}button{border:1px solid #43647e;background:#18314a;color:var(--text);padding:9px 13px;border-radius:8px;cursor:pointer}button:hover{background:#234260}h1{font-size:29px;margin:0 0 6px;letter-spacing:-.03em}h2{font-size:17px;margin:0 0 4px}p{margin:0}.muted{color:var(--muted)}.topline{display:flex;justify-content:space-between;gap:15px;align-items:flex-start;margin-bottom:22px}.badge{display:inline-block;border:1px solid #35516a;background:#17314a;color:#a5d4ff;padding:7px 10px;border-radius:999px;white-space:nowrap}.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}.card,.panel{background:var(--panel);border:1px solid var(--border);border-radius:13px;box-shadow:0 8px 26px #0002}.card{grid-column:span 2;padding:16px;min-height:88px}.card .label{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em}.card strong{display:block;font-size:24px;letter-spacing:-.04em;margin-top:7px}.panel{grid-column:span 6;padding:18px;min-width:0}.panel.wide{grid-column:span 12}.chart{height:360px}.chart.tall{height:450px}.tablebox{overflow:auto;max-height:520px;margin-top:12px;border:1px solid var(--border);border-radius:8px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:right;padding:9px 11px;border-bottom:1px solid #203144;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{position:sticky;top:0;background:#172a3d;color:#a8bed1;font-size:11px;z-index:1}tr:hover td{background:#183047}.pos{color:var(--green)}.neg{color:var(--red)}.note{background:#102237;border:1px solid #29516d;border-radius:10px;padding:14px 17px;margin:15px 0 23px;color:#bcd4e7}.links{margin-top:13px;display:flex;gap:15px;flex-wrap:wrap}a{color:#79c7ff}.tiny{font-size:11px}.tablehead{display:flex;justify-content:space-between;gap:12px;align-items:center}.tablehead input{width:250px;background:#15263a;border:1px solid #35516a;border-radius:7px;color:white;padding:8px}.pager{display:flex;gap:8px;align-items:center;margin-top:12px}.pager button{padding:5px 10px}@media(max-width:1050px){aside{position:static;width:auto;display:grid;grid-template-columns:repeat(3,1fr);gap:10px}aside .brand,aside .subbrand{grid-column:1/-1}.filter{margin:0}main{margin:0}.card{grid-column:span 4}.panel{grid-column:span 12}}@media(max-width:620px){aside{grid-template-columns:repeat(2,1fr)}main{padding:14px}.card{grid-column:span 6}.topline{display:block}.badge{margin-top:12px}}
</style></head><body>
<aside><div class="brand">Binance USD-M · Araştırma</div><div class="subbrand">525 aktif USDT perpetual<br>5 aşama · 4 kaldıraç · 1m çözünürlük</div>
<div class="filter"><label>Kümülatif aşama</label><select id="stage"><option value="1">1 · Formasyon</option><option value="2">1+2 · BTC + likidite</option><option value="3">1+2+3 · V4 ≥ 0,26</option><option value="4">1+2+3+4 · 200 mum düşüş</option><option selected value="5">1+2+3+4+5 · Alt %40 kapanış</option></select></div>
<div class="filter"><label>Kaldıraç</label><select id="leverage"><option value="1">1x</option><option value="5">5x</option><option selected value="20">20x</option><option value="25">25x</option></select></div>
<div class="filter"><label>Yıl</label><select id="year"><option value="all">Tüm yıllar</option></select></div>
<div class="filter"><label>Model dönemi</label><select id="period"><option value="all">Tüm dönemler</option><option>Eğitim öncesi</option><option>V4 eğitim</option><option>Eğitim sonrası</option></select></div>
<div class="filter"><label>Ay</label><select id="month"><option value="all">Tüm aylar</option></select></div>
<div class="filter"><label>Coin</label><select id="coin"><option value="all">Tüm coinler</option></select></div>
<div class="filter"><label>Coin grubu</label><select id="cohort"><option value="all">525 aktif coin</option><option value="old">Önceki çalışmayla ortak 173</option><option value="new">Yeni 352 coin</option></select></div>
<div class="filter"><label>Formasyon</label><select id="pattern"><option value="all">Tümü</option></select></div>
<div class="filter"><label>Varyant</label><select id="variant"><option value="all">Tümü</option></select></div>
<div class="filter"><label>Zaman dilimi</label><select id="timeframe"><option value="all">Tümü</option><option>1h</option><option>4h</option></select></div>
<div class="filter"><label>Azami mum ufku</label><select id="horizon"><option value="all">Tümü</option></select></div>
<div class="filter"><label>Çıkış nedeni</label><select id="reason"><option value="all">Tümü</option></select></div>
<div class="filter"><label>Risk bayrağı</label><select id="risk"><option value="all">Tümü</option><option value="clear">Bayraksız</option><option value="flagged">Bayraklı</option></select></div>
<div class="filter"><label>İşlem yönü</label><select id="result"><option value="all">Kâr + zarar</option><option value="pos">Sadece kâr</option><option value="neg">Sadece zarar</option></select></div>
<button id="reset">Filtreleri sıfırla</button></aside>
<main><div class="topline"><div><h1>525 coin · beş aşamalı short backtest</h1><p class="muted">Her sinyalde en çok 10 USDT izole teminat · ücret, kayma ve funding dahil · giriş/çıkış 1m Futures mumları</p></div><span class="badge">Son tamamlanan veri: 17 Eki 2026 UTC</span></div>
<div class="note" id="coverage"></div><div class="grid" id="kpis"></div>
<div class="grid" style="margin-top:15px"><section class="panel wide"><h2>Beş aşama × kaldıraç</h2><p class="muted">Hücre: işlem · net kâr · işlem başı net. Diğer filtreler bu tabloyu da günceller.</p><div class="tablebox"><table><thead><tr><th>Kaldıraç</th><th>1 · Formasyon</th><th>1+2 · BTC + likidite</th><th>1+2+3 · V4</th><th>1+2+3+4 · Trend</th><th>1+2+3+4+5 · Mum</th></tr></thead><tbody id="matrix"></tbody></table></div></section>
<section class="panel wide"><h2>Gün gün kümülatif kasa</h2><p class="muted">İşlemler çıkış UTC gününe toplanır; 1000 USDT başlangıç bakiyesi.</p><div id="equity" class="chart tall"></div></section>
<section class="panel wide"><h2>Günlük kâr / zarar</h2><p class="muted">Çıkış UTC gününe göre kârlı ve zararlı işlemlerin toplamı.</p><div id="daily" class="chart"></div></section>
<section class="panel"><h2>Yıl bazında kâr / zarar</h2><div id="annual" class="chart"></div></section><section class="panel"><h2>Ay bazında kâr / zarar</h2><div id="monthly" class="chart"></div></section>
<section class="panel wide"><h2>Takvim ayına göre mevsimsellik</h2><p class="muted">Seçili yıllardaki aynı takvim ayları bir araya getirilir.</p><div id="season" class="chart"></div></section>
<section class="panel wide"><h2>Aylık net kâr ısı haritası</h2><div id="heatmap" class="chart"></div></section>
<section class="panel"><h2>Coin bazında kârlı / zararlı işlem toplamı</h2><p class="muted">Grafikte en çok işlem üreten 30 coin; alttaki tabloda 525 coinin tamamı.</p><div id="coins" class="chart tall"></div></section><section class="panel"><h2>Giriş saati (UTC)</h2><div id="hours" class="chart tall"></div></section>
<section class="panel"><h2>Formasyon</h2><div id="patterns" class="chart"></div></section><section class="panel"><h2>Varyant</h2><div id="variants" class="chart"></div></section>
<section class="panel"><h2>Zaman dilimi</h2><div id="timeframes" class="chart"></div></section>
<section class="panel"><h2>Stratejinin azami ufku (mum)</h2><div id="horizons" class="chart"></div></section><section class="panel"><h2>Gerçek işlemde kalış süresi</h2><div id="holding" class="chart"></div></section>
<section class="panel"><h2>İşlem başı net dağılımı</h2><div id="hist" class="chart"></div></section><section class="panel"><h2>V4 puanı × sonuç</h2><div id="scores" class="chart"></div></section>
<section class="panel"><h2>Çıkış nedenleri</h2><div id="reasons" class="chart"></div></section>
<section class="panel wide"><h2>Tüm coinler</h2><p class="muted">Her coin için işlem sayısı, pozitif ve negatif net PnL toplamı. İşlem üretmeyen coinler de görünür.</p><div class="tablebox"><table><thead><tr><th>Coin</th><th>İşlem</th><th>Kârlı işlemler</th><th>Zararlı işlemler</th><th>Net</th><th>İşlem başı</th></tr></thead><tbody id="coinTable"></tbody></table></div></section>
<section class="panel wide"><div class="tablehead"><div><h2>İşlem günlüğü</h2><p class="muted">Seçili filtrelerden ilk 100 kayıt; tüm kayıtlar CSV/Parquet bağlantısında.</p></div><input id="search" placeholder="Coin veya varyant ara"></div><div class="tablebox"><table><thead><tr><th>Coin</th><th>Aşama</th><th>Kaldıraç</th><th>Formasyon</th><th>Varyant</th><th>TF</th><th>Giriş UTC</th><th>Çıkış UTC</th><th>Neden</th><th>Net USDT</th><th>Funding</th><th>Ücret</th><th>Risk bayrağı</th></tr></thead><tbody id="tradeTable"></tbody></table></div><div class="pager"><button id="prev">Önceki</button><span id="pageInfo"></span><button id="next">Sonraki</button></div></section></div>
<div class="note tiny">V4 modeli 2021–2024 Spot temelli geçmiş etiketlerle eğitildi; burada Futures OHLCV ve 525 coinlik çapraz piyasa özellikleriyle uygulanıyor. Sabit 147,62 USDT likidite ve 0,26 USDT puan eşikleri bu yeni veri dağılımında tekrar ayarlanmadı; puan kalibrasyonu değişebilir. Bu 525 coin talebinde üç yıl yaş koşulu uygulanmadı. Güncel aktif coin evreni geçmişten seçilmiştir; geçmişte listeden çıkan coinler bu tabloda yoktur. Emir alt sınırları ve lot adımları güncel exchangeInfo ile denetlenir; tarihsel kurallar değişmiş olabilir. Miktar aşağı yuvarlandığında kullanılan teminat 10 USDT'den az olabilir. Likidasyon seviyesi tarihsel teminat kademeleri yerine %0,5 bakım marjı yaklaşımıyla hesaplanır. Mark fiyatında olası likidasyon ve aynı 1m mumunda TP/SL sırası bayraklanır. Tüm sonuçlar bağımsız en çok 10 USDT teminatlı işlemlerin toplamıdır; aynı anda açılabilecek işlem sayısı veya kasa sınırı uygulanmaz.</div>
<div class="links"><a href="trades.csv">Tüm işlemler CSV</a><a href="stage_leverage_summary.csv">Aşama × kaldıraç özeti</a><a href="universe_comparison.csv">Coin evreni karşılaştırması</a><a href="d_1m_coverage.csv">1m veri kapsamı</a><a href="d_mark_1m_missing.csv">Mark 1m veri boşlukları</a><a href="active_525_five_stage.xlsx">Excel raporu</a><a href="METHOD.md">Yöntem ve sınırlar</a><a href="dashboard_manifest.json">Veri özeti</a></div></main>
<script id="payload" type="application/json">__PAYLOAD__</script><script>
const R=JSON.parse(document.getElementById('payload').textContent),META=__META__, fmt=new Intl.NumberFormat('tr-TR',{maximumFractionDigits:2}),money=x=>fmt.format(x)+' USDT';
const green='#31d3a0',red='#ff6e78',blue='#6cb8ff',muted='#9aabc0';
const layout={paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:'#b9cadd',family:'Inter,Segoe UI,system-ui'},margin:{l:55,r:22,t:12,b:52},legend:{orientation:'h',y:1.17},xaxis:{gridcolor:'#20354a',zerolinecolor:'#29455b'},yaxis:{gridcolor:'#20354a',zerolinecolor:'#29455b'}};
const opt={responsive:true,displayModeBar:false};let PAGE=0,VIEW=[];
function choices(id,values){const s=document.getElementById(id);for(const v of [...new Set(values)].sort((a,b)=>String(a).localeCompare(String(b),'tr',{numeric:true}))){let o=new Option(v,v);s.add(o)}}
choices('year',Array.from({length:8},(_,i)=>2019+i));choices('month',R.map(x=>x.m));choices('coin',META.active_symbols);choices('pattern',R.map(x=>x.p));choices('variant',R.map(x=>x.v));choices('reason',R.map(x=>x.r));choices('horizon',R.map(x=>x.hz));
function controls(){return Object.fromEntries(['stage','leverage','year','period','month','coin','cohort','pattern','variant','timeframe','horizon','reason','risk','result'].map(k=>[k,document.getElementById(k).value]))}
function match(x,f,ignoreStageLeverage=false){if(!ignoreStageLeverage&&(x.s<+f.stage||x.l!=+f.leverage))return false;
 if(f.year!=='all'&&x.y!=f.year)return false;if(f.period!=='all'&&x.period!==f.period)return false;if(f.month!=='all'&&x.m!==f.month)return false;if(f.coin!=='all'&&x.c!==f.coin)return false;if(f.cohort!=='all'&&x.co!==f.cohort)return false;
 if(f.pattern!=='all'&&x.p!==f.pattern)return false;if(f.variant!=='all'&&x.v!==f.variant)return false;if(f.timeframe!=='all'&&x.tf!==f.timeframe)return false;if(f.horizon!=='all'&&x.hz!=f.horizon)return false;
 if(f.reason!=='all'&&x.r!==f.reason)return false;if(f.risk==='clear'&&x.flag)return false;if(f.risk==='flagged'&&!x.flag)return false;if(f.result==='pos'&&x.n<=0)return false;if(f.result==='neg'&&x.n>=0)return false;return true}
function agg(rows,key){const m=new Map();for(const x of rows){const k=key(x);let a=m.get(k);if(!a)m.set(k,a={k,n:0,pos:0,neg:0,net:0});a.n++;a.net+=x.n;if(x.n>0)a.pos+=x.n;else a.neg+=x.n}return [...m.values()]}
function bar(id,data,horizontal=false){Plotly.react(id,[{type:'bar',name:'Kâr',x:horizontal?data.map(d=>d.pos):data.map(d=>d.k),y:horizontal?data.map(d=>d.k):data.map(d=>d.pos),orientation:horizontal?'h':'v',marker:{color:green}},{type:'bar',name:'Zarar',x:horizontal?data.map(d=>d.neg):data.map(d=>d.k),y:horizontal?data.map(d=>d.k):data.map(d=>d.neg),orientation:horizontal?'h':'v',marker:{color:red}}],{...layout,barmode:'relative',margin:{...layout.margin,l:horizontal?105:55},yaxis:{...layout.yaxis,automargin:true}},opt)}
function safe(x){return String(x??'').replace(/[&<>"']/g,s=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]))}
function render(){const f=controls(),a=R.filter(x=>match(x,f)),order=[...a].sort((x,y)=>x.e-y.e),n=a.length,net=a.reduce((s,x)=>s+x.n,0),pos=a.reduce((s,x)=>s+Math.max(0,x.n),0),neg=a.reduce((s,x)=>s+Math.min(0,x.n),0),wins=a.filter(x=>x.n>0).length;
 let eq=1000,peak=1000,dd=0,min=1000;for(const x of order){eq+=x.n;peak=Math.max(peak,eq);min=Math.min(min,eq);dd=Math.max(dd,peak-eq)}
 const byExitDay=agg(order,x=>new Date(x.e).toISOString().slice(0,10)).sort((x,y)=>String(x.k).localeCompare(String(y.k))),ex=[],ey=[];let dayEq=1000;for(const d of byExitDay){dayEq+=d.net;ex.push(d.k);ey.push(dayEq)}
 const cards=[['İşlem',fmt.format(n)],['Net kâr',money(net)],['İşlem başı net',n?money(net/n):'—'],['Brüt kâr',money(pos)],['Brüt zarar',money(neg)],['Kazanma oranı',n?fmt.format(100*wins/n)+'%':'—'],['Maks. düşüş',money(dd)],['En düşük kasa',money(min)],['Aktif coin',fmt.format(new Set(a.map(x=>x.c)).size)],['Risk bayraklı',fmt.format(a.filter(x=>x.flag).length)],['Funding',money(a.reduce((s,x)=>s+x.f,0))],['Komisyon',money(a.reduce((s,x)=>s+x.fee,0))]];
 document.getElementById('kpis').innerHTML=cards.map(([l,v])=>`<div class="card"><div class="label">${l}</div><strong>${v}</strong></div>`).join('');
 const all=R.filter(x=>match(x,f,true));const levs=[1,5,20,25];document.getElementById('matrix').innerHTML=levs.map(l=>`<tr><th>${l}x</th>${[1,2,3,4,5].map(s=>{const z=all.filter(x=>x.l===l&&x.s>=s),sum=z.reduce((q,x)=>q+x.n,0);return `<td class="${sum>=0?'pos':'neg'}">${fmt.format(z.length)} işlem<br>${money(sum)}<br>${z.length?money(sum/z.length):'—'} / işlem</td>`}).join('')}</tr>`).join('');
 Plotly.react('equity',[{type:'scatter',mode:'lines',x:ex,y:ey,line:{color:blue,width:2},fill:'tozeroy',fillcolor:'rgba(108,184,255,.07)',name:'Kasa'},{type:'scatter',mode:'lines',x:ex.length?[ex[0],ex[ex.length-1]]:[],y:[1000,1000],line:{color:muted,dash:'dot'},name:'Başlangıç'}],{...layout,yaxis:{...layout.yaxis,title:'USDT'}},opt);
 bar('daily',byExitDay);
 bar('annual',agg(a,x=>x.y).sort((x,y)=>x.k-y.k));bar('monthly',agg(a,x=>x.m).sort((x,y)=>String(x.k).localeCompare(String(y.k))));bar('season',agg(a,x=>x.m.slice(5)).sort((x,y)=>String(x.k).localeCompare(String(y.k))));
 const heat=agg(a,x=>x.m),years=Array.from({length:8},(_,i)=>2019+i),months=Array.from({length:12},(_,i)=>String(i+1).padStart(2,'0'));let hm=new Map(heat.map(x=>[x.k,x.net]));
 Plotly.react('heatmap',[{type:'heatmap',x:months.map(x=>x+'. ay'),y:years,z:years.map(y=>months.map(m=>hm.has(y+'-'+m)?hm.get(y+'-'+m):null)),colorscale:[[0,red],[.5,'#233447'],[1,green]],zmid:0,hovertemplate:'%{y} %{x}<br>%{z:.2f} USDT<extra></extra>'}],{...layout,margin:{l:50,r:15,t:10,b:45}},opt);
 const coin=agg(a,x=>x.c).sort((x,y)=>y.n-x.n),top=[...coin].sort((x,y)=>y.n-x.n).slice(0,30).reverse();bar('coins',top,true);bar('hours',agg(a,x=>String(x.h).padStart(2,'0')+':00').sort((x,y)=>String(x.k).localeCompare(String(y.k))));
 bar('patterns',agg(a,x=>'Formasyon '+x.p).sort((x,y)=>String(x.k).localeCompare(String(y.k))));bar('variants',agg(a,x=>x.v).sort((x,y)=>String(x.k).localeCompare(String(y.k))));
 bar('timeframes',agg(a,x=>x.tf));
 bar('horizons',agg(a,x=>x.hz+' mum').sort((x,y)=>parseInt(x.k)-parseInt(y.k)));
 const buckets=x=>x.dur<60?'<1 saat':x.dur<360?'1–6 saat':x.dur<1440?'6–24 saat':x.dur<4320?'1–3 gün':'>3 gün';bar('holding',agg(a,buckets));
 Plotly.react('hist',[{type:'histogram',x:a.filter(x=>x.n>=0).map(x=>x.n),name:'Kâr',marker:{color:green},nbinsx:50},{type:'histogram',x:a.filter(x=>x.n<0).map(x=>x.n),name:'Zarar',marker:{color:red},nbinsx:50}],{...layout,barmode:'overlay',xaxis:{...layout.xaxis,title:'İşlem başı net USDT'}},opt);
 const samples=a.length>7000?a.filter((_,i)=>i%Math.ceil(a.length/7000)===0):a;Plotly.react('scores',[{type:'scattergl',mode:'markers',x:samples.map(x=>x.sc),y:samples.map(x=>x.n),marker:{color:samples.map(x=>x.n>=0?green:red),size:5,opacity:.43},hovertemplate:'Puan %{x:.3f}<br>Net %{y:.2f} USDT<extra></extra>'}],{...layout,xaxis:{...layout.xaxis,title:'V4 puanı (USDT)'},yaxis:{...layout.yaxis,title:'Net USDT'}},opt);
 bar('reasons',agg(a,x=>x.r).sort((x,y)=>y.n-x.n));
 const oldSet=new Set(META.old_symbols),activeCoins=(f.coin==='all'?META.active_symbols:[f.coin]).filter(c=>f.cohort==='all'||(oldSet.has(c)?'old':'new')===f.cohort),byCoin=new Map(coin.map(x=>[x.k,x]));document.getElementById('coinTable').innerHTML=activeCoins.map(c=>byCoin.get(c)||{k:c,n:0,pos:0,neg:0,net:0}).sort((x,y)=>y.net-x.net).map(x=>`<tr><td>${safe(x.k)}</td><td>${fmt.format(x.n)}</td><td class="pos">${money(x.pos)}</td><td class="neg">${money(x.neg)}</td><td class="${x.net>=0?'pos':'neg'}">${money(x.net)}</td><td>${x.n?money(x.net/x.n):'—'}</td></tr>`).join('');
 VIEW=[...a].sort((x,y)=>y.t-x.t);PAGE=0;renderTable();}
function renderTable(){const q=document.getElementById('search').value.toLowerCase(),a=VIEW.filter(x=>x.c.toLowerCase().includes(q)||x.v.toLowerCase().includes(q));let pages=Math.max(1,Math.ceil(a.length/100));PAGE=Math.min(PAGE,pages-1);document.getElementById('pageInfo').textContent=`${PAGE+1} / ${pages} · ${fmt.format(a.length)} işlem`;
 document.getElementById('tradeTable').innerHTML=a.slice(PAGE*100,(PAGE+1)*100).map(x=>`<tr><td>${safe(x.c)}</td><td>${x.s}</td><td>${x.l}x</td><td>${x.p}</td><td>${safe(x.v)}</td><td>${x.tf}</td><td>${new Date(x.t).toISOString().slice(0,16).replace('T',' ')}</td><td>${new Date(x.e).toISOString().slice(0,16).replace('T',' ')}</td><td>${safe(x.r)}</td><td class="${x.n>=0?'pos':'neg'}">${money(x.n)}</td><td>${money(x.f)}</td><td>${money(x.fee)}</td><td>${x.flag?'⚑':'—'}</td></tr>`).join('')}
for(const id of ['stage','leverage','year','period','month','coin','cohort','pattern','variant','timeframe','horizon','reason','risk','result'])document.getElementById(id).addEventListener('change',render);
document.getElementById('search').addEventListener('input',()=>{PAGE=0;renderTable()});document.getElementById('prev').onclick=()=>{PAGE=Math.max(0,PAGE-1);renderTable()};document.getElementById('next').onclick=()=>{PAGE++;renderTable()};document.getElementById('reset').onclick=()=>{for(const id of ['year','period','month','coin','cohort','pattern','variant','timeframe','horizon','reason','risk','result'])document.getElementById(id).value='all';document.getElementById('stage').value='5';document.getElementById('leverage').value='20';render()};
document.getElementById('coverage').innerHTML=`<strong>Veri kapsamı:</strong> ${fmt.format(META.universe)} aktif kontrat tarandı (exchangeInfo sunucu zamanı: ${META.universe_exchange_info_server_time}); önceki 218 coinin 173'ü bu listede, 352'si yeni, 45 eski coin artık aktif listede değil. ${fmt.format(META.archive_files_available)} Futures 1m arşivi mevcut; ${fmt.format(META.archive_files_unavailable)} eski işlem fiyatı ve ${fmt.format(META.mark_archive_files_unavailable)} mark fiyatı arşivi Binance tarafından sağlanmıyor. ${fmt.format(META.signals)} ham formasyon sinyali, ${fmt.format(META.resolved_scenarios)} çözülen kaldıraç senaryosu. Veri kesimi ${META.cutoff}. Çözülemeyen senaryolar: ${fmt.format(META.unresolved_scenarios)}. Sonuçlar yalnızca seçili kaldıraç ve aşamadaki çözülen işlemlerden hesaplanır.`;render();
</script></body></html>'''
    html = html.replace("__PAYLOAD__", payload).replace("__META__", json.dumps(meta, ensure_ascii=False))
    path = OUT / "dashboard.html"
    path.write_text(html, encoding="utf-8")
    print("dashboard", path, path.stat().st_size, "bytes", flush=True)


if __name__ == "__main__":
    main()

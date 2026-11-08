const $=id=>document.getElementById(id);
const money=v=>Number.isFinite(+v)?new Intl.NumberFormat('tr-TR',{minimumFractionDigits:2,maximumFractionDigits:2}).format(+v)+' USDT':'—';
const shortMoney=v=>Number.isFinite(+v)?new Intl.NumberFormat('tr-TR',{minimumFractionDigits:2,maximumFractionDigits:2}).format(+v):'—';
const price=v=>Number.isFinite(+v)?new Intl.NumberFormat('tr-TR',{maximumSignificantDigits:9}).format(+v):'—';
const signed=v=>(+v>0?'+':'')+money(v);
const dt=t=>t?new Intl.DateTimeFormat('tr-TR',{timeZone:'Europe/Istanbul',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(t)):'—';
const ago=t=>t?Math.max(0,Math.floor((Date.now()-t)/1000))+' sn önce':'—';
const cls=v=>+v<0?'negative':+v>0?'positive':'';
function escapeHtml(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}

function renderChart(samples){const box=$('equityChart');if(!samples?.length){box.innerHTML='<div class="empty">İlk veri noktaları bekleniyor</div>';return}
  const points=samples.length>720?samples.filter((_,i)=>i%Math.ceil(samples.length/720)===0||i===samples.length-1):samples;
  const vals=points.map(x=>+x.equity),low=Math.min(...vals,1000),high=Math.max(...vals,1000),span=Math.max(1,high-low),min=low-span*.12,max=high+span*.12;
  const W=900,H=220,L=51,R=12,T=12,B=20,pw=W-L-R,ph=H-T-B;
  const x=i=>L+pw*i/Math.max(1,points.length-1),y=v=>T+(max-v)/(max-min)*ph;
  let grid='';for(let i=0;i<4;i++){let val=min+(max-min)*i/3,yy=y(val);grid+=`<line class="gridline" x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}"/><text class="axistext" x="${L-8}" y="${yy+4}" text-anchor="end">${Math.round(val)}</text>`}
  const line=vals.map((v,i)=>(i?'L':'M')+x(i).toFixed(2)+','+y(v).toFixed(2)).join(' ');
  const area=line+` L${x(vals.length-1).toFixed(2)},${y(min).toFixed(2)} L${x(0).toFixed(2)},${y(min).toFixed(2)} Z`;
  box.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kasa eğrisi"><defs><linearGradient id="chartGradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#54e4cc" stop-opacity=".26"/><stop offset="100%" stop-color="#54e4cc" stop-opacity="0"/></linearGradient></defs>${grid}<path class="areafill" d="${area}"/><path class="trace" d="${line}"/><circle cx="${x(vals.length-1)}" cy="${y(vals.at(-1))}" r="5" fill="#61e4ce" stroke="#183e40" stroke-width="4"/></svg>`;
  $('chartStart').textContent=dt(points[0].timestamp);$('chartEnd').textContent=dt(points.at(-1).timestamp);
}

function render(d){const e=d.equity||{},net=+e.net_pnl||0,active=d.open_positions||[];
  $('equity').textContent=money(e.equity);$('equityDelta').textContent=signed(net);$('equityDelta').className=cls(net);
  $('netPnl').textContent=signed(net);$('netPnl').className='metric-value '+cls(net);
  $('minEquity').textContent=money(e.min_equity);$('maxDrawdown').textContent=(+e.max_drawdown>0?'−':'')+money(e.max_drawdown);
  $('availableCash').textContent=money(e.available_cash);$('reservedMargin').textContent=money(e.reserved_margin);$('unrealizedPnl').textContent=signed(e.unrealized_pnl);$('unrealizedPnl').className=cls(e.unrealized_pnl);
  $('openCount').textContent=active.length;$('navOpen').textContent=active.length;$('universeCount').textContent=d.scan_symbol_count||'—';
  const pct=Math.max(0,Math.min(100,(+e.reserved_margin||0)/1000*100));$('ring').style.setProperty('--usage',pct+'%');$('ringPct').textContent=Math.round(pct)+'%';
  $('lastUpdate').textContent='Son kontrol: '+ago(d.last_heartbeat_ms);
  const feed=d.filters?.market_feed?.ready,required=Object.values(d.filters||{}).every(x=>x.ready&&x.enabled),alive=d.engine_running;
  const pill=$('systemPill');pill.className='live-pill '+(!alive||!feed?'off':required?'':'wait');pill.querySelector('.dot').className='dot '+(!alive||!feed?'red':required?'green':'amber');pill.lastElementChild.textContent=!alive?'SİSTEM DURDU':!feed?'VERİ AKIŞI YOK':required?'SİSTEM AKTİF':'KONTROL GEREKİYOR';
  renderChart(d.equity_series||[]);
  const labels={formation:'Seçili 5 formasyon kolu',btc:'BTC rejim filtresi',liquidity:'İşlem büyüklüğü filtresi',v4:'Yeni V4 2020–2024',execution:'10× paper motoru',market_feed:'Binance USD-M veri akışı'};
  $('healthGrid').innerHTML=Object.entries(d.filters||{}).map(([key,item])=>`<div class="health-card"><div class="head"><span class="dot ${item.ready?'green':'red'}"></span>${escapeHtml(labels[key]||key)}</div><p>${escapeHtml(item.detail)}</p></div>`).join('');
  $('openBody').innerHTML=active.length?active.map(p=>{const mark=+p.mark_price||+p.entry_price,qty=+p.quantity,up=qty*(+p.entry_price-mark)-.001*qty*(+p.entry_price+mark)+(+p.funding_usdt||0);return `<tr><td class="coin-cell"><strong>${escapeHtml(p.symbol)}</strong><small>SHORT · 5×</small></td><td>${escapeHtml(p.strategy)}</td><td>${price(p.entry_price)}</td><td>${price(mark)}</td><td>${price(p.stop)}</td><td>${price(p.target)}</td><td>${dt(p.entry_time)}</td><td>${shortMoney(p.score)} $</td><td class="${cls(up)}">${signed(up)}</td></tr>`}).join(''):'<tr><td colspan="9" class="table-empty">Henüz açık işlem yok. Yeni sinyaller bekleniyor.</td></tr>';
  $('closedCount').textContent=(d.closed_count||0)+' tamamlandı';
  $('closedBody').innerHTML=d.recent_closed?.length?d.recent_closed.map(p=>`<tr><td class="coin-cell"><strong>${escapeHtml(p.symbol)}</strong><small>SHORT · 5×</small></td><td>${escapeHtml(p.strategy)}</td><td>${dt(p.exit_time)}</td><td><span class="reason-tag">${escapeHtml(p.exit_reason)}</span></td><td class="${cls(p.net_pnl_usdt)}">${signed(p.net_pnl_usdt)}</td></tr>`).join(''):'<tr><td colspan="5" class="table-empty">Kapanan işlem yok.</td></tr>';
  $('eventList').innerHTML=d.events?.length?d.events.map(v=>`<div class="event ${escapeHtml(v.kind)}"><i class="event-dot"></i><p>${escapeHtml(v.message)}</p><small>${dt(v.timestamp)}</small></div>`).join(''):'<div class="empty">Olaylar bekleniyor</div>';
}
async function refresh(){try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);render(await r.json())}catch(e){const pill=$('systemPill');pill.className='live-pill off';pill.querySelector('.dot').className='dot red';pill.lastElementChild.textContent='BAĞLANTI KESİLDİ';$('lastUpdate').textContent=String(e)}}
function tick(){$('clock').textContent=new Intl.DateTimeFormat('tr-TR',{timeZone:'Europe/Istanbul',weekday:'short',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date())}
tick();setInterval(tick,1000);refresh();setInterval(refresh,15000);

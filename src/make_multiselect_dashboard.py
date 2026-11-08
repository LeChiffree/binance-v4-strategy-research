from pathlib import Path
import json, re

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'output/v4_2020_2024_leverage_volume'
path=OUT/'dashboard.html'
s=path.read_text(encoding='utf-8')

old='<div class="control"><label for="pattern">Formasyon ailesi</label><select id="pattern"><option value="all">Tüm formasyonlar</option></select></div><div class="control"><label for="variant">12 formasyon/varyant</label><select id="variant"><option value="all">Grubun tüm varyantları</option></select></div>'
new='''<div class="control"><label>Formasyon aileleri</label><div id="patternChecks" class="checklist family-list"></div></div><div class="control"><label>12 formasyon/varyant</label><div class="check-actions"><button type="button" data-preset="all">Tümü</button><button type="button" data-preset="core">Ana 5</button><button type="button" data-preset="none">Temizle</button></div><div id="variantChecks" class="checklist variant-list"></div></div>'''
if old in s:s=s.replace(old,new)

css=""".checklist{border:1px solid #34475a;border-radius:9px;background:#122131;padding:7px;display:grid;gap:4px}.variant-list{max-height:285px;overflow:auto}.check-item{display:flex!important;align-items:flex-start;gap:8px;margin:0!important;padding:6px 5px;border-radius:6px;color:#c5d5df!important;font-size:11px!important;font-weight:500!important;line-height:1.25}.check-item:hover{background:#1b3043}.check-item input{width:15px;height:15px;min-width:15px;margin:0;padding:0;accent-color:#6caaff}.check-item span{overflow-wrap:anywhere}.check-actions{display:flex;gap:5px;margin:0 0 7px}.check-actions button{padding:5px 8px;font-size:10px;flex:1}.family-list{grid-template-columns:1fr 1fr}.family-list .check-item{align-items:center}"""
if '.checklist{' not in s:s=s.replace('@media(max-width:1050px)',css+'@media(max-width:1050px)')

start=s.index("const pids=[...new Set(rows.map(r=>r.pattern_id))]")
end=s.index("$('coin').addEventListener('input',render)",start)
multi="""const pids=[...new Set(rows.map(r=>r.pattern_id))].sort((a,b)=>a-b),combos=[...new Map(rows.map(r=>[`${r.pattern_id}|${r.variant}|${r.timeframe}`,r])).values()].sort((a,b)=>(PN[a.pattern_id]+a.variant).localeCompare(PN[b.pattern_id]+b.variant,'tr')),core=new Set(['65|close_2r_h100|4h','65|retest_2r|4h','9|close_2r|4h','16|close_2r_h25|1h','21|close_2r|1h']);$('patternChecks').innerHTML=pids.map(v=>`<label class="check-item"><input type="checkbox" value="${v}" checked><span>${PN[v]||v}</span></label>`).join('');$('variantChecks').innerHTML=combos.map(r=>{let k=`${r.pattern_id}|${r.variant}|${r.timeframe}`;return `<label class="check-item"><input type="checkbox" value="${k}" data-pattern="${r.pattern_id}" checked><span>${PN[r.pattern_id]} · ${r.variant} · ${r.timeframe}</span></label>`}).join('');function checkValues(id){return new Set([...$(id).querySelectorAll('input:checked')].map(x=>x.value))}function syncFamilyState(){for(const box of $('patternChecks').querySelectorAll('input')){let vs=[...$('variantChecks').querySelectorAll(`input[data-pattern="${box.value}"]`)],n=vs.filter(x=>x.checked).length;box.checked=n===vs.length;box.indeterminate=n>0&&n<vs.length}}$('patternChecks').addEventListener('change',e=>{if(e.target.matches('input')){for(const v of $('variantChecks').querySelectorAll(`input[data-pattern="${e.target.value}"]`))v.checked=e.target.checked;syncFamilyState();render()}});$('variantChecks').addEventListener('change',()=>{syncFamilyState();render()});for(const b of document.querySelectorAll('[data-preset]'))b.addEventListener('click',()=>{let mode=b.dataset.preset;for(const v of $('variantChecks').querySelectorAll('input'))v.checked=mode==='all'||(mode==='core'&&core.has(v.value));syncFamilyState();render()});for(const id of ['universe','year','leverage','stage','timeframe','chartCount'])$(id).addEventListener('change',render);"""
s=s[:start]+multi+s[end:]

s=s.replace("tf:$('timeframe').value,pattern:$('pattern').value,variant:$('variant').value,coin:","tf:$('timeframe').value,patterns:checkValues('patternChecks'),variants:checkValues('variantChecks'),coin:")
s=s.replace("(f.tf==='all'||r.timeframe===f.tf)&&(f.pattern==='all'||r.pattern_id===+f.pattern)&&(f.variant==='all'||`${r.pattern_id}|${r.variant}|${r.timeframe}`===f.variant)&&","(f.tf==='all'||r.timeframe===f.tf)&&f.patterns.has(String(r.pattern_id))&&f.variants.has(`${r.pattern_id}|${r.variant}|${r.timeframe}`)&&")
s=s.replace("${f.pattern==='all'?'12 varyant':PN[+f.pattern]}","${f.variants.size} / 12 varyant")
s=s.replace('Soldaki formasyon ailesi ve 12 varyant filtresi bütün dashboardu günceller. Bir aile seçildiğinde kendi varyantları burada karşılaştırılır.','Soldaki checkboxlarla birden fazla formasyon veya varyantı birlikte seçebilirsin; bütün dashboard seçilen kombinasyonu gösterir.')
path.write_text(s,encoding='utf-8')

# Rebuild the file:// friendly version.
d=json.loads((OUT/'dashboard_data.json').read_text(encoding='utf-8'))
plotly=(ROOT/'output/active_525_five_stage/plotly.min.js').read_text(encoding='utf-8')
offline=s.replace('<script src="../active_525_five_stage/plotly.min.js"></script>','<script>'+plotly.replace('</script','<\\/script')+'</script>')
fetch="fetch('./dashboard_data.json').then(r=>{if(!r.ok)throw Error(r.status);return r.json()}).then(normalize).catch(e=>{$('loading').textContent='Veri açılamadı: '+e.message+' · Dashboardu localhost sunucusundan açın.';console.error(e)});"
offline=offline.replace(fetch,'normalize('+json.dumps(d,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')+');')
(OUT/'V4_2020_2024_Dashboard_Offline.html').write_text(offline,encoding='utf-8')
print('multi-select dashboard ready')

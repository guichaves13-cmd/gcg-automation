// StudioPilot Pro - Web Edition JS
function showPage(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  const page=document.getElementById('page-'+id);
  if(page)page.classList.add('active');
  document.querySelectorAll('.nav-btn').forEach(b=>{if(b.textContent.toLowerCase().includes(id)||b.getAttribute('onclick')?.includes(id))b.classList.add('active')});
  if(id==='dashboard')loadDashboard();
  if(id==='planner')loadPlanner();
  if(id==='gallery')loadGallery();
  if(id==='settings')loadKeys();
  if(id==='sync')loadUploads();
  if(id==='diagnostic')runDiagnostics();
  if(id==='scheduler')loadSchedules();
  if(id==='templates')loadTemplates();
  if(id==='thumbnail')loadThumbnailDefaults();
  if(id==='social')loadSocialStatus();
  if(id==='auto-mode')checkAutoStatus();
}

function toast(msg,dur=3000){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),dur)}
function loading(on,text){const l=document.getElementById('loading');if(on){document.getElementById('loading-text').textContent=text||'Processando...';l.classList.add('active')}else l.classList.remove('active')}

async function previewVoice(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel || !sel.value) return toast("Selecione uma voz primeiro!");
  
  const btn = event.target;
  const oldText = btn.textContent;
  btn.textContent = "Carregando...";
  btn.disabled = true;
  
  try {
    const res = await fetch("/api/voice_preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice: sel.value })
    });
    
    if (res.ok) {
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      btn.textContent = "Tocando...";
      audio.play();
      audio.onended = () => {
        btn.textContent = oldText;
        btn.disabled = false;
      };
    } else {
      throw new Error("Erro na prévia");
    }
  } catch (e) {
    toast("Erro ao gerar áudio: " + e.message);
    btn.textContent = oldText;
    btn.disabled = false;
  }
}
function fmtBytes(b){if(b>=1e9)return(b/1e9).toFixed(1)+' GB';if(b>=1e6)return(b/1e6).toFixed(1)+' MB';if(b>=1e3)return(b/1e3).toFixed(1)+' KB';return b+' B'}
function escHtml(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}

async function runDiagnostics(){
  const statusEl = document.getElementById('health-status');
  if(!statusEl)return;
  statusEl.innerHTML = 'Executando diagnóstico completo (testes de API, disco, FFmpeg, Whisper)...';
  statusEl.style.color = 'var(--text)';
  try {
    const r = await fetch('/api/pipeline/diagnose');
    const d = await r.json();
    const ok = s => s ? '<span style="color:var(--green)">✔ OK</span>' : '<span style="color:var(--red)">✘ FALHA</span>';
    const warn = s => s ? '<span style="color:var(--green)">✔ OK</span>' : '<span style="color:var(--yellow)">⚠ Ausente</span>';
    const statusColor = d.status==='healthy'?'var(--green)':d.status==='issues'?'var(--yellow)':'var(--red)';
    let txt = `<b>Status Geral:</b> <span style="color:${statusColor};font-weight:700">${d.status.toUpperCase()}</span>`;
    txt += ` <span style="color:var(--dim);font-size:11px">Python ${d.python_version}</span><br><br>`;
    txt += `<b>🛠 Ferramentas:</b><br>`;
    txt += `&nbsp;&nbsp;FFmpeg: ${ok(d.checks.ffmpeg)}<br>`;
    txt += `&nbsp;&nbsp;FFprobe: ${ok(d.checks.ffprobe)}<br>`;
    txt += `&nbsp;&nbsp;Whisper: ${warn(d.checks.whisper)}<br>`;
    txt += `<b>🔑 APIs:</b><br>`;
    txt += `&nbsp;&nbsp;Google AI: ${ok(d.checks.google_ai)} ${d.checks.gemini_api?'<span style="color:var(--green);font-size:11px">(Gemini respondeu)</span>':'<span style="color:var(--dim);font-size:11px">(sem teste)</span>'}<br>`;
    txt += `&nbsp;&nbsp;Pexels: ${ok(d.checks.pexels)}<br>`;
    txt += `&nbsp;&nbsp;Pixabay: ${ok(d.checks.pixabay)}<br>`;
    txt += `&nbsp;&nbsp;NVIDIA GLM: ${d.checks.glm_api?'<span style="color:var(--green)">✔ OK</span>':'<span style="color:var(--dim)">—</span>'}<br>`;
    txt += `<b>💾 Sistema:</b><br>`;
    txt += `&nbsp;&nbsp;Disco livre: ${d.checks.disk_free_gb}GB<br>`;
    txt += `&nbsp;&nbsp;Uploads (avatar): ${d.checks.avatar_uploads} arquivo(s)<br>`;
    txt += `&nbsp;&nbsp;Diretório: ${d.checks.output_dir}<br>`;
    if(d.issues.length>0){
      txt += `<br><b>🚨 Problemas (${d.issues.length}):</b><br>`;
      d.issues.forEach((issue,i)=>{
        const fix = d.fixes[i] ? `<br><span style="color:var(--yellow);font-size:11px;margin-left:16px">💡 ${escHtml(d.fixes[i])}</span>` : '';
        txt += `<span style="color:var(--red)">✘</span> ${escHtml(issue)}${fix}<br>`;
      });
    } else {
      txt += `<br><span style="color:var(--green)">✅ Nenhum problema detectado!</span>`;
    }
    statusEl.innerHTML = txt;
    statusEl.style.color = d.status==='healthy'?'var(--green)':'var(--accent)';
    // Also load recovery log
    loadRecoveryLog();
  } catch(e) {
    statusEl.innerHTML = 'Falha ao conectar: ' + e.message;
    statusEl.style.color = '#ef4444';
  }
}

async function autoFixAll(){
  const statusEl = document.getElementById('health-status');
  if(!statusEl)return;
  statusEl.innerHTML = 'Aplicando correcoes automaticas...';
  statusEl.style.color = 'var(--text)';
  try {
    // 1. Clean temp dirs
    await fetch('/api/clean', {method: 'POST'});
    // 2. Reset pipeline if stuck
    await fetch('/api/pipeline/reset', {method: 'POST'});
    // 3. Re-run diagnostics
    statusEl.innerHTML = 'Correcoes aplicadas. Re-executando diagnostico...';
    await runDiagnostics();
    toast('Auto-correcao concluida!');
  } catch(e) {
    statusEl.innerHTML = 'Auto-correcao falhou: ' + e.message;
    statusEl.style.color = '#ef4444';
  }
}

async function loadRecoveryLog(){
  const logEl = document.getElementById('recovery-log');
  if(!logEl)return;
  try {
    const r = await fetch('/api/system/recovery-log');
    const d = await r.json();
    if(!d.log || d.log.length===0){
      logEl.innerHTML = '<span style="color:var(--dim)">Nenhum evento de recuperacao registrado.</span>';
      return;
    }
    let txt = '';
    d.log.forEach(entry => {
      const color = entry.result==='success'?'var(--green)':entry.result==='failed'?'var(--red)':'var(--yellow)';
      txt += `<span style="color:var(--dim)">${escHtml(entry.time.substring(11,19))}</span> `;
      txt += `<b style="color:${color}">${entry.result.toUpperCase()}</b> `;
      txt += `<span style="color:var(--dim)">${escHtml(entry.action)}</span>`;
      txt += ` <span style="font-size:11px;color:var(--dim)">${escHtml(entry.detail.substring(0,80))}</span><br>`;
    });
    logEl.innerHTML = txt;
  } catch(e) {
    logEl.innerHTML = 'Erro ao carregar: ' + e.message;
  }
}

// DASHBOARD
let _chartProd = null, _chartPipe = null;

function renderProductionChart(data) {
  if (!data || !data.daily_7) return;
  const canvas = document.getElementById('chart-productions');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (_chartProd) { _chartProd.destroy(); }
  const labels = data.daily_7.map(d => {
    const dt = new Date(d.date + 'T00:00:00');
    return dt.toLocaleDateString('pt-BR', { weekday: 'short', day: 'numeric' });
  });
  const counts = data.daily_7.map(d => d.count || 0);
  document.getElementById('chart-7-label').textContent = data.daily_7.some(d => d.count > 0)
    ? 'Total: ' + counts.reduce((a,b) => a+b, 0) + ' videos'
    : 'Nenhuma producao nos ultimos 7 dias';
  _chartProd = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Videos',
        data: counts,
        backgroundColor: 'rgba(6,182,212,0.7)',
        borderColor: '#06b6d4',
        borderWidth: 1,
        borderRadius: 6,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { stepSize: 1, color: '#6b6b8d' }, grid: { color: 'rgba(255,255,255,0.03)' } },
        x: { ticks: { color: '#6b6b8d', font: { size: 10 } }, grid: { display: false } }
      }
    }
  });
}

function renderPipelineChart(data) {
  if (!data || !data.pipeline_breakdown) return;
  const canvas = document.getElementById('chart-pipelines');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (_chartPipe) { _chartPipe.destroy(); }
  const entries = Object.entries(data.pipeline_breakdown);
  if (!entries.length) {
    document.getElementById('chart-pipe-count').textContent = 'Sem dados';
    return;
  }
  document.getElementById('chart-pipe-count').textContent = entries.reduce((a, [,c]) => a+c, 0) + ' execucoes';
  const colors = ['#06b6d4', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#ec4899'];
  _chartPipe = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: entries.map(([k]) => k),
      datasets: [{
        data: entries.map(([,v]) => v),
        backgroundColor: entries.map((_, i) => colors[i % colors.length]),
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom', labels: { color: '#6b6b8d', font: { size: 10 }, padding: 8 } }
      },
      cutout: '65%',
    }
  });
}

async function loadDashboard(){
  const now=new Date();
  const days=['Domingo','Segunda-feira','Terca-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sabado'];
  const months=['janeiro','fevereiro','marco','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
  document.getElementById('dash-date').textContent=`${days[now.getDay()]}, ${now.getDate()} de ${months[now.getMonth()]}  ${now.toLocaleTimeString('pt-BR')}`;
  const h=now.getHours();
  const greeting=h<12?'Bom dia':h<18?'Boa tarde':'Boa noite';
  document.querySelector('.banner h2').innerHTML=`${greeting}, Guilherme <span class="wave">👋</span>`;
  // Stats
  try{
    const s=await(await fetch('/api/stats')).json();
    document.getElementById('stat-today').textContent=s.today||0;
    document.getElementById('stat-total').textContent=s.total||0;
    const al=document.getElementById('activity-list');
    if(s.history&&s.history.length){
      al.innerHTML=s.history.slice(0,5).map(h=>`<div class="activity-item"><span class="activity-dot"></span><span class="activity-text">✅ ${escHtml(h.f)}</span><span class="activity-time">${h.d}</span></div>`).join('');
    }
  }catch(e){}
  try{
    const st=await(await fetch('/api/storage')).json();
    document.getElementById('stat-storage').textContent=fmtBytes(st.total_bytes);
  }catch(e){}
  try{
    const plans=await(await fetch('/api/plans')).json();
    document.getElementById('stat-pipeline').textContent=plans.length;
    const counts={idea:0,script:0,prompts:0,production:0,done:0};
    plans.forEach(p=>counts[p.status]=(counts[p.status]||0)+1);
    document.getElementById('pipe-plan').textContent=counts.idea+counts.script;
    document.getElementById('pipe-prod').textContent=counts.prompts+counts.production;
    document.getElementById('pipe-done').textContent=counts.done;
  }catch(e){}
  // Analytics charts
  try{
    const a = await(await fetch('/api/analytics/overview')).json();
    renderProductionChart(a);
    renderPipelineChart(a);
  }catch(e){ console.error('Chart error:', e); }
}

// PLANNER — with full drag-and-drop
let _draggingPlanId = null;
const _colStatus = {'k-idea':'idea','k-script':'script','k-prompts':'prompts','k-prod':'production','k-done':'done'};

async function loadPlanner(){
  try{
    const plans = await(await fetch('/api/plans')).json();
    const colIds = ['k-idea','k-script','k-prompts','k-prod','k-done'];
    const counts = {idea:0,script:0,prompts:0,production:0,done:0};
    const statusColors = {idea:'var(--yellow)',script:'var(--accent)',prompts:'var(--purple)',production:'var(--orange)',done:'var(--green)'};

    colIds.forEach(colId => {
      const el = document.getElementById(colId);
      el.innerHTML = '';
      el.ondragover = e => { e.preventDefault(); el.classList.add('drag-over'); };
      el.ondragleave = e => { if(!el.contains(e.relatedTarget)) el.classList.remove('drag-over'); };
      el.ondrop = e => {
        e.preventDefault();
        el.classList.remove('drag-over');
        if(_draggingPlanId !== null) movePlan(_draggingPlanId, _colStatus[colId]);
      };
    });

    plans.forEach(p => {
      const status = p.status || 'idea';
      const colId = Object.keys(_colStatus).find(k => _colStatus[k] === status) || 'k-idea';
      counts[status] = (counts[status]||0) + 1;
      const color = statusColors[status] || 'var(--dim)';

      const item = document.createElement('div');
      item.className = 'kanban-item';
      item.draggable = true;
      item.dataset.id = p.id;
      item.style.borderLeft = `3px solid ${color}`;
      item.innerHTML = `<div class="ki-header"><div class="ki-title">${escHtml(p.title)}</div><button class="ki-del" onclick="deletePlan(${p.id},event)" title="Deletar">✕</button></div><div class="ki-date">${p.date||''}</div>`;

      item.ondragstart = e => {
        _draggingPlanId = p.id;
        item.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      };
      item.ondragend = () => {
        _draggingPlanId = null;
        item.classList.remove('dragging');
        document.querySelectorAll('.kanban-items').forEach(c => c.classList.remove('drag-over'));
      };

      document.getElementById(colId).appendChild(item);
    });

    const countMap = {idea:'k-idea-n',script:'k-script-n',prompts:'k-prompts-n',production:'k-prod-n',done:'k-done-n'};
    Object.entries(countMap).forEach(([k,id]) => {
      const el = document.getElementById(id);
      if(el) el.textContent = counts[k]||0;
    });
  }catch(e){ console.error(e); }
}

async function movePlan(id, status) {
  await fetch('/api/plans/move', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id, status})});
  loadPlanner();
  const labels = {idea:'Ideia',script:'Roteiro',prompts:'Prompts',production:'Producao',done:'Pronto'};
  toast('Movido para ' + (labels[status]||status) + '!');
}

async function deletePlan(id, event) {
  event.stopPropagation();
  if(!confirm('Deletar este projeto?')) return;
  await fetch('/api/plans/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
  loadPlanner();
  toast('Projeto deletado!');
}

async function addPlan(){
  const title = prompt('Nome do projeto:');
  if(!title) return;
  await fetch('/api/plans',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,status:'idea'})});
  loadPlanner();
  toast('Projeto adicionado!');
}

// GALLERY
async function loadGallery(){
  const container=document.getElementById('gallery-list');
  const countEl=document.getElementById('gallery-count');
  if(!container)return;
  container.innerHTML='<p style="color:var(--dim)">Carregando...</p>';
  try{
    const resp=await fetch('/api/gallery?t='+Date.now()); // bust cache
    const d=await resp.json();
    if(d.error) throw new Error(d.error);
    if(countEl) countEl.textContent=d.total+' arquivo(s) encontrado(s)';
    if(!d.files||!d.files.length){
      container.innerHTML='<p style="color:var(--dim)">Nenhum video encontrado. A pasta de saida esta vazia ou nenhum video foi gerado ainda.</p>';
      return;
    }
    container.innerHTML='';
    d.files.forEach(f=>{
      const icon=f.ext==='.mp4'?'🎬':f.ext==='.srt'?'📝':'📄';
      const sizeLabel=f.size_mb>=1000?(f.size_mb/1024).toFixed(1)+' GB':f.size_mb+' MB';
      const encodedName=encodeURIComponent(f.name);
      const div=document.createElement('div');
      div.className='gallery-item';
      div.id='gitem-'+encodedName;
      div.style.cssText='display:flex;align-items:center;justify-content:space-between;padding:14px 16px;background:rgba(6,182,212,.05);border:1px solid rgba(6,182,212,.15);border-radius:12px;margin-bottom:10px;transition:opacity .3s';
      const info=document.createElement('div');
      info.style.cssText='flex:1;min-width:0';
      const shortName=f.name.length>55?f.name.substring(0,52)+'...':f.name;
      info.innerHTML='<div style="font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="'+escHtml(f.name)+'">'+icon+' '+escHtml(shortName)+'</div><div style="font-size:12px;color:var(--dim);margin-top:4px">'+sizeLabel+' &bull; '+f.modified+'</div>';
      const btns=document.createElement('div');
      btns.style.cssText='display:flex;gap:6px;margin-left:12px;flex-shrink:0';
      if(f.ext==='.mp4'){
        const prevBtn=document.createElement('button');
        prevBtn.className='btn btn-ghost';
        prevBtn.style.cssText='font-size:12px;padding:7px 12px';
        prevBtn.innerHTML='&#x25B6; Preview';
        prevBtn.addEventListener('click',()=>previewVideo(f.name));
        btns.appendChild(prevBtn);
      }
      const dlLink=document.createElement('a');
      dlLink.className='btn btn-primary';
      dlLink.style.cssText='font-size:12px;text-decoration:none;padding:7px 12px';
      dlLink.innerHTML='&#x2B07; Baixar';
      dlLink.href='/api/download/'+encodedName;
      btns.appendChild(dlLink);
      const delBtn=document.createElement('button');
      delBtn.className='btn btn-ghost';
      delBtn.style.cssText='font-size:12px;padding:7px 10px;color:var(--red);border-color:rgba(239,68,68,.3)';
      delBtn.innerHTML='&#x1F5D1;';
      delBtn.title='Apagar arquivo';
      delBtn.addEventListener('click',()=>deleteGalleryFile(f.name, div));
      btns.appendChild(delBtn);
      div.appendChild(info);
      div.appendChild(btns);
      container.appendChild(div);
    });
  }catch(e){container.innerHTML='<p style="color:#ef4444">Erro ao carregar galeria: '+escHtml(e.message)+'</p>'}
}

async function deleteGalleryFile(name, rowEl){
  if(!confirm('Apagar "'+name+'" e arquivos relacionados (.srt, relatorio)?')) return;
  rowEl.style.opacity='0.4';
  try{
    const r=await fetch('/api/gallery/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
    if(!r.ok){
      const txt=await r.text();
      throw new Error('Servidor retornou '+r.status+'. Reinicie o servidor e tente novamente.');
    }
    const d=await r.json();
    if(d.error){toast('Erro: '+d.error);rowEl.style.opacity='1';return;}
    rowEl.remove();
    toast('Arquivo apagado!');
    const countEl=document.getElementById('gallery-count');
    if(countEl){
      const cur=parseInt(countEl.textContent)||0;
      countEl.textContent=Math.max(0,cur-1)+' arquivo(s) encontrado(s)';
    }
  }catch(e){
    rowEl.style.opacity='1';
    toast(e.message.includes('Reinicie')?e.message:'Erro ao apagar: reinicie o servidor (Ctrl+C e rode novamente)');
  }
}

async function openOutputFolder(){
  try{
    await fetch('/api/gallery/open_folder',{method:'POST'});
    toast('Abrindo pasta de videos...');
  }catch(e){
    toast('Pasta: C:\\Users\\Guilherme\\Downloads\\videos ferramenta');
  }
}

function previewVideo(name){
  const modal=document.createElement('div');
  modal.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.85);z-index:9999;display:flex;align-items:center;justify-content:center;cursor:pointer';
  modal.onclick=()=>modal.remove();
  modal.innerHTML=`<div style="max-width:90%;max-height:90%" onclick="event.stopPropagation()">
    <video controls autoplay style="max-width:100%;max-height:85vh;border-radius:12px;box-shadow:0 0 40px rgba(6,182,212,.3)">
      <source src="/output/${encodeURIComponent(name)}" type="video/mp4">
    </video>
    <div style="text-align:center;margin-top:12px">
      <a href="/api/download/${encodeURIComponent(name)}" class="btn btn-primary" style="text-decoration:none">⬇ Baixar Vídeo</a>
      <button class="btn btn-ghost" onclick="this.closest('div').parentElement.remove()" style="margin-left:8px">✕ Fechar</button>
    </div>
  </div>`;
  document.body.appendChild(modal);
}

// NARRATION
async function generateNarration(){
  const topic=document.getElementById('narr-topic').value.trim();
  if(!topic){toast('Digite um tema');return}
  loading(true,'Gerando roteiro humanizado com 100+ hooks...');
  try{
    const r=await fetch('/api/narrate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      topic,language:document.getElementById('narr-lang').value,
      duration:document.getElementById('narr-dur').value,
      tone:document.getElementById('narr-tone').value,
      engine:document.getElementById('narr-engine')?.value||'gemini-2.0-flash'
    })});
    const d=await r.json();
    if(d.error){document.getElementById('narr-output').value='Erro: '+d.error;return}
    document.getElementById('narr-output').value=d.script;
    document.getElementById('narr-wordcount').textContent=d.word_count+' palavras';
    toast('Roteiro gerado! '+d.word_count+' palavras');
  }catch(e){document.getElementById('narr-output').value='Erro: '+e.message}
  finally{loading(false)}
}
function downloadScript(){
  const text=document.getElementById('narr-output').value;
  if(!text){toast('Nada para salvar');return}
  const blob=new Blob([text],{type:'text/plain'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='roteiro_'+Date.now()+'.txt';a.click();toast('Salvo!')
}
// VOICES
async function loadVoices(){
  try{
    const d=await(await fetch('/api/voices')).json();
    const selects=['pipe-voice','narr-voice'];
    selects.forEach(id=>{
      const el=document.getElementById(id);
      if(!el)return;
      el.innerHTML='';
      for(const[lang,voices]of Object.entries(d.voices)){
        const og=document.createElement('optgroup');
        og.label=(d.labels[lang]||lang)+' ('+voices.length+')';
        voices.forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;og.appendChild(o)});
        el.appendChild(og);
      }
    });
    const ct=document.getElementById('pipe-voice-count');
    if(ct)ct.textContent=d.total+' vozes';
  }catch(e){}
}
// MULTI-CONTA VEO
function showVeoTab(n){
  document.getElementById('veo-tab-1').style.display=n===1?'block':'none';
  document.getElementById('veo-tab-2').style.display=n===2?'block':'none';
  document.querySelectorAll('#page-multiconta .tab-btn').forEach((b,i)=>{b.classList.toggle('active',i===n-1)});
}


// RESEARCH
async function searchResearch(){
  const query=document.getElementById('res-query').value.trim();
  if(!query){toast('Digite um tema para pesquisar');return}
  const sources=[];
  document.querySelectorAll('#page-research .source-check input:checked').forEach(c=>sources.push(c.value));
  if(!sources.length){toast('Selecione pelo menos uma fonte');return}
  loading(true,'Pesquisando em '+sources.join(', ')+'...');
  try{
    const r=await fetch('/api/research',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query,sources})});
    const d=await r.json();
    let html=`<div class="section-card" style="margin-top:16px"><h3>📊 Resultados <span class="count">${d.total}</span></h3>`;
    if(d.results&&d.results.length){
      html+='<div class="results-grid">';
      d.results.forEach(r=>{
        const bgImg = r.preview ? `background-image: url('${r.preview}'); background-size: cover; background-position: center;` : '';
        const dlBtn = r.source === 'youtube' ? `<button onclick="downloadYoutubeClip('${r.url}', '${r.title.replace(/'/g, "\\'")}')" class="btn btn-secondary" style="font-size:11px; padding:4px 8px;">⬇ Baixar</button>` : '';
        html+=`<div class="result-card" style="display:flex; flex-direction:column;">
          <div class="rc-preview" style="height:120px; ${bgImg} position:relative; background-color:#1e1e2d;">
            <div style="position:absolute; bottom:4px; right:4px; background:rgba(0,0,0,0.7); padding:2px 6px; border-radius:4px; font-size:10px;">${r.type==='video'?'🎬':'🖼'}</div>
          </div>
          <div class="rc-info" style="padding:10px; display:flex; flex-direction:column; flex:1;">
            <div class="rc-title" style="flex:1; margin-bottom:8px; font-size:12px;">${escHtml(r.title)}</div>
            <div style="display:flex; justify-content:space-between; align-items:center; gap:4px;">
              <div class="rc-source" style="font-size:11px; opacity:0.7;">${r.source.toUpperCase()}</div>
              <div style="display:flex; gap:4px;">
                ${dlBtn}
                <a href="${r.url}" target="_blank" class="btn btn-primary" style="font-size:11px; padding:4px 8px; text-decoration:none;">🔗 Acessar</a>
              </div>
            </div>
          </div>
        </div>`;
      });
      html+='</div>';
    }else{html+='<p style="color:var(--dim)">Nenhum resultado encontrado</p>'}
    html+='</div>';
    document.getElementById('res-results').innerHTML=html;
    toast(d.total+' resultados encontrados');
  }catch(e){document.getElementById('res-results').innerHTML='<p style="color:var(--red)">Erro: '+e.message+'</p>'}
  finally{loading(false)}
}

async function downloadYoutubeClip(url, title) {
  loading(true, 'Baixando vídeo do YouTube...');
  try {
    const r = await fetch('/api/download_clip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, title })
    });
    const d = await r.json();
    if(d.error) {
      toast('Erro: ' + d.error);
    } else {
      toast('Download concluído! ' + d.name + ' salvo em Uploads.');
      loadUploads(); // refresh uploads list if on that tab
    }
  } catch(e) {
    toast('Erro de conexão: ' + e.message);
  } finally {
    loading(false);
  }
}

// KEYS
async function loadKeys(){
  try{
    const k=await(await fetch('/api/keys')).json();
    Object.entries(k).forEach(([key,val])=>{
      const el=document.getElementById('key-'+key);
      if(!el) return;
      if(key==='youtube_channels'){
        // campo texto — mostra o valor real aqui e no campo da aba Pipeline
        if(val){
          el.value=val;
          const pipeEl=document.getElementById('pipe-yt-channels');
          if(pipeEl&&!pipeEl.value) pipeEl.value=val;
        }
      } else {
        if(val) el.placeholder='••••••• (salva)';
      }
    });
  }catch(e){}
}

async function saveKeys(){
  const data={};
  ['google_ai','youtube','pexels','pixabay','unsplash','nvidia','youtube_channels'].forEach(k=>{
    const el=document.getElementById('key-'+k);
    if(el&&el.value.trim())data[k]=el.value.trim();
  });
  const r=await fetch('/api/keys/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const d=await r.json();
  document.getElementById('keys-status').textContent=`✅ ${d.saved} keys salvas!`;
  toast(d.saved+' keys salvas!');
}


// VEO3 + FLOW INTEGRATION
function updateVeo3Count(){
  const t=document.getElementById('veo3-prompts')?.value||'';
  const count=t.split('\n').filter(l=>l.trim()).length;
  const el=document.getElementById('veo3-count');
  if(el) el.textContent=count;
}
document.getElementById('veo3-prompts')?.addEventListener('input',updateVeo3Count);

function processVeo3Prompts(){
  const text=document.getElementById('veo3-prompts')?.value||'';
  if(!text.trim()){toast('Cole os prompts primeiro');return}
  fetch('/api/veo3/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompts:text})})
    .then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})
    .then(d=>toast(d.count+' prompts prontos!'))
    .catch(e=>toast('Erro: '+e.message));
}

async function connectVeoAccount(n){
  let cookies=document.getElementById('veo'+n+'-cookies').value.trim();
  let project=document.getElementById('veo'+n+'-project').value.trim();
  // If fields empty, try to use saved cookies from server
  if(!cookies || !project){
    try{
      const st=await(await fetch('/api/veo/status')).json();
      const key='veo'+n;
      if(st[key]?.connected){
        if(!cookies) toast('Cookies ja salvos no servidor');
        if(!project && st[key].project_id){
          document.getElementById('veo'+n+'-project').value=st[key].project_id;
          project=st[key].project_id;
        }
        document.getElementById('veo'+n+'-status').innerHTML='🟢 Conectada';
        document.getElementById('veo'+n+'-status').style.background='rgba(16,185,129,.1)';
        document.getElementById('veo'+n+'-status').style.color='#10b981';
        document.getElementById('veo'+n+'-status').style.borderColor='rgba(16,185,129,.2)';
        document.getElementById('veo'+n+'-log').innerHTML='✅ Conectada | Project: '+(st[key].project_id||'').substring(0,8)+'...';
        if(cookies){} // proceed to save new cookies below
        else return; // nothing to save, just show connected
      }
    }catch(e){}
    if(!cookies || !project){toast('Preencha cookies e Project ID');return}
  }
  try{
    const r=await fetch('/api/veo/connect',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({account:n,cookies,project_id:project})});
    const d=await r.json();
    if(d.error){toast('Erro: '+d.error);return}
    const st=document.getElementById('veo'+n+'-status');
    if(st){st.innerHTML='🟢 Conectada';st.style.background='rgba(16,185,129,.1)';st.style.color='#10b981';st.style.borderColor='rgba(16,185,129,.2)'}
    document.getElementById('veo'+n+'-log').innerHTML='✅ '+d.cookie_count+' cookies salvos | Project: '+d.project_id.substring(0,8)+'...';
    toast('VEO'+n+' conectada! '+d.cookie_count+' cookies');
  }catch(e){toast('Erro: '+e.message)}
}

async function sendPromptsToFlow(n){
  const text=document.getElementById('veo3-prompts')?.value||'';
  const prompts=text.split('\n').filter(l=>l.trim());
  if(!prompts.length){toast('Cole prompts primeiro');return}
  
  const model=document.querySelector('input[name="flow-model"]:checked')?.value||'veo31lite';
  const modelName=model==='veo31lite'?'Veo 3.1 Lite (0 créditos)':'Nano Banana 2 (Imagens)';
  const multiplier=parseInt(document.getElementById('flow-multiplier')?.value||'1');
  
  if(!confirm(`Enviar ${prompts.length} prompts para VEO${n}?\n\nModelo: ${modelName}\nPor prompt: x${multiplier}\nIntervalo: 3s entre cada\n\nContinuar?`))return;
  
  loading(true,'Lançando automação Flow...');
  try{
    const r=await fetch('/api/veo/launch',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({account:n,prompts,model,multiplier})});
    const d=await r.json();
    if(d.error){toast('Erro: '+d.error);return}
    
    toast('🚀 Automação lançada! '+d.prompts_count+' prompts | '+modelName);
    
    // Show monitor
    document.getElementById('veo3-monitor').style.display='block';
    document.getElementById('mon-total').textContent=d.prompts_count;
    document.getElementById('mon-model').textContent=modelName;
    document.getElementById('mon-status').innerHTML='⚡ Iniciando automação...';
    
    startAutomationMonitor();
  }catch(e){toast('Erro: '+e.message)}
  finally{loading(false)}
}

let _monIv=null;
function startAutomationMonitor(){
  if(_monIv)clearInterval(_monIv);
  _monIv=setInterval(checkAutomationStatus,3000);
}

async function checkAutomationStatus(){
  try{
    const r=await fetch('/api/veo/automation_status');
    const d=await r.json();
    
    const mon=document.getElementById('veo3-monitor');
    if(mon)mon.style.display='block';
    
    const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v};
    set('mon-current',d.sent||d.current||0);
    set('mon-total',d.total||0);
    set('mon-done',d.completed||0);
    set('mon-fail',d.failed||0);
    if(d.model)set('mon-model',d.model);
    
    const total=d.total||1;
    const sent=d.sent||0;
    const pct=Math.round(sent/total*100);
    const bar=document.getElementById('mon-bar');
    if(bar)bar.style.width=pct+'%';
    
    // Phase info
    const phaseEl=document.getElementById('mon-phase');
    const phases={setup:'⚙️ Configurando...',config:'⚙️ Selecionando modelo...',sending:'📤 Enviando prompts (10s entre cada)',downloading:'📥 Baixando resultados em ordem',done:'✅ Concluído!'};
    if(phaseEl)phaseEl.textContent=phases[d.phase]||d.phase||'';
    
    const statusEl=document.getElementById('mon-status');
    if(d.running){
      if(d.paused){
        if(statusEl)statusEl.innerHTML='⏸️ <b>PAUSADO</b> — clique Retomar para continuar';
        document.getElementById('btn-pause')?.setAttribute('disabled','');
        document.getElementById('btn-resume')?.removeAttribute('disabled');
      }else{
        const wait=d.wait_seconds?` (${d.wait_seconds}s)`:'';
        if(d.phase==='sending'){
          if(statusEl)statusEl.innerHTML=`📤 Enviando prompt <b>${sent}</b>/${total} | ${pct}%`;
        }else if(d.phase==='downloading'){
          if(statusEl)statusEl.innerHTML=`📥 Baixando... ${d.completed||0} prontos | ${d.downloads?.length||0} salvos`;
          if(bar){bar.style.width='100%';bar.style.background='var(--purple)'}
        }else{
          if(statusEl)statusEl.innerHTML=`⚡ ${sent}/${total} enviados${wait}`;
        }
        document.getElementById('btn-pause')?.removeAttribute('disabled');
        document.getElementById('btn-resume')?.setAttribute('disabled','');
      }
    }else{
      if(statusEl)statusEl.innerHTML=`✅ <b>Finalizado!</b> ${sent} enviados, ${d.downloads?.length||0} baixados, ${d.failed||0} falhas`;
      if(bar){bar.style.background='var(--green)';bar.style.width='100%'}
      if(_monIv){clearInterval(_monIv);_monIv=null}
    }
    
    // Log
    const logEl=document.getElementById('mon-log');
    if(logEl && d.log && d.log.length){
      logEl.innerHTML=d.log.map(l=>
        `<div style="padding:2px 0;color:${l.m.includes('ERRO')||l.m.includes('ERROR')?'var(--red)':l.m.includes('✓')||l.m.includes('PRONTO')||l.m.includes('SALVO')||l.m.includes('[OK]')?'var(--green)':'var(--dim)'}"><span style="color:var(--accent)">[${l.t}]</span> ${escHtml(l.m)}</div>`
      ).join('');
      logEl.scrollTop=logEl.scrollHeight;
    }
    
    // Downloads
    const dlEl=document.getElementById('mon-downloads');
    if(dlEl && d.downloads && d.downloads.length){
      dlEl.innerHTML=d.downloads.map(dl=>
        `<div style="display:flex;align-items:center;gap:8px;padding:8px;background:var(--card2);border-radius:8px;margin:4px 0;font-size:12px">
          <span style="font-size:18px;font-weight:800;color:var(--green)">#${dl.index}</span>
          <span style="flex:1;color:var(--text)">${escHtml(dl.prompt||'')}</span>
          <span style="color:var(--accent);font-weight:600">${dl.file||''}</span>
          <span style="color:var(--dim);font-size:10px">${dl.time||''}</span>
        </div>`
      ).join('');
    }
  }catch(e){if(_monIv){clearInterval(_monIv);_monIv=null}toast('Erro monitor: '+e.message)}
}

async function flowControl(action){
  try{
    await fetch('/api/veo/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    if(action==='pause')toast('⏸️ Automação pausada');
    if(action==='cancel')toast('⏹️ Automação cancelada');
    if(action==='resume')toast('▶️ Automação retomada');
    setTimeout(checkAutomationStatus,1000);
  }catch(e){toast('Erro: '+e.message)}
}

async function loadVeoStatus(){
  try{
    const r=await fetch('/api/veo/status');
    const d=await r.json();
    [1,2].forEach(n=>{
      const key='veo'+n;
      if(d[key]?.connected){
        const st=document.getElementById(key+'-status');
        if(st){st.innerHTML='🟢 Conectada';st.style.background='rgba(16,185,129,.1)';st.style.color='#10b981';st.style.borderColor='rgba(16,185,129,.2)'}
        const log=document.getElementById(key+'-log');
        if(log)log.innerHTML='✅ Conectada | Project: '+(d[key].project_id||'').substring(0,8)+'...';
        const proj=document.getElementById(key+'-project');
        if(proj&&!proj.value)proj.value=d[key].project_id||'';
      }
    });
  }catch(e){toast('Erro ao carregar status VEO: '+e.message)}
}
// Auto-load VEO status
setTimeout(loadVeoStatus,2000);

async function autoGenerateVeo3Prompts() {
  const topic=document.getElementById('veo3-topic')?.value.trim();
  if(!topic){toast('Digite um tema/assunto para gerar prompts');return}
  const qty=parseInt(document.getElementById('veo3-qty')?.value||'30');
  loading(true,'IA gerando '+qty+' prompts VEO3 cinematicos para: '+topic+'...');
  try{
    const r = await fetch('/api/veo3/auto_topic', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({topic, count:qty})
    });
    const d = await r.json();
    if(d.error){toast('Erro: '+d.error);return;}
    const textarea = document.getElementById('veo3-prompts');
    if(textarea){
      textarea.value = (d.prompts||[]).join('\n');
      updateVeo3Count();
    }
    toast(d.count+' prompts VEO3 gerados para: '+topic);
  }catch(e){
    toast('Erro: '+e.message);
  }finally{
    loading(false);
  }
}

async function autoGenerateFromAvatar() {
  if(!currentAvatarPath){
    toast('Carregue um avatar primeiro na aba Sincronizador!');
    showPage('sync');
    return;
  }
  loading(true,'IA analisando video avatar e gerando prompts VEO3...');
  try{
    const r = await fetch('/api/veo3/auto_prompts', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({avatar_path: currentAvatarPath})
    });
    const d = await r.json();
    if(d.error){toast('Erro: '+d.error);return;}
    const textarea = document.getElementById('veo3-prompts');
    if(textarea){
      textarea.value = d.prompts.join('\n');
      updateVeo3Count();
    }
    toast(d.count+' prompts VEO3 gerados do avatar!');
  }catch(e){
    toast('Erro: '+e.message);
  }finally{
    loading(false);
  }
}

// FILE UPLOAD
let currentAvatarPath = '';
function handleDrop(e){if(e.dataTransfer.files.length)uploadFile(e.dataTransfer.files[0])}

function uploadFile(file){
  if(!file)return;
  const prog=document.getElementById('upload-progress');
  prog.style.display='block';
  document.getElementById('upload-name').textContent=file.name;
  
  const fd=new FormData();
  fd.append('file',file);
  const xhr=new XMLHttpRequest();
  xhr.upload.onprogress=e=>{
    if(e.lengthComputable){
      const pct=Math.round(e.loaded/e.total*100);
      document.getElementById('upload-pct').textContent=pct+'%';
      document.getElementById('upload-bar').style.width=pct+'%';
    }
  };
  xhr.onload=function(){
    if(xhr.status===200){
      const d=JSON.parse(xhr.responseText);
      currentAvatarPath=d.path;
      document.getElementById('upload-zone').innerHTML=`<div style="font-size:40px;margin-bottom:8px">✅</div><div style="font-size:14px;font-weight:600;color:var(--green)">${d.name}</div><div style="font-size:11px;color:var(--dim)">${d.size_mb} MB - Pronto para processar</div>`;
      document.getElementById('sync-outname').value='final_'+d.name;
      toast('Arquivo carregado: '+d.name);
      loadUploads();
    }else{
      document.getElementById('upload-pct').textContent='ERRO';
      toast('Erro no upload! Servidor retornou '+xhr.status);
    }
  };
  xhr.onerror=function(){
    document.getElementById('upload-pct').textContent='ERRO';
    toast('Erro de conexao — servidor offline?');
  };
  xhr.ontimeout=function(){
    document.getElementById('upload-pct').textContent='ERRO';
    toast('Upload excedeu o tempo limite');
  };
  xhr.timeout=120000;
  xhr.open('POST','/api/upload');
  xhr.send(fd);
}

async function loadUploads(){
  try{
    const files=await(await fetch('/api/uploads')).json();
    const el=document.getElementById('uploaded-list');
    if(!files.length){el.innerHTML='<span style="color:var(--dim)">Nenhum arquivo carregado ainda</span>';return}
    el.innerHTML=files.map(f=>`<div class="activity-item" style="cursor:pointer" onclick="selectAvatar('${f.path.replace(/\\/g,'\\\\')}','${f.name}')"><span class="activity-dot" style="background:${currentAvatarPath===f.path?'var(--green)':'var(--dim)'}"></span><span class="activity-text">${escHtml(f.name)} <span style="color:var(--dim)">(${f.size_mb} MB)</span></span><span class="activity-time">${currentAvatarPath===f.path?'✅ Selecionado':'Clique para selecionar'}</span></div>`).join('');
  }catch(e){}
}

function selectAvatar(path,name){
  currentAvatarPath=path;
  document.getElementById('sync-outname').value='final_'+name;
  document.getElementById('upload-zone').innerHTML=`<div style="font-size:40px;margin-bottom:8px">✅</div><div style="font-size:14px;font-weight:600;color:var(--green)">${name}</div><div style="font-size:11px;color:var(--dim)">Selecionado - Pronto para processar</div>`;
  loadUploads();
  toast('Avatar selecionado: '+name);
}

// PIPELINE & SYNC — REAL-TIME MONITORING
let _sseSource = null;

function cancelPipeline(){
  if(_sseSource){_sseSource.close();_sseSource=null;}
  fetch('/api/pipeline/cancel',{method:'POST'}).then(()=>{
    document.getElementById('sync-status').style.display='none';
    toast('Cancelado!');
  });
}

function resetPipeline(){
  if(_sseSource){_sseSource.close();_sseSource=null;}
  fetch('/api/pipeline/reset',{method:'POST'}).then(r=>r.json()).then(d=>{
    document.getElementById('sync-status').style.display='none';
    toast('Estado resetado! Pode iniciar novo video.');
  }).catch(()=>toast('Reset enviado.'));
}

function _updateMonitorPanel(d){
  const pct = d.progress||0;
  document.getElementById('sync-pct').textContent = pct+'%';
  document.getElementById('sync-bar').style.width = pct+'%';
  document.getElementById('sync-phase-label').textContent = d.phase||'Inicializando';
  document.getElementById('sync-msg').textContent = d.message||'Aguardando...';

  // elapsed timer
  if(d.elapsed){
    const m=Math.floor(d.elapsed/60), s=Math.floor(d.elapsed%60);
    document.getElementById('sync-elapsed').textContent = `Tempo: ${m}m ${s}s`;
  }

  // 8-phase dots
  const phIdx = d.phase_idx||0;
  for(let i=1;i<=8;i++){
    const row=document.getElementById('ai-ph-'+i);
    const dot=document.getElementById('dot-'+i);
    if(!row||!dot) continue;
    row.className='ai-phase-row';
    dot.className='ai-ph-dot';
    if(i < phIdx){row.classList.add('phase-done');dot.classList.add('dot-done');dot.textContent='✓';}
    else if(i===phIdx){row.classList.add('phase-active');dot.classList.add('dot-active');dot.textContent='●';}
    else{dot.textContent='●';}
  }

  // ai_stats
  const st=d.ai_stats||{};
  const dlEl=document.getElementById('ms-dl');
  if(dlEl) dlEl.textContent=st.clips_downloaded||0;
  const okEl=document.getElementById('ms-ok');
  if(okEl) okEl.textContent=st.clips_validated||0;
  const rejEl=document.getElementById('ms-rej');
  if(rejEl) rejEl.textContent=st.clips_rejected||0;
  const segEl=document.getElementById('ms-seg');
  if(segEl) segEl.textContent=(st.segments_done||0)+'/'+(st.segments_total||0);

  // live log tail — last 8 entries
  const logs=d.logs||[];
  const logEl=document.getElementById('live-log');
  if(logEl && logs.length){
    const tail=logs.slice(-8);
    logEl.innerHTML=tail.map(e=>{
      const cls=e.level==='phase'?'log-phase':e.level==='warn'?'log-warn':'log-info';
      const ts=e.t?`<span style="color:#475569">[${e.t}s]</span> `:'';
      return `<div class="log-line ${cls}">${ts}${escHtml(e.msg)}</div>`;
    }).join('');
    logEl.scrollTop=logEl.scrollHeight;
  }

  // quality score panel (Agent 3 enhanced)
  if(phIdx >= 8){
    if(st.quality_score != null){
      const details = {
        'Audio': st.audio_score || st.quality_score,
        'Video': st.video_score || st.quality_score,
        'Sync': st.sync_score || st.quality_score,
        'B-Roll': st.broll_score || st.quality_score,
        'Legendas': st.subs_score || st.quality_score,
      };
      if(typeof showQualityScore === 'function') {
        showQualityScore(st.quality_score, details);
      } else {
        const qr=document.getElementById('quality-report');
        if(qr){
          qr.style.display='block';
          const scoreEl=document.getElementById('qr-score');
          const detailEl=document.getElementById('qr-detail');
          const pct2=Math.round(st.quality_score*100);
          if(scoreEl) scoreEl.textContent=pct2+'%';
          if(scoreEl) scoreEl.style.color=pct2>=70?'var(--green)':'var(--yellow)';
          if(detailEl) detailEl.textContent=pct2>=70?'Vídeo aprovado pelo IA Auditor Final':'Vídeo com ressalvas — verifique o relatório JSON na pasta de saída';
        }
      }
    } else {
      // Pipeline done but no quality score - hide panel
      if(typeof showQualityScore === 'function') {
        showQualityScore(-1, null);
      }
    }
  }

  // Progress ETA (Agent 3)
  if(typeof updateProgressETA === 'function') {
    updateProgressETA(phIdx, 8, d.elapsed || 0);
  }

  // done state
  if(!d.running){
    if(d.error){
      const errMsg = escHtml(d.error);
      const suggest = d.suggestion ? `<br><span style="color:var(--yellow);font-size:12px">💡 ${escHtml(d.suggestion)}</span>` : '';
      document.getElementById('sync-msg').innerHTML=`<span style="color:var(--red)">❌ Erro: ${errMsg}</span>${suggest}`;
      document.getElementById('sync-bar').style.background='var(--red)';
    } else {
      document.getElementById('sync-msg').innerHTML='✅ Vídeo finalizado! <a href="#" onclick="showPage(\'gallery\');return false;" style="color:var(--green);font-weight:700">→ Ver Galeria</a>';
      document.getElementById('sync-bar').style.background='var(--green)';
      loadDashboard();
      // Wait 5s to ensure the file is fully written before redirecting
      setTimeout(()=>{ loadGallery(); showPage('gallery'); }, 5000);
    }
    toast(d.error?'Erro no pipeline!':'Vídeo pronto!');
  }
}

let _pollFallbackIv=null;
let _sseRetryTimer=null;

function startMonitoringStream(resetPanel=true){
  if(_sseSource){_sseSource.close();_sseSource=null;}
  if(_sseRetryTimer){clearTimeout(_sseRetryTimer);_sseRetryTimer=null;}
  if(_pollFallbackIv){clearInterval(_pollFallbackIv);_pollFallbackIv=null;}

  const statusEl=document.getElementById('sync-status');
  if(statusEl) statusEl.style.display='block';

  if(resetPanel){
    document.getElementById('quality-report').style.display='none';
    document.getElementById('live-log').innerHTML='';
    document.getElementById('sync-bar').style.background='var(--accent)';
    for(let i=1;i<=8;i++){
      const row=document.getElementById('ai-ph-'+i);
      const dot=document.getElementById('dot-'+i);
      if(row){row.className='ai-phase-row';}
      if(dot){dot.className='ai-ph-dot';dot.textContent='●';}
    }
  }

  _sseSource=new EventSource('/api/pipeline/stream');
  _sseSource.onmessage=e=>{
    try{
      const d=JSON.parse(e.data);
      if(d.__done__){_sseSource.close();_sseSource=null;return;}
      _updateMonitorPanel(d);
    }catch(err){}
  };
  _sseSource.onerror=()=>{
    if(_sseSource){_sseSource.close();_sseSource=null;}
    // Check if pipeline still running; if yes, reconnect in 3s
    fetch('/api/pipeline/status').then(r=>r.json()).then(d=>{
      _updateMonitorPanel(d);
      if(d.running){
        _sseRetryTimer=setTimeout(()=>startMonitoringStream(false),3000);
      } else {
        _pollFallback();
      }
    }).catch(()=>{
      _sseRetryTimer=setTimeout(()=>startMonitoringStream(false),5000);
    });
  };
}

// Reconnect SSE when window regains focus (user un-minimizes or switches back)
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible'){
    fetch('/api/pipeline/status').then(r=>r.json()).then(d=>{
      if(d.running){
        // Pipeline still going — reconnect SSE to resume live updates
        if(!_sseSource) startMonitoringStream(false);
      } else if(_sseSource){
        _sseSource.close();_sseSource=null;
      }
      _updateMonitorPanel(d);
    }).catch(()=>{});
  }
});

window.addEventListener('focus',()=>{
  fetch('/api/pipeline/status').then(r=>r.json()).then(d=>{
    if(d.running && !_sseSource) startMonitoringStream(false);
    _updateMonitorPanel(d);
  }).catch(()=>{});
});

function _pollFallback(){
  if(_pollFallbackIv) clearInterval(_pollFallbackIv);
  _pollFallbackIv=setInterval(async()=>{
    try{
      const d=await(await fetch('/api/pipeline/status')).json();
      _updateMonitorPanel(d);
      if(!d.running){clearInterval(_pollFallbackIv);_pollFallbackIv=null;}
    }catch(e){}
  },2000);
}

async function startSync(){
  if(!currentAvatarPath){toast('Carregue um vídeo avatar primeiro!');return}
  const outname=document.getElementById('sync-outname').value.trim()||'video_'+Date.now()+'.mp4';
  const body={
    avatar_path: currentAvatarPath,
    output_name: outname,
    resolution: document.getElementById('sync-res').value,
    broll_count: parseInt(document.getElementById('sync-broll').value),
    pipeline: document.getElementById('sync-pipe').value,
    subtitles: document.getElementById('sync-subs').checked,
    music: document.getElementById('sync-music').checked,
  };
  try{
    const r=await fetch('/api/pipeline/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    if(d.error){
      if(r.status===409){
        toast('Erro: '+d.error+' — Clique em Resetar para liberar.');
        if(confirm('Existe um processo travado. Resetar o estado agora?')){resetPipeline();}
      } else {
        toast('Erro: '+d.error);
      }
      return;
    }
    toast('Pipeline iniciado!');
    startMonitoringStream();
  }catch(e){toast('Erro: '+e.message)}
}

function pollPipelineStatus(){
  // kept for legacy callers — delegates to SSE stream
  startMonitoringStream();
}

async function startPipeline(){
  const mode = document.getElementById('pipe-mode').value;

  if (mode !== 'auto') {
    // Mode has avatar or narration
    let pipeAvatarPath = currentAvatarPath || null;
    if (mode.includes('avatar')) {
      const fileInput = document.getElementById('pipe-avatar-upload');
      if (fileInput.files.length) {
        // New file uploaded - upload it first
        loading(true, 'Fazendo upload do avatar...');
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        try {
          const r = await fetch('/api/upload', { method: 'POST', body: fd });
          const d = await r.json();
          if(d.error) throw new Error(d.error);
          pipeAvatarPath = d.path;
          currentAvatarPath = d.path;
        } catch(e) {
          toast('Erro no upload: ' + e.message);
          loading(false);
          return;
        }
      }
      if (!pipeAvatarPath) {
        toast('Carregue ou selecione um vídeo de avatar primeiro!');
        return;
      }
    }
    
    // Start pipeline
    loading(true, 'Iniciando pipeline manual nas 5 IAs de Validação...');
    const brollCount = parseInt(document.getElementById('pipe-broll-count').value) || 15;
    const ytChannels = (document.getElementById('pipe-yt-channels')?.value || '').trim();
    const body={
      avatar_path: pipeAvatarPath,
      output_name: 'pipeline_' + Date.now() + '.mp4',
      resolution: '1080p',
      broll_count: brollCount,
      pipeline: mode,
      subtitles: true,
      music: true,
      prompts: document.getElementById('pipe-custom-prompts').value,
      youtube_channel_ids: ytChannels,
    };
    
    try {
      const r2=await fetch('/api/pipeline/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d2=await r2.json();
      if(d2.error){
        const hint = d2.hint ? `\n💡 ${d2.hint}` : '';
        if(r2.status===409){
          toast(`Erro: ${d2.error}${hint} — Clique em Resetar para liberar.`);
          if(confirm('Existe um processo travado. Resetar o estado agora?')){resetPipeline();}
        } else if(r2.status===400 && d2.fixes){
          const fixList = d2.fixes.join(', ');
          toast(`⚠️ ${d2.error}\nCorreções: ${fixList}`);
          // Open diagnostic page
          showPage('diagnostic');
          runDiagnostics();
        } else {
          toast(`Erro: ${d2.error}${hint}`);
        }
        return;
      }
      
      showPage('sync');
      toast('Pipeline iniciado!');
      startMonitoringStream();
    } catch(e) {
      toast('Erro de conexão.');
    } finally {
      loading(false);
    }
  } else {
    // Auto mode
    const topic = document.getElementById('pipe-topic').value;
    if(!topic){toast('Preencha o Tema/Assunto primeiro!');return;}
    toast('Iniciando tripla validação de roteiro...');
    
    // Switch to narrator tab to show generation
    document.getElementById('narr-topic').value = topic;
    document.getElementById('narr-dur').value = document.getElementById('pipe-duration').value;
    document.getElementById('narr-tone').value = document.getElementById('pipe-tone').value;
    document.getElementById('narr-engine').value = document.getElementById('pipe-engine').value;
    showPage('narrator');
    generateNarration();
  }
}

// RADAR
let _radarData = {};

async function scanRadar() {
  const query = document.getElementById('radar-query').value.trim();
  if (!query) { toast('Digite um nicho para escanear'); return; }
  const region = document.getElementById('radar-region').value;

  loading(true, `Escaneando YouTube: "${query}" na regiao ${region}...`);
  try {
    const r = await fetch('/api/radar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, region })
    });
    const d = await r.json();
    if (d.error) { toast('Erro: ' + d.error); document.getElementById('radar-results').innerHTML = `<div class="section-card" style="margin-top:16px;color:var(--red)">❌ ${escHtml(d.error)}</div>`; return; }
    if (!d.results || !d.results.length) {
      document.getElementById('radar-results').innerHTML = '<div class="section-card" style="margin-top:16px;text-align:center;padding:40px;color:var(--dim)">Nenhum resultado encontrado. Configure a chave YouTube API em Configuracoes.</div>';
      return;
    }

    const maxVph = Math.max(...d.results.map(v => v.vph||0), 1);
    _radarData = {};
    d.results.forEach(v => { _radarData[v.id] = v; });

    let html = `<div class="section-card" style="margin-top:16px"><h3>📊 Resultados do Nicho: "${escHtml(query)}" <span class="count">${d.results.length}</span></h3><div style="display:flex;flex-direction:column;gap:10px;margin-top:12px">`;

    d.results.forEach((v, i) => {
      const vphPct = Math.min(100, ((v.vph||0) / maxVph) * 100);
      const vphColor = v.vph > 200 ? 'var(--green)' : v.vph > 50 ? 'var(--yellow)' : 'var(--accent)';
      const channelName = v.channel_name || v.channel_title || 'Desconhecido';
      const videoUrl = v.url || `https://www.youtube.com/watch?v=${v.id}`;
      const isViral = v.vph > 100;
      const engBadge = v.engagement > 5 ? `<span style="color:var(--purple);font-size:11px">📊 ${v.engagement}% eng</span>` : '';

      html += `
      <div style="display:flex;gap:12px;padding:14px;background:var(--bg);border:1px solid var(--border);border-radius:12px;transition:border-color .2s" onmouseover="this.style.borderColor='var(--accent)'" onmouseout="this.style.borderColor='var(--border)'">
        <div style="flex-shrink:0;position:relative">
          <img src="${escHtml(v.thumbnail||'')}" style="width:160px;height:90px;border-radius:8px;object-fit:cover;background:var(--card)" onerror="this.style.background='var(--card)'">
          ${v.duration_text ? `<div style="position:absolute;bottom:4px;right:4px;background:rgba(0,0,0,.85);padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600">${escHtml(v.duration_text)}</div>` : ''}
          <div style="position:absolute;top:4px;left:4px;background:rgba(0,0,0,.8);border-radius:4px;padding:1px 7px;font-size:11px;font-weight:700;color:var(--dim)">#${i+1}</div>
        </div>
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:6px">
            <div style="font-size:14px;font-weight:600;flex:1;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">${escHtml(v.title)}</div>
            ${isViral ? '<span style="flex-shrink:0;background:var(--green);color:#000;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:800">🔥 VIRAL</span>' : ''}
          </div>
          <div style="font-size:12px;color:var(--dim);margin-bottom:6px">📺 ${escHtml(channelName)} • ${v.days_ago ? v.days_ago+'d atrás' : v.published||''}</div>
          <div style="display:flex;gap:12px;font-size:12px;flex-wrap:wrap;margin-bottom:6px">
            <span>👁 <b>${(v.views||0).toLocaleString('pt-BR')}</b></span>
            <span style="color:${vphColor}">⚡ <b>${(v.vph||0).toLocaleString('pt-BR')}</b> VPH</span>
            <span>👍 <b>${(v.likes||0).toLocaleString('pt-BR')}</b></span>
            ${engBadge}
          </div>
          <div style="background:var(--card);border-radius:4px;height:4px;margin-bottom:8px;overflow:hidden">
            <div style="background:${vphColor};height:100%;width:${vphPct.toFixed(1)}%;border-radius:4px;transition:width .8s ease"></div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <a href="${videoUrl}" target="_blank" class="btn btn-ghost" style="font-size:11px;padding:4px 10px;text-decoration:none">🔗 YouTube</a>
            <button onclick="copyRadarTitle('${escHtml(v.id)}')" class="btn btn-ghost" style="font-size:11px;padding:4px 10px">📋 Copiar</button>
            <button onclick="useRadarInPipeline('${escHtml(v.id)}')" class="btn btn-primary" style="font-size:11px;padding:4px 10px">🚀 Pipeline</button>
          </div>
        </div>
      </div>`;
    });
    html += '</div></div>';
    document.getElementById('radar-results').innerHTML = html;
    toast(`${d.results.length} videos encontrados em alta!`);
  } catch(e) {
    document.getElementById('radar-results').innerHTML = `<div class="section-card" style="margin-top:16px;color:var(--red)">❌ Erro: ${escHtml(e.message)}</div>`;
  } finally {
    loading(false);
  }
}

function copyRadarTitle(id) {
  const v = _radarData[id];
  if(!v) return;
  if(navigator.clipboard) {
    navigator.clipboard.writeText(v.title).then(() => toast('Titulo copiado!'));
  } else {
    const el = document.createElement('textarea');
    el.value = v.title;
    document.body.appendChild(el);
    el.select();
    document.execCommand('copy');
    el.remove();
    toast('Titulo copiado!');
  }
}

function useRadarInPipeline(id) {
  const v = _radarData[id];
  if(!v) return;
  document.getElementById('pipe-topic').value = v.title.substring(0, 100);
  showPage('pipeline');
  toast('Tema copiado para Pipeline!');
}

// MY CHANNELS
async function loadChannels(){
  const container = document.getElementById('ch-results');
  if(!container) return;
  try {
    const r = await fetch('/api/channels');
    const d = await r.json();
    const channels = d.channels || [];
    if(!channels.length) {
      container.innerHTML = '<div class="section-card" style="text-align:center;padding:40px;color:var(--dim)"><div style="font-size:48px;margin-bottom:16px">📺</div><div style="font-size:16px;font-weight:600;margin-bottom:8px">Nenhum canal adicionado ainda</div><div style="font-size:13px">Adicione canais do YouTube para usar como fonte de B-roll e monitorar tendencias.</div></div>';
      return;
    }
    let html = `<div style="display:grid;gap:12px">`;
    channels.forEach((ch, i) => {
      const subs = ch.subscribers ? (ch.subscribers >= 1000 ? (ch.subscribers/1000).toFixed(1)+'k' : ch.subscribers) : '--';
      const thumb = ch.thumbnail ? `<img src="${escHtml(ch.thumbnail)}" style="width:48px;height:48px;border-radius:50%;object-fit:cover;background:var(--card)">` : '<div style="width:48px;height:48px;border-radius:50%;background:var(--card);display:flex;align-items:center;justify-content:center;font-size:20px">📺</div>';
      html += `
        <div style="display:flex;align-items:center;gap:12px;padding:14px;background:var(--bg);border:1px solid var(--border);border-radius:12px">
          ${thumb}
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:14px">${escHtml(ch.title || ch.id)}</div>
            <div style="font-size:12px;color:var(--dim)">${ch.id} • ${subs} inscritos • ${ch.video_count||'--'} videos</div>
          </div>
          <div style="display:flex;gap:6px">
            <button class="btn btn-secondary" style="font-size:11px;padding:6px 12px" onclick="scanChannel('${ch.id}')">🔍 Escanear</button>
            <button class="btn btn-ghost" style="font-size:11px;padding:6px 10px;color:var(--red)" onclick="removeChannel('${ch.id}')">✕</button>
          </div>
        </div>`;
    });
    html += '</div>';
    container.innerHTML = html;
  } catch(e) {
    container.innerHTML = `<div style="color:var(--red)">Erro: ${escHtml(e.message)}</div>`;
  }
}

async function addChannel(){
  const input = document.getElementById('ch-add-input').value.trim();
  if(!input) { toast('Digite um ID ou URL do canal'); return; }
  loading(true, 'Adicionando canal...');
  try {
    const r = await fetch('/api/channels/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel: input })
    });
    const d = await r.json();
    if(d.error) { toast('Erro: '+d.error); return; }
    document.getElementById('ch-add-input').value = '';
    toast('Canal adicionado!');
    loadChannels();
    loadKeys();
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

async function removeChannel(id){
  if(!confirm('Remover canal?')) return;
  try {
    await fetch('/api/channels/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    toast('Canal removido!');
    loadChannels();
    loadKeys();
  } catch(e) { toast('Erro: '+e.message); }
}

async function scanChannel(id){
  loading(true, 'Escaneando canal no YouTube...');
  try {
    const r = await fetch('/api/channels/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    const d = await r.json();
    if(d.error) { toast('Erro: '+d.error); return; }
    if(!d.videos || !d.videos.length) {
      toast('Nenhum video encontrado nos ultimos 30 dias');
      return;
    }
    // Show results in a modal
    let html = `<div style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.85);z-index:9999;display:flex;align-items:center;justify-content:center" onclick="this.remove()">
      <div style="max-width:700px;max-height:80vh;overflow-y:auto;background:var(--bg);border:1px solid var(--border);border-radius:12px;padding:20px;margin:20px" onclick="event.stopPropagation()">
      <h3 style="margin-bottom:12px">📺 Videos Recentes do Canal</h3>
      <div style="display:flex;flex-direction:column;gap:8px">`;
    d.videos.forEach(v => {
      html += `<div style="display:flex;gap:10px;padding:10px;background:var(--card);border-radius:8px;align-items:center">
        <div style="flex-shrink:0;width:100px;height:56px;background:var(--card2);border-radius:6px;overflow:hidden">
          ${v.thumbnail ? `<img src="${escHtml(v.thumbnail)}" style="width:100%;height:100%;object-fit:cover">` : ''}
        </div>
        <div style="flex:1;min-width:0;font-size:13px">${escHtml(v.title||'')}</div>
        <div style="flex-shrink:0;font-size:12px;color:var(--dim)">${v.views ? (v.views/1000).toFixed(1)+'k' : '--'}</div>
      </div>`;
    });
    html += `</div></div></div>`;
    const modal = document.createElement('div');
    modal.innerHTML = html;
    document.body.appendChild(modal);
    toast(`${d.total} videos encontrados`);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// Auto-load channels on page show
document.addEventListener('click', function(e) {
  const btn = e.target.closest('.nav-btn');
  if(btn && btn.textContent.includes('Channels')) {
    setTimeout(loadChannels, 100);
  }
});

// THUMBNAIL IA
async function generateThumbnail() {
  const video = document.getElementById('thumb-video').value.trim();
  const topic = document.getElementById('thumb-topic').value.trim();
  if (!topic && !video) { toast('Preencha o tema do video'); return; }
  loading(true, 'IA analisando e gerando thumbnail...');
  try {
    const r = await fetch('/api/thumbnail/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_path: video, topic })
    });
    const d = await r.json();
    if (d.error) { toast('Erro: ' + d.error); return; }
    let html = '<div class="section-card"><h3>✅ Thumbnail Gerada</h3>';
    if (d.main) {
      html += `<img src="file://${d.main}" style="max-width:100%;border-radius:8px;margin:8px 0;border:1px solid var(--border)">`;
    }
    if (d.analysis) {
      html += `<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">`;
      html += `<span class="tag tag-cyan">Texto: ${escHtml(d.analysis.text||'')}</span>`;
      html += `<span class="tag tag-purple">Cores: ${escHtml(d.analysis.colors||'')}</span>`;
      html += `</div>`;
    }
    if (d.variants && d.variants.length) {
      html += '<h4 style="margin-top:16px">Variantes:</h4><div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:8px">';
      d.variants.forEach(v => {
        html += `<img src="file://${v}" style="width:100%;border-radius:8px;border:1px solid var(--border)">`;
      });
      html += '</div>';
    }
    html += '</div>';
    document.getElementById('thumb-results').innerHTML = html;
    toast('Thumbnail gerada!');
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

async function generateThumbnailConcepts() {
  const topic = document.getElementById('thumb-concept-topic').value.trim();
  if (!topic) { toast('Digite um tema'); return; }
  const count = parseInt(document.getElementById('thumb-concept-count').value) || 3;
  loading(true, 'Gerando conceitos de thumbnail...');
  try {
    const r = await fetch('/api/thumbnail/concepts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic, count })
    });
    const d = await r.json();
    const concepts = d.concepts || [];
    if (!concepts.length) { document.getElementById('thumb-results').innerHTML = '<div class="section-card" style="color:var(--dim)">Nenhum conceito gerado</div>'; return; }
    let html = '<div class="section-card"><h3>💡 Conceitos de Thumbnail</h3><div style="display:grid;gap:12px">';
    concepts.forEach(c => {
      html += `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:16px">
        <div style="font-size:18px;font-weight:800;margin-bottom:4px">${escHtml(c.text||'')}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
          <span class="tag tag-purple">${escHtml(c.colors||'')}</span>
          <span class="tag tag-cyan">${escHtml(c.emoji||'')}</span>
        </div>
        <div style="font-size:12px;color:var(--dim)">${escHtml(c.description||'')}</div>
      </div>`;
    });
    html += '</div></div>';
    document.getElementById('thumb-results').innerHTML = html;
    toast(concepts.length + ' conceitos gerados!');
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// SCHEDULER
async function loadSchedules() {
  try {
    const r = await fetch('/api/schedule');
    const d = await r.json();
    const scheds = d.schedules || [];
    document.getElementById('sched-count').textContent = scheds.length;
    const el = document.getElementById('sched-list');
    if (!scheds.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">⏰</div><h3>Nenhum agendamento</h3><p>Adicione agendamentos para automatizar a produção de vídeos.</p></div>';
      return;
    }
    el.innerHTML = scheds.map(s => {
      const active = s.active !== false;
      return `<div class="list-item">
        <div class="li-icon">${active ? '⏰' : '⏸️'}</div>
        <div class="li-body">
          <div class="li-title">${escHtml(s.title)}</div>
          <div class="li-sub">${escHtml(s.cron)} • Próximo: ${s.next_run ? new Date(s.next_run).toLocaleString('pt-BR') : '--'}</div>
        </div>
        <div class="li-actions">
          <span class="tag ${active ? 'tag-green' : 'tag-yellow'}">${active ? 'Ativo' : 'Pausado'}</span>
          <button class="btn btn-ghost btn-sm" onclick="toggleSchedule(${s.id})">${active ? '⏸️' : '▶️'}</button>
          <button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deleteSchedule(${s.id})">✕</button>
        </div>
      </div>`;
    }).join('');
    loadScheduleLogs();
    loadScheduleStatus();
  } catch(e) { console.error(e); }
}

async function loadScheduleLogs() {
  try {
    const r = await fetch('/api/schedule/logs');
    const d = await r.json();
    const logs = d.logs || [];
    const el = document.getElementById('sched-logs');
    if (!logs.length) { el.innerHTML = '<div style="color:var(--dim);text-align:center;padding:20px">Nenhuma execução ainda</div>'; return; }
    el.innerHTML = logs.map(l => `<div class="timeline-item" style="padding:10px 14px">
      <div class="tl-time">${l.time ? new Date(l.time).toLocaleString('pt-BR') : '--'}</div>
      <div class="tl-title">${escHtml(l.title)} <span class="tag ${l.status === 'completed' ? 'tag-green' : 'tag-red'}">${l.status}</span></div>
      ${l.error ? `<div class="tl-sub">${escHtml(l.error)}</div>` : ''}
    </div>`).join('');
  } catch(e) {}
}

async function loadScheduleStatus() {
  try {
    const r = await fetch('/api/schedule/status');
    const d = await r.json();
    const el = document.getElementById('sched-status');
    if (el) {
      el.textContent = d.running ? '▶ Ativo' : '⏸ Parado';
      el.className = 'tag ' + (d.running ? 'tag-green' : 'tag-yellow');
    }
  } catch(e) {}
}

function showAddSchedule() {
  const topic = prompt('Tema do video:');
  if (!topic) return;
  const cron = prompt('Agendamento:\n- "daily 08:00" (diario)\n- "weekly mon 09:00" (semanal)\n- "interval 60" (a cada N minutos)', 'daily 08:00');
  if (!cron) return;
  fetch('/api/schedule', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: topic, cron, config: { topic } })
  }).then(r => r.json()).then(d => {
    if (d.ok) { toast('Agendamento criado!'); loadSchedules(); }
    else toast('Erro: ' + (d.error||'unknown'));
  });
}

async function toggleSchedule(id) {
  try {
    const r = await fetch('/api/schedule');
    const d = await r.json();
    const s = (d.schedules||[]).find(x => x.id === id);
    if (!s) return;
    await fetch('/api/schedule/' + id, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: !s.active })
    });
    loadSchedules();
  } catch(e) {}
}

async function deleteSchedule(id) {
  if (!confirm('Remover este agendamento?')) return;
  await fetch('/api/schedule/' + id, { method: 'DELETE' });
  loadSchedules();
  toast('Agendamento removido!');
}

// TEMPLATES
async function loadTemplates(category) {
  try {
    const url = category ? '/api/templates?category=' + encodeURIComponent(category) : '/api/templates';
    const r = await fetch(url);
    const d = await r.json();
    const templates = d.templates || [];
    const cats = d.categories || {};
    renderTemplateCategories(cats);
    const el = document.getElementById('template-list');
    if (!templates.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">📦</div><h3>Nenhum template</h3><p>Salve configurações de pipeline como templates.</p></div>';
      return;
    }
    el.innerHTML = templates.map(t => `<div class="list-item">
      <div class="li-icon">📦</div>
      <div class="li-body">
        <div class="li-title">${escHtml(t.name)}</div>
        <div class="li-sub">${escHtml(t.category)} • ${t.description ? escHtml(t.description) : ''} • ${Object.keys(t.config||{}).length} configurações</div>
      </div>
      <div class="li-actions">
        <button class="btn btn-secondary btn-sm" onclick="applyTemplate(${t.id})">📋 Aplicar</button>
        <button class="btn btn-ghost btn-sm" onclick="deleteTemplate(${t.id})">✕</button>
      </div>
    </div>`).join('');
  } catch(e) { console.error(e); }
}

function renderTemplateCategories(cats) {
  const el = document.getElementById('template-categories');
  if (!el) return;
  let html = '<button class="tab-btn active" onclick="filterTemplates(\'\',this)">Todos (' + (Object.values(cats).reduce((a,b)=>a+b,0)) + ')</button>';
  Object.entries(cats).forEach(([cat, count]) => {
    html += `<button class="tab-btn" onclick="filterTemplates('${cat}',this)">${cat} (${count})</button>`;
  });
  el.innerHTML = html;
}

function filterTemplates(cat, btn) {
  btn.closest('#template-categories')?.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadTemplates(cat);
}

function showAddTemplate() {
  const topic = prompt('Nome do template:');
  if (!topic) return;
  const cat = prompt('Categoria (ex: documentario, tecnologia, educacao):', 'geral');
  if (!cat) return;
  const desc = prompt('Descrição curta:');
  fetch('/api/templates', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: topic, category: cat, description: desc || '',
      config: { pipeline: 'avatar_auto', resolution: '1080p', broll_count: 30 }
    })
  }).then(r => r.json()).then(d => {
    if (d.ok) { toast('Template salvo!'); loadTemplates(); }
  });
}

async function applyTemplate(id) {
  loading(true, 'Aplicando template...');
  try {
    const r = await fetch('/api/templates/' + id);
    const t = await r.json();
    if (!t.config) { toast('Template invalido'); return; }
    // Fill pipeline page with template config
    const cfg = t.config;
    if (cfg.pipeline) document.getElementById('pipe-mode').value = cfg.pipeline;
    if (cfg.resolution) document.getElementById('sync-res').value = cfg.resolution;
    if (cfg.broll_count) document.getElementById('sync-broll').value = cfg.broll_count;
    showPage('pipeline');
    toast('Template "' + t.name + '" aplicado!');
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

async function deleteTemplate(id) {
  if (!confirm('Remover template?')) return;
  await fetch('/api/templates/' + id, { method: 'DELETE' });
  loadTemplates();
  toast('Template removido!');
}

// ===== AUDIO TO VIDEO =====
async function renderAudioVideo() {
  const path = document.getElementById('atv-audio-path').value;
  if (!path) { toast('Selecione um arquivo de audio'); return; }
  loading(true,'Gerando waveform...');
  try {
    const r = await fetch('/api/audio-to-video/render', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        audio_path: path,
        title: document.getElementById('atv-title').value,
        bar_color: document.getElementById('atv-color').value,
      })
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('atv-result').innerHTML = '<video controls width="100%" src="/output/'+d.name+'"></video>';
      toast('Video gerado!');
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== CLIPPER =====
let _detectedClips = [];
async function detectClips() {
  const path = document.getElementById('clip-video-path').value;
  if (!path) { toast('Informe o caminho do video'); return; }
  loading(true,'Analisando video...');
  try {
    const r = await fetch('/api/clipper/detect', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        video_path: path,
        max_clips: parseInt(document.getElementById('clip-max').value)||5,
        min_duration: parseInt(document.getElementById('clip-min-dur').value)||15,
      })
    });
    const d = await r.json();
    if (d.clips) {
      _detectedClips = d.clips;
      let html = '<h3>Clipes detectados ('+d.total+'):</h3>';
      d.clips.forEach((c,i)=>{
        html += '<div class="card" style="margin:8px 0;padding:12px">'+
          '<b>Clip '+(i+1)+'</b> | Score: '+c.score+' | '+c.start.toFixed(1)+'s - '+c.end.toFixed(1)+'s'+
          '<br><small>'+(c.text||'').substring(0,120)+'</small>'+
          '</div>';
      });
      document.getElementById('clip-results').innerHTML = html;
      toast(d.total+' clipes detectados');
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}
async function extractAllClips() {
  if (!_detectedClips.length) { toast('Detecte clipes primeiro'); return; }
  loading(true,'Extraindo clipes...');
  try {
    const r = await fetch('/api/clipper/auto', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        video_path: document.getElementById('clip-video-path').value,
        max_clips: parseInt(document.getElementById('clip-max').value)||5,
        formats: [document.getElementById('clip-format').value],
      })
    });
    const d = await r.json();
    if (d.results) {
      let html = '<h3>Clipes extraidos:</h3>';
      d.results.forEach((r,i)=>{
        html += '<div class="card" style="margin:8px 0;padding:12px">'+
          'Clip '+(i+1)+' ('+r.format+')'+
          (r.output ? '<br><video controls width="200" src="/output/'+r.output.split(/[/\\\\]/).pop()+'"></video>' : '')+
          (r.error ? '<br><span style="color:red">'+r.error+'</span>' : '')+
          '</div>';
      });
      document.getElementById('clip-results').innerHTML = html;
      toast(d.total+' clipes extraidos');
    }
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== AI IMAGE GEN =====
async function generateImage() {
  const prompt = document.getElementById('img-prompt').value;
  if (!prompt) { toast('Digite um prompt'); return; }
  loading(true,'Gerando imagem...');
  try {
    const r = await fetch('/api/image-gen/generate', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        prompt, api: document.getElementById('img-api').value,
        width: parseInt(document.getElementById('img-width').value)||1024,
        height: parseInt(document.getElementById('img-height').value)||1024,
      })
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('img-result').innerHTML =
        '<img src="/output/ai_images/'+d.name+'" style="max-width:400px;border-radius:8px">';
      toast('Imagem gerada!');
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}
async function generateThumbnailAI() {
  const topic = document.getElementById('image-thumb-topic').value || document.getElementById('thumb-topic')?.value;
  if (!topic) { toast('Digite um topico'); return; }
  loading(true,'Gerando thumbnail...');
  try {
    const r = await fetch('/api/image-gen/thumbnail', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ topic })
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('thumb-result').innerHTML =
        '<img src="/output/ai_thumbnails/'+d.name+'" style="max-width:300px;border-radius:8px">';
      toast('Thumbnail gerada!');
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== TRANSLATE =====
async function translateText() {
  const text = document.getElementById('tr-text').value;
  if (!text) { toast('Digite o texto'); return; }
  loading(true,'Traduzindo...');
  try {
    const r = await fetch('/api/translate/text', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ text, target_language: document.getElementById('tr-lang').value })
    });
    const d = await r.json();
    if (d.translated) {
      document.getElementById('tr-result').innerHTML =
        '<div class="card" style="padding:12px"><h4>Traducao:</h4><p>'+d.translated+'</p></div>';
      toast('Traduzido!');
    } else toast('Erro: '+(d.error||'unknown'));
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}
async function dubVideo() {
  const path = document.getElementById('dub-video-path').value;
  if (!path) { toast('Informe o video'); return; }
  loading(true,'Dublando video...');
  try {
    const r = await fetch('/api/translate/dub', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        video_path: path,
        segments: [{start:0,end:300,text:'Dubbing in progress'}],
        target_language: document.getElementById('dub-lang').value,
      })
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('dub-result').innerHTML = '<video controls width="100%" src="/output/'+d.name+'"></video>';
      toast('Video dublado!');
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== SOCIAL PUBLISHER =====
async function loadSocialStatus() {
  try {
    const r = await fetch('/api/social/connections');
    const d = await r.json();
    let html = '';
    for (const [platform, info] of Object.entries(d)) {
      const connected = info.connected ? '<span style="color:#4caf50">Conectado</span>' : '<span style="color:#999">Desconectado</span>';
      html += '<div style="display:flex;justify-content:space-between;padding:6px 0">'+
        '<b>'+platform.charAt(0).toUpperCase()+platform.slice(1)+'</b> '+connected+'</div>';
    }
    document.getElementById('social-status').innerHTML = html;
    // Load videos
    const vr = await fetch('/api/social/publishable-videos');
    const vd = await vr.json();
    const sel = document.getElementById('pub-video');
    sel.innerHTML = '<option value="">Selecione...</option>';
    (vd.videos||[]).forEach(v => {
      sel.innerHTML += '<option value="'+v.path+'">'+v.name+' ('+v.size_mb+' MB)</option>';
    });
  } catch(e) { toast('Erro ao carregar: '+e.message); }
}
async function publishVideo() {
  const path = document.getElementById('pub-video').value;
  if (!path) { toast('Selecione um video'); return; }
  loading(true,'Publicando...');
  const platforms = [];
  if (document.getElementById('pub-yt').checked) platforms.push('youtube');
  if (document.getElementById('pub-fb').checked) platforms.push('facebook');
  try {
    const r = await fetch('/api/social/publish', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        video_path: path,
        title: document.getElementById('pub-title').value,
        description: document.getElementById('pub-desc').value,
        platforms,
      })
    });
    const d = await r.json();
    if (d.results) {
      let html = '<h4>Resultados:</h4>';
      for (const [p, res] of Object.entries(d.results)) {
        html += '<div>'+(res.success?'&#x2705;':'&#x274C;')+' '+p+': '+res.message+'</div>';
      }
      document.getElementById('pub-result').innerHTML = html;
    }
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== PPT TO VIDEO =====
async function convertPptToVideo() {
  const fileInput = document.getElementById('pptx-file');
  if (!fileInput.files.length) { toast('Selecione um arquivo PPTX'); return; }
  loading(true,'Convertendo apresentacao...');
  const form = new FormData();
  form.append('file', fileInput.files[0]);
  form.append('language', document.getElementById('pptx-lang').value);
  try {
    const r = await fetch('/api/ppt-to-video/convert', { method:'POST', body: form });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('pptx-result').innerHTML = '<video controls width="100%" src="/output/'+d.name+'"></video>';
      toast('Video gerado!');
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== AUTO MODE =====
async function checkAutoStatus() {
  try {
    const r = await fetch('/api/auto-mode/status');
    const d = await r.json();
    document.getElementById('auto-status').textContent = d.running ? 'Rodando' : 'Parado';
    document.getElementById('auto-status-bar').style.background = d.running ? 'var(--success)' : 'var(--bg-card)';
  } catch(e) {}
}
async function startAutoMode() {
  const topic = document.getElementById('auto-topic').value;
  if (!topic) { toast('Defina um topico'); return; }
  loading(true,'Iniciando modo autonomo...');
  try {
    const r = await fetch('/api/auto-mode/start', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ topic, interval_hours: parseInt(document.getElementById('auto-interval').value)||24 })
    });
    const d = await r.json();
    if (d.ok) {
      document.getElementById('auto-result').innerHTML = '<div class="card" style="padding:12px;background:var(--success)">Modo autonomo ativo! Proximo video em '+d.interval_hours+'h</div>';
      checkAutoStatus();
    } else toast('Erro: '+d.error);
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}
async function stopAutoMode() {
  await fetch('/api/auto-mode/stop', { method:'POST' });
  checkAutoStatus();
  toast('Modo autonomo parado');
}

// ===== SUBTITLE EDITOR =====
let _subtitleLines = [];
async function generateSubtitlesFromVideo() {
  const video = document.getElementById('sub-video').value.trim();
  if (!video) { toast('Informe o caminho do video'); return; }
  loading(true, 'Gerando legendas com Whisper...');
  try {
    const r = await fetch('/api/captions/render', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ video_path: video, generate_srt: true }) });
    const d = await r.json();
    toast(d.ok ? 'Legendas geradas!' : 'Erro: '+(d.error||''));
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}
function loadSubtitles() { toast('Carregue um arquivo SRT - funcionalidade em desenvolvimento'); }
function addSubtitleLine() { _subtitleLines.push({id:String(_subtitleLines.length+1),time:'00:00:00,000 --> 00:00:05,000',text:''}); renderSubtitleEditor(); }
function renderSubtitleEditor() {
  const el = document.getElementById('sub-editor');
  if (!_subtitleLines.length) { el.innerHTML = '<div style="color:var(--dim);padding:20px;text-align:center">Nenhuma legenda</div>'; return; }
  el.innerHTML = _subtitleLines.map((s,i) => `<div style="display:grid;grid-template-columns:40px 180px 1fr 40px;gap:8px;padding:6px;border-bottom:1px solid var(--border);align-items:center"><span style="color:var(--dim);font-size:11px">#${i+1}</span><input type="text" value="${escHtml(s.time)}" style="font-size:11px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:4px;color:var(--text)" onchange="_subtitleLines[${i}].time=this.value"><input type="text" value="${escHtml(s.text)}" style="font-size:12px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:6px;color:var(--text)" onchange="_subtitleLines[${i}].text=this.value"><button style="background:none;border:none;color:var(--red);cursor:pointer" onclick="_subtitleLines.splice(${i},1);renderSubtitleEditor()">x</button></div>`).join('');
}
function downloadSubtitles() {
  if (!_subtitleLines.length) { toast('Nenhuma legenda'); return; }
  const srt = _subtitleLines.map((s,i) => `${i+1}\n${s.time}\n${s.text}`).join('\n\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([srt])); a.download = 'legendas.srt'; a.click(); toast('SRT exportado!');
}
async function burnSubtitles() {
  const video = document.getElementById('sub-video').value.trim();
  if (!video) { toast('Informe o video'); return; }
  loading(true, 'Queimando legendas...');
  try {
    const r = await fetch('/api/captions/render', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ video_path:video, style:{ font:document.getElementById('sub-font').value, size:parseInt(document.getElementById('sub-size').value), color:document.getElementById('sub-color').value, outline_color:document.getElementById('sub-outline').value, bold:document.getElementById('sub-bold').checked, position:document.getElementById('sub-pos').value }}) });
    const d = await r.json();
    document.getElementById('sub-result').innerHTML = d.ok ? '<span style="color:var(--green)">Legendas queimadas!</span>' : '<span style="color:var(--red)">'+(d.error||'Erro')+'</span>';
  } catch(e) { toast('Erro: '+e.message); }
  finally { loading(false); }
}

// ===== BRAND KIT =====
async function loadBrandKit() {
  try {
    const r = await fetch('/api/brand/kit'); const d = await r.json();
    if (d.primary_color) document.getElementById('brand-color-primary').value = d.primary_color;
    if (d.secondary_color) document.getElementById('brand-color-secondary').value = d.secondary_color;
    const ar = await fetch('/api/brand/assets'); const ad = await ar.json();
    const list = document.getElementById('brand-assets-list');
    if (!ad.assets||!ad.assets.length) { list.innerHTML = '<span style="color:var(--dim)">Nenhum asset</span>'; }
    else { list.innerHTML = ad.assets.map(a => `<div style="display:flex;align-items:center;gap:12px;padding:8px;border-bottom:1px solid var(--border)"><span style="font-size:20px">${a.type==='logo'?'🖼':'💧'}</span><div style="flex:1"><b>${escHtml(a.name||a.type)}</b></div><button class="btn btn-ghost" style="font-size:11px;color:var(--red)" onclick="deleteBrandAsset('${escHtml(a.name||a.type)}')">x</button></div>`).join(''); }
  } catch(e) { console.error(e); }
}
async function saveBrandKit() {
  loading(true,'Salvando...'); try {
    await fetch('/api/brand/kit', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ primary_color:document.getElementById('brand-color-primary').value, secondary_color:document.getElementById('brand-color-secondary').value, text_color:document.getElementById('brand-color-text').value, bg_color:document.getElementById('brand-color-bg').value }) });
    document.getElementById('brand-status').innerHTML = '<span style="color:var(--green)">Salvo!</span>'; toast('Brand Kit salvo!');
  } catch(e) { toast('Erro: '+e.message); } finally { loading(false); }
}
async function resetBrandKit() { if(!confirm('Resetar Brand Kit?'))return; await fetch('/api/brand/reset',{method:'POST'}); loadBrandKit(); toast('Resetado!'); }
async function uploadBrandAsset(type, input) {
  if (!input.files.length) return; const form = new FormData(); form.append('file', input.files[0]);
  loading(true,'Enviando...'); try { const r = await fetch(type==='logo'?'/api/brand/upload-logo':'/api/brand/upload-watermark',{method:'POST',body:form}); const d = await r.json(); if(d.ok||d.path){toast(type+' salvo!');loadBrandKit();}else toast('Erro: '+(d.error||'')); } catch(e){toast('Erro: '+e.message);} finally{loading(false);}
}
async function deleteBrandAsset(name) { if(!confirm('Remover?'))return; await fetch('/api/brand/delete-asset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}); loadBrandKit(); }

// ===== BLOG TO VIDEO =====
async function validateBlogUrl() { const url=document.getElementById('blog-url').value.trim(); if(!url){toast('Cole uma URL');return;} loading(true,'Validando...'); try { const r=await fetch('/api/blog/validate-url',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})}); const d=await r.json(); document.getElementById('blog-validation').innerHTML=d.valid?'<span style="color:var(--green)">Valida: '+(d.title||'')+'</span>':'<span style="color:var(--red)">'+(d.error||'Invalida')+'</span>'; } catch(e){toast('Erro: '+e.message);} finally{loading(false);} }
async function extractBlogContent() { const url=document.getElementById('blog-url').value.trim(); if(!url){toast('Cole URL');return;} loading(true,'Extraindo...'); try { const r=await fetch('/api/blog/extract',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url})}); const d=await r.json(); if(d.content){document.getElementById('blog-content').value=d.content;toast((d.word_count||0)+' palavras');}else toast('Erro: '+(d.error||'')); } catch(e){toast('Erro: '+e.message);} finally{loading(false);} }
async function summarizeBlogContent() { const c=document.getElementById('blog-content').value; if(!c){toast('Extraia primeiro');return;} loading(true,'Resumindo...'); try { const r=await fetch('/api/blog/summarize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({content:c})}); const d=await r.json(); if(d.summary)document.getElementById('blog-content').value=d.summary; } catch(e){toast('Erro: '+e.message);} finally{loading(false);} }
async function blogToVideo() { const c=document.getElementById('blog-content').value; if(!c){toast('Extraia conteudo');return;} loading(true,'Convertendo...'); try { const r=await fetch('/api/blog/to-video',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url:document.getElementById('blog-url').value,content:c,duration:document.getElementById('blog-duration').value,tone:document.getElementById('blog-tone').value,language:document.getElementById('blog-lang').value})}); const d=await r.json(); document.getElementById('blog-result').innerHTML=d.ok?'<span style="color:var(--green)">Video gerado!</span>':'<span style="color:var(--red)">'+(d.error||'Erro')+'</span>'; } catch(e){toast('Erro: '+e.message);} finally{loading(false);} }

// ===== YOUTUBE UPLOAD =====
async function checkYtUploadStatus() {
  try { const r=await fetch('/api/yt-upload/status'); const d=await r.json(); const el=document.getElementById('yt-auth-status');
    el.innerHTML=d.configured?'<div style="display:flex;align-items:center;gap:10px"><span style="font-size:24px">&#x2705;</span><div><b style="color:var(--green)">OAuth Configurado</b></div></div>':'<div style="display:flex;align-items:center;gap:10px"><span style="font-size:24px">&#x26A0;</span><div><b style="color:var(--yellow)">Nao Configurado</b><div style="font-size:12px;color:var(--dim)">Envie client_secret.json</div></div></div>';
    const vr=await fetch('/api/outputs'); const vd=await vr.json(); const sel=document.getElementById('yt-video-select'); sel.innerHTML='<option value="">Selecione...</option>'; (vd.files||[]).filter(f=>f.name.endsWith('.mp4')).forEach(v=>{sel.innerHTML+='<option value="'+escHtml(v.path||v.name)+'">'+escHtml(v.name)+'</option>';});
  } catch(e){console.error(e);}
}
async function saveYtClientSecret(input) { if(!input.files.length)return; const form=new FormData(); form.append('file',input.files[0]); loading(true,'Salvando...'); try { const r=await fetch('/api/yt-upload/save-client-secret',{method:'POST',body:form}); const d=await r.json(); if(d.ok){toast('Salvo!');checkYtUploadStatus();}else toast('Erro: '+(d.error||'')); } catch(e){toast('Erro: '+e.message);} finally{loading(false);} }
async function uploadToYouTube() {
  const video=document.getElementById('yt-video-select').value; if(!video){toast('Selecione video');return;} const title=document.getElementById('yt-title').value; if(!title){toast('Digite titulo');return;}
  loading(true,'Enviando para YouTube...');
  try { const r=await fetch('/api/yt-upload/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_path:video,title:title,description:document.getElementById('yt-desc').value,tags:document.getElementById('yt-tags').value.split(',').map(function(t){return t.trim()}).filter(Boolean),privacy:document.getElementById('yt-privacy').value})}); const d=await r.json();
    if(d.ok||d.video_id){document.getElementById('yt-upload-result').innerHTML='<div style="background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:10px;padding:16px;text-align:center"><div style="font-size:24px">&#x2705;</div><b style="color:var(--green)">Enviado!</b>'+(d.video_id?'<br><a href="https://youtube.com/watch?v='+d.video_id+'" target="_blank" style="color:var(--accent)">Assistir</a>':'')+'</div>';toast('Upload OK!');}
    else toast('Erro: '+(d.error||''));
  } catch(e){toast('Erro: '+e.message);} finally{loading(false);}
}

// ============================================================
// TITLE INTELLIGENCE — GCG VideosMAX (ported from TitlePilot)
// All functions prefixed with "ti", routes use /api/ti/
// ============================================================

// ---- TAB MANAGEMENT ----
function tiInitPage(sub){
  document.querySelectorAll('.ti-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.ti-tab').forEach(b=>b.classList.remove('active'));
  const panel=document.getElementById('ti-'+sub);
  if(panel) panel.classList.add('active');
  const btn=document.querySelector(`[data-titab="${sub}"]`);
  if(btn) btn.classList.add('active');
  if(sub==='channels') tiLoadChannels();
  if(sub==='subniche') tiLoadNichesDropdown();
}

function tiShowYtTab(id){
  document.querySelectorAll('.ti-yt-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.ti-yt-tab').forEach(b=>b.classList.remove('active'));
  const panel=document.getElementById(id);
  if(panel) panel.classList.add('active');
  const btn=document.querySelector(`[data-tiyttab="${id}"]`);
  if(btn) btn.classList.add('active');
}

// ---- UTILITIES ----
function tiFmtNum(n){if(!n&&n!==0)return '0';if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return (n/1e3).toFixed(1)+'K';return Math.round(n).toString();}
function tiGradeClass(g){return 'grade-'+(g||'F');}
function tiGradeColor(g){return{S:'#FFD700',A:'#4ecca3',B:'#3b82f6',C:'#f59e0b',D:'#e94560',F:'#666'}[g]||'#666';}
function tiBarColor(s){return s>=80?'#FFD700':s>=60?'#4ecca3':s>=40?'#3b82f6':s>=20?'#f59e0b':'#e94560';}
function tiFormatAiText(text){
  if(!text)return '';
  let html=escHtml(text);
  html=html.replace(/^(#{1,3})\s*(.+)$/gm,(m,h,t)=>{const sz=h.length===1?'20px':h.length===2?'17px':'15px';return `<div style="font-size:${sz};font-weight:800;color:var(--accent);margin:24px 0 12px;letter-spacing:-0.5px">${t}</div>`;});
  html=html.replace(/\*\*(.+?)\*\*/g,'<strong style="color:var(--text);font-weight:700">$1</strong>');
  html=html.replace(/^(\d+[\.\)]\s*)([A-Z][^:]+:)/gm,'<div style="margin-top:16px"><span style="color:var(--accent);font-weight:800">$1$2</span></div>');
  html=html.replace(/^[-•]\s+(.+)$/gm,'<div style="padding:4px 0 4px 16px;border-left:2px solid var(--border);margin:4px 0;font-size:14px">$1</div>');
  html=html.replace(/\n\n/g,'<div style="margin:12px 0"></div>');
  html=html.replace(/\n/g,'<br>');
  return html;
}

// ---- AI POST HELPER ----
async function tiPost(url, data){
  loading(true,'Analisando com Gemini AI...');
  try{
    data.ai_api_key=localStorage.getItem('ai_api_key')||'';
    data.yt_api_key=localStorage.getItem('yt_api_key')||'';
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const txt=await r.text();
    try{return JSON.parse(txt);}catch(e){return{error:`Server Error (${r.status}): ${txt.substring(0,100)}`};}
  }catch(err){return{error:err.message};}
  finally{loading(false);}
}

// ---- RENDER HELPERS ----
function tiRenderScoreCard(r){
  const structs=(r.structures||[]).map(s=>`<span class="tag tag-green">${escHtml(s.name)} <span style="opacity:.7">+${Math.round((s.ctr_boost-1)*100)}% CTR</span></span>`).join('');
  const emots=(r.emotional_words||[]).map(w=>`<span class="tag tag-purple">${escHtml(w)}</span>`).join('');
  const powers=(r.power_words||[]).map(w=>`<span class="tag tag-gold">${escHtml(w)}</span>`).join('');
  const issues=(r.issues||[]).map(i=>`<span class="tag tag-red">${escHtml(i)}</span>`).join('');
  const suggs=(r.suggestions||[]).map(s=>`<li>💡 ${escHtml(s)}</li>`).join('');
  const lenLabel=r.length>=70&&r.length<=95?'<span style="color:var(--green)">✓ Tamanho otimo</span>':r.length<60?'<span style="color:var(--red)">✗ Muito curto</span>':r.length>100?'<span style="color:var(--red)">✗ Muito longo</span>':'<span style="color:var(--accent)">○ Aceitavel</span>';
  return `<div class="score-card">
    <div class="score-header">
      <div class="score-circle ${tiGradeClass(r.grade)}">${r.grade||'F'}<div style="font-size:11px;font-weight:500">${r.score||0}/100</div></div>
      <div style="flex:1">
        <div class="score-title">${escHtml(r.title||'')}</div>
        <div class="score-meta">${r.length||0} chars · ${r.words||0} words · ${lenLabel}</div>
      </div>
    </div>
    <div class="score-bar"><div class="score-fill" style="width:${r.score||0}%;background:${tiBarColor(r.score||0)}"></div></div>
    ${structs?`<div style="margin:20px 0"><div class="section-label section-label-green">✅ VIRAL STRUCTURES</div><div class="tags">${structs}</div></div>`:''}
    ${emots?`<div style="margin:20px 0"><div class="section-label section-label-purple">🎯 EMOTIONAL TRIGGERS</div><div class="tags">${emots}</div></div>`:''}
    ${powers?`<div style="margin:20px 0"><div class="section-label section-label-gold">⚡ POWER WORDS</div><div class="tags">${powers}</div></div>`:''}
    ${issues?`<div style="margin:20px 0"><div class="section-label section-label-red">⚠️ PROBLEMAS</div><div class="tags">${issues}</div></div>`:''}
    ${suggs?`<div style="margin:20px 0"><div class="section-label section-label-blue">💡 SUGESTOES</div><ul style="list-style:none;padding:0;margin-top:8px">${suggs}</ul></div>`:''}
  </div>`;
}

function tiVphBadge(vph,mult){
  let cls='vph-cold',txt=`${vph} VPH`;
  if(mult>=5){cls='vph-fire';txt=`🔥 ${vph} VPH (${mult}x)`;}
  else if(mult>=2){cls='vph-hot';txt=`⚡ ${vph} VPH (${mult}x)`;}
  else if(mult>=1){cls='vph-warm';txt=`${vph} VPH (${mult}x)`;}
  return `<span class="vph-badge ${cls}">${txt}</span>`;
}

function tiRenderVideoList(videos,title){
  let html=title?`<h3 style="color:var(--accent);margin:16px 0 8px">${title}</h3>`:'';
  (videos||[]).forEach(v=>{
    const mult=v.vph_multiplier||1;
    const outlier=v.outlier_score||1;
    let outlierBadge='';
    if(outlier>=3)outlierBadge=`<span class="vph-badge" style="background:#ff2a2a;color:#fff;margin-left:6px">🚀 ${outlier}x OUTLIER</span>`;
    else if(outlier>=1.5)outlierBadge=`<span class="vph-badge" style="background:var(--accent);color:#fff;margin-left:6px">📈 ${outlier}x OUTLIER</span>`;
    html+=`<div class="yt-video">
      <div style="flex:1">
        <div style="font-size:13px;font-weight:600;margin-bottom:4px"><a href="https://youtube.com/watch?v=${v.id||v.video_id}" target="_blank" style="color:var(--text);text-decoration:none">${escHtml(v.title||'')}</a></div>
        <div style="font-size:11px;color:var(--dim)">${escHtml(v.channel_title||v.channel_name||'')} · ${v.duration_text||''} · ${v.days_ago||0}d ago</div>
        <div style="margin-top:4px">${tiVphBadge(v.vph,mult)} ${outlierBadge}</div>
      </div>
      <div class="yt-stat"><div class="num">${tiFmtNum(v.views)}</div><div class="label">Views</div></div>
      <div class="yt-stat"><div class="num">${tiFmtNum(v.likes)}</div><div class="label">Likes</div></div>
      <div class="yt-stat"><div class="num">${v.engagement||0}%</div><div class="label">Engage</div></div>
    </div>`;
  });
  return html;
}

function tiRenderMetrics(m){
  return `<div class="yt-metric-grid">
    <div class="yt-metric-box"><div class="value">${tiFmtNum(m.avg_vph||0)}</div><div class="label">Avg VPH</div></div>
    <div class="yt-metric-box"><div class="value">${tiFmtNum(m.max_vph||0)}</div><div class="label">Peak VPH</div></div>
    <div class="yt-metric-box"><div class="value">${tiFmtNum(m.avg_views||0)}</div><div class="label">Avg Views</div></div>
    <div class="yt-metric-box"><div class="value">${m.avg_engagement||0}%</div><div class="label">Engage</div></div>
    ${m.avg_duration_text?`<div class="yt-metric-box"><div class="value">${m.avg_duration_text}</div><div class="label">Avg Duration</div></div>`:''}
    ${m.total_analyzed?`<div class="yt-metric-box"><div class="value">${m.total_analyzed}</div><div class="label">Videos</div></div>`:''}
  </div>`;
}

function tiRenderWordCloud(words){
  if(!words||!Object.keys(words).length)return '';
  let html='<div style="margin:12px 0"><b style="font-size:11px;color:var(--accent)">TOP TITLE WORDS</b><div class="tags" style="margin-top:6px">';
  const sorted=Object.entries(words).sort((a,b)=>b[1]-a[1]).slice(0,20);
  const max=sorted[0]?sorted[0][1]:1;
  sorted.forEach(([w,c])=>{
    const size=Math.max(11,Math.min(18,11+Math.round((c/max)*7)));
    html+=`<span class="tag tag-blue" style="font-size:${size}px;opacity:${Math.max(0.5,c/max)}">${escHtml(w)} (${c})</span>`;
  });
  return html+'</div></div>';
}

// ---- ANALYZER ----
async function tiAnalyzeTitle(){
  const title=document.getElementById('ti-analyze-input').value.trim();
  if(!title)return;
  const r=await tiPost('/api/ti/analyze',{title});
  document.getElementById('ti-analyze-result').innerHTML=r.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`:tiRenderScoreCard(r);
}
async function tiDeepAnalyze(){
  const title=document.getElementById('ti-analyze-input').value.trim();
  if(!title)return;
  const r=await tiPost('/api/ti/deep_analysis',{title});
  if(r.error){document.getElementById('ti-analyze-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  let html=tiRenderScoreCard(r);
  if(r.ai_deep_analysis)html+=`<div class="ai-text">${tiFormatAiText(r.ai_deep_analysis)}</div>`;
  document.getElementById('ti-analyze-result').innerHTML=html;
}

// ---- GENERATOR ----
async function tiGenerateTitles(){
  const topic=document.getElementById('ti-gen-topic').value.trim();
  if(!topic)return;
  const lang=document.getElementById('ti-gen-lang').value;
  const niche=document.getElementById('ti-gen-niche').value.trim();
  const r=await tiPost('/api/ti/generate',{topic,language:lang,niche});
  if(r.error){document.getElementById('ti-gen-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  const titles=r.titles||[];
  const avgScore=titles.length?Math.round(titles.reduce((s,t)=>s+t.score,0)/titles.length):0;
  const avgLen=titles.length?Math.round(titles.reduce((s,t)=>s+t.length,0)/titles.length):0;
  let html=`<div class="results-header"><h3>⚡ Gerado para: ${escHtml(r.topic||topic)}</h3>
    <div class="results-stats">
      <div class="results-stat"><div class="val" style="color:var(--accent)">${titles.length}</div><div class="lbl">Titulos</div></div>
      <div class="results-stat"><div class="val" style="color:${tiBarColor(avgScore)}">${avgScore}</div><div class="lbl">Score Medio</div></div>
      <div class="results-stat"><div class="val" style="color:${avgLen>=70?'var(--green)':'var(--red)'}">${avgLen}c</div><div class="lbl">Tam Medio</div></div>
    </div></div>`;
  titles.forEach(t=>{
    const lenColor=t.length>=70&&t.length<=95?'var(--green)':t.length>=55?'var(--accent)':'var(--red)';
    const structTags=(t.structures||[]).map(s=>`<span class="title-struct-tag">${escHtml(s)}</span>`).join('');
    html+=`<div class="title-item">
      <div class="title-score ${tiGradeClass(t.grade)}">${t.grade||'F'}<div style="font-size:11px;font-weight:500">${t.score}</div></div>
      <div style="flex:1">
        <div class="title-text">${escHtml(t.title)}</div>
        ${structTags?`<div class="title-structures">${structTags}</div>`:''}
        ${t.thumbnail_concept?`<div style="font-size:12px;color:var(--dim);margin-top:6px;padding:6px;background:var(--card);border-left:2px solid var(--purple)">🖼️ <b>Thumbnail:</b> ${escHtml(t.thumbnail_concept)}</div>`:''}
      </div>
      <div class="title-len" style="color:${lenColor}">${t.length}c</div>
    </div>`;
  });
  document.getElementById('ti-gen-result').innerHTML=html;
}

// ---- HOOK ANALYZER ----
async function tiAnalyzeHook(){
  const hook=document.getElementById('ti-hook-input').value.trim();
  const title=document.getElementById('ti-hook-title').value.trim();
  const lang=document.getElementById('ti-hook-lang').value;
  if(!hook){toast('Cole o intro do script');return;}
  if(!title){toast('Informe o titulo do video');return;}
  const r=await tiPost('/api/ti/hook_analyze',{hook,title,language:lang});
  document.getElementById('ti-hook-result').innerHTML=r.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`:`<div class="ai-text" style="font-size:15px;line-height:1.6">${tiFormatAiText(r.analysis||'')}</div>`;
}

// ---- SEO OPTIMIZER ----
async function tiGenerateSEO(){
  const title=document.getElementById('ti-seo-title').value.trim();
  const context=document.getElementById('ti-seo-context').value.trim();
  const lang=document.getElementById('ti-seo-lang').value;
  if(!title){toast('Informe o titulo do video');return;}
  const r=await tiPost('/api/ti/seo_optimize',{title,context,language:lang});
  document.getElementById('ti-seo-result').innerHTML=r.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`:`<div class="ai-text" style="font-size:14px;line-height:1.6">${tiFormatAiText(r.seo||'')}</div>`;
}

// ---- A/B BATTLE ----
async function tiRunBattle(){
  const title_a=document.getElementById('ti-battle-a').value.trim();
  const title_b=document.getElementById('ti-battle-b').value.trim();
  const lang=document.getElementById('ti-battle-lang').value;
  if(!title_a||!title_b){toast('Informe os dois titulos');return;}
  const r=await tiPost('/api/ti/ab_battle',{title_a,title_b,language:lang});
  if(r.error){document.getElementById('ti-battle-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  const b=r.battle;
  const winnerColor=b.winner&&b.winner.includes('A')?'#4ecca3':'#FFD700';
  document.getElementById('ti-battle-result').innerHTML=`
    <div class="score-card" style="border:2px solid ${winnerColor}">
      <h2 style="color:${winnerColor};text-align:center;font-size:22px;margin-bottom:8px">👑 VENCEDOR: TITULO ${b.winner||'?'}</h2>
      <div style="text-align:center;font-size:16px;color:#fff;margin-bottom:16px;font-weight:bold">${escHtml(b.ctr_delta||'')}</div>
      <p style="color:#ccc;font-size:14px;line-height:1.6;margin-bottom:16px">${escHtml(b.reasoning||'')}</p>
      <div style="display:flex;gap:12px;margin-top:16px">
        <div style="flex:1;background:var(--card);padding:12px;border-radius:8px"><b style="color:#4ecca3">Titulo A:</b><br><span style="font-size:12px">${escHtml(b.breakdown_a||'')}</span></div>
        <div style="flex:1;background:var(--card);padding:12px;border-radius:8px"><b style="color:#FFD700">Titulo B:</b><br><span style="font-size:12px">${escHtml(b.breakdown_b||'')}</span></div>
      </div>
    </div>`;
}

// ---- BATCH ANALYZER ----
async function tiBatchAnalyze(){
  const text=document.getElementById('ti-batch-input').value.trim();
  if(!text)return;
  const titles=text.split('\n').filter(t=>t.trim());
  const r=await tiPost('/api/ti/batch',{titles});
  if(r.error){document.getElementById('ti-batch-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  let html=`<div class="score-card">
    <div style="display:flex;gap:20px;margin-bottom:16px">
      <div style="flex:1;text-align:center"><div style="font-size:32px;font-weight:900;color:${tiBarColor(r.avg_score)}">${r.avg_score}</div><div style="font-size:11px;color:#666">Score Medio</div></div>
      <div style="flex:1;text-align:center"><div style="font-size:32px;font-weight:900;color:#FFD700">${r.count}</div><div style="font-size:11px;color:#666">Titulos</div></div>
    </div>
    ${r.best?`<div style="margin-bottom:8px"><b style="color:#4ecca3;font-size:11px">MELHOR:</b> ${escHtml(r.best.title)} (${r.best.score})</div>`:''}
    ${r.worst?`<div><b style="color:#e94560;font-size:11px">PIOR:</b> ${escHtml(r.worst.title)} (${r.worst.score})</div>`:''}
  </div>`;
  if(r.structures){
    html+='<div class="section"><h3 style="margin-bottom:8px">📊 Uso de Estruturas</h3>';
    for(let[s,c]of Object.entries(r.structures)){
      const pct=Math.round(c/r.count*100);
      html+=`<div style="margin:6px 0;display:flex;align-items:center;gap:8px"><div style="width:130px;font-size:12px">${escHtml(s)}</div><div style="flex:1;height:8px;background:var(--card);border-radius:4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:#4ecca3;border-radius:4px"></div></div><div style="font-size:11px;color:#4ecca3;width:40px;text-align:right">${pct}%</div></div>`;
    }
    html+='</div>';
  }
  html+='<h3 style="color:#FFD700;margin:16px 0 8px">Todos os Titulos (ranking)</h3>';
  (r.results||[]).forEach(t=>{html+=`<div class="title-item"><div class="title-score ${tiGradeClass(t.grade)}" style="font-size:12px">${t.grade}<br>${t.score}</div><div class="title-text">${escHtml(t.title)}</div><div class="title-len">${t.length}c</div></div>`;});
  document.getElementById('ti-batch-result').innerHTML=html;
}

// ---- SUBNICHE FINDER ----
async function tiLoadNichesDropdown(){
  try{
    const r=await fetch('/api/ti/niche_list');
    const d=await r.json();
    if(d.niches&&Object.keys(d.niches).length){
      let html='<option value="">Todos os Temas (Auto)</option>';
      for(let[cat,niches]of Object.entries(d.niches)){
        html+=`<optgroup label="${escHtml(cat)}">`;
        niches.forEach(n=>{html+=`<option value="${escHtml(n)}">${escHtml(n)}</option>`;});
        html+='</optgroup>';
      }
      const el=document.getElementById('ti-sub-theme');
      if(el) el.innerHTML=html;
    }
  }catch(e){console.error('Failed to load TI niches',e);}
}

async function tiFindSubniches(){
  const dropdownTheme=document.getElementById('ti-sub-theme').value.trim();
  const customTheme=document.getElementById('ti-sub-custom').value.trim();
  const theme=customTheme||dropdownTheme;
  const lang=document.getElementById('ti-sub-lang').value;
  const r=await tiPost('/api/ti/subniche',{theme,language:lang});
  if(r.error){document.getElementById('ti-sub-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  let html='';
  if(r.niches&&r.niches.length){
    html+=`<div class="results-header"><h3>🔥 ${r.niches.length} Subnichos Encontrados</h3><div class="results-count">${escHtml(theme||'Todos os temas')} · ${lang}</div></div>`;
    r.niches.forEach((n,i)=>{
      const demC=n.demand>=7?'#4ecca3':n.demand>=4?'#f59e0b':'#e94560';
      const supC=n.supply<=3?'#4ecca3':n.supply<=6?'#f59e0b':'#e94560';
      const oppC=n.opportunity>=6?'#4ecca3':n.opportunity>=3?'#f59e0b':'#e94560';
      const kws=(n.keywords||[]).map(k=>`<span class="tag tag-blue">${escHtml(k)}</span>`).join('');
      html+=`<div class="niche-card">
        <h3>#${i+1} — ${escHtml(n.name)}</h3>
        <div class="niche-meter">
          <div class="meter"><div class="meter-label">📈 DEMANDA</div><div class="meter-bar"><div class="meter-fill" style="width:${n.demand*10}%;background:${demC}"></div></div><div style="font-size:13px;color:${demC};margin-top:4px;font-weight:700">${n.demand}/10</div></div>
          <div class="meter"><div class="meter-label">📉 OFERTA</div><div class="meter-bar"><div class="meter-fill" style="width:${n.supply*10}%;background:${supC}"></div></div><div style="font-size:13px;color:${supC};margin-top:4px;font-weight:700">${n.supply}/10</div></div>
          <div class="meter"><div class="meter-label">🎯 OPORTUNIDADE</div><div class="meter-bar"><div class="meter-fill" style="width:${n.opportunity*10}%;background:${oppC}"></div></div><div style="font-size:13px;color:${oppC};margin-top:4px;font-weight:800">${n.opportunity}/10</div></div>
        </div>
        ${n.target_audience?`<div style="margin:16px 0"><div class="section-label section-label-purple">👤 PUBLICO-ALVO</div><div style="font-size:14px;color:#ccc;margin-top:6px;line-height:1.6">${escHtml(n.target_audience)}</div></div>`:''}
        ${n.audience_pain?`<div style="margin:16px 0"><div class="section-label section-label-red">😤 DOR DO PUBLICO</div><div style="font-size:14px;color:#ccc;margin-top:6px;line-height:1.6">${escHtml(n.audience_pain)}</div></div>`:''}
        ${n.content_angle?`<div style="margin:16px 0"><div class="section-label section-label-orange">🎯 ANGULO DE CONTEUDO</div><div style="font-size:14px;color:#ccc;margin-top:6px;line-height:1.6">${escHtml(n.content_angle)}</div></div>`:''}
        ${n.estimated_views_per_video?`<div style="margin:16px 0"><div class="section-label section-label-gold">📊 ESTIMATIVA DE VIEWS</div><div style="font-size:16px;color:var(--accent);margin-top:6px;font-weight:700">${escHtml(n.estimated_views_per_video)}</div></div>`:''}
        ${kws?`<div style="margin:16px 0"><div class="section-label section-label-blue">🔑 KEYWORDS</div><div class="tags">${kws}</div></div>`:''}
        ${n.example_titles&&n.example_titles.length?`<div style="margin:16px 0"><div class="section-label section-label-green">📝 TITULOS VIRAIS DE EXEMPLO</div>${n.example_titles.map(t=>`<div class="title-item" style="margin:6px 0"><div class="title-text">${escHtml(t)}</div><div class="title-len">${t.length}c</div></div>`).join('')}</div>`:''}
        <div style="display:flex;gap:8px;margin-top:16px">
          <button class="btn btn-primary" style="flex:1;font-size:13px" onclick="tiValidateSubniche('${escHtml(n.name).replace(/'/g,"\\'")}',${JSON.stringify(n.keywords||[]).replace(/"/g,'&quot;')},'ti-validate-${i}')">📊 Validar com YouTube</button>
          <button class="btn btn-secondary" style="font-size:13px" onclick="tiGenerateFromSubniche('${escHtml(n.name).replace(/'/g,"\\'")}')">⚡ Gerar Titulos</button>
        </div>
        <div id="ti-validate-${i}" style="margin-top:12px"></div>
      </div>`;
    });
  } else if(r.raw){
    html=`<div class="ai-text">${tiFormatAiText(r.raw)}</div>`;
  }
  document.getElementById('ti-sub-result').innerHTML=html;
}

async function tiValidateSubniche(name,keywords,targetId){
  const el=document.getElementById(targetId);
  if(!el)return;
  el.innerHTML='<div style="padding:16px;text-align:center;color:var(--accent)"><div class="spinner" style="width:24px;height:24px;border-width:3px;margin:0 auto 8px"></div>Buscando dados reais do YouTube...</div>';
  try{
    const kw=typeof keywords==='string'?JSON.parse(keywords):keywords;
    const r=await fetch('/api/ti/subniche_validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,keywords:kw,yt_api_key:localStorage.getItem('yt_api_key')||''})});
    const d=await r.json();
    if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">⚠️ ${escHtml(d.error)}</div>`;return;}
    let html=`<div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;margin-top:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <div class="section-label section-label-gold" style="margin:0;padding:0;border:none">📡 VALIDACAO YOUTUBE — ${escHtml(d.subniche||name)}</div>
        <div style="display:flex;gap:12px">
          <div style="text-align:center"><div style="font-size:20px;font-weight:800;color:var(--accent)">${d.total_videos||0}</div><div style="font-size:10px;color:var(--dim)">VIDEOS</div></div>
          <div style="text-align:center"><div style="font-size:20px;font-weight:800;color:var(--green)">${d.total_channels||0}</div><div style="font-size:10px;color:var(--dim)">CANAIS</div></div>
          <div style="text-align:center"><div style="font-size:20px;font-weight:800;color:var(--purple)">${tiFmtNum(d.avg_vph||0)}</div><div style="font-size:10px;color:var(--dim)">VPH MEDIO</div></div>
        </div>
      </div>`;
    if(d.channels&&d.channels.length){
      html+='<div class="section-label section-label-purple" style="margin-top:16px">📺 CANAIS TRENDING NESTE SUBNICHO</div>';
      d.channels.forEach(ch=>{
        const subs=ch.subscribers>=1e6?(ch.subscribers/1e6).toFixed(1)+'M':ch.subscribers>=1e3?Math.round(ch.subscribers/1e3)+'K':ch.subscribers;
        html+=`<div style="display:flex;align-items:center;gap:12px;padding:10px;background:var(--card);border-radius:10px;margin:6px 0">
          ${ch.thumbnail?`<img src="${ch.thumbnail}" style="width:36px;height:36px;border-radius:50%">`:''}
          <div style="flex:1"><div style="font-weight:600;font-size:14px">${escHtml(ch.name||'')}</div>
          <div style="font-size:11px;color:var(--dim)">${subs} subs · VPH: <span style="color:var(--green);font-weight:700">${tiFmtNum(ch.avg_vph||0)}</span></div></div>
          <button class="btn btn-secondary" style="font-size:11px;padding:6px 12px" onclick="tiSaveChannel('${ch.id}','${escHtml(ch.name||'').replace(/'/g,"\\'")}',${ch.subscribers||0},${ch.avg_vph||0},'${escHtml(name).replace(/'/g,"\\'")}','${ch.thumbnail||''}')">💾 Salvar</button>
        </div>`;
      });
    }
    if(d.videos&&d.videos.length){
      html+='<div class="section-label section-label-green" style="margin-top:20px">🔥 TOP VIDEOS (por VPH)</div>';
      d.videos.slice(0,8).forEach(v=>{
        const vphC=v.vph>=1000?'#ff6b35':v.vph>=100?'var(--accent)':v.vph>=10?'var(--green)':'var(--dim)';
        html+=`<div style="display:flex;align-items:center;gap:12px;padding:10px;background:var(--card);border-radius:10px;margin:6px 0">
          ${v.thumbnail?`<img src="${v.thumbnail}" style="width:64px;height:36px;border-radius:6px;object-fit:cover">`:''}
          <div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escHtml(v.title||'')}</div>
          <div style="font-size:11px;color:var(--dim)">${escHtml(v.channel_name||'')} · ${tiFmtNum(v.views)} views</div></div>
          <div style="text-align:center;min-width:60px"><div style="font-size:16px;font-weight:800;color:${vphC}">${tiFmtNum(v.vph||0)}</div><div style="font-size:9px;color:var(--dim)">VPH</div></div>
        </div>`;
      });
    }
    html+='</div>';
    el.innerHTML=html;
  }catch(e){el.innerHTML=`<div style="color:var(--red);padding:12px">Erro: ${e.message}</div>`;}
}

function tiGenerateFromSubniche(name){
  document.getElementById('ti-gen-topic').value=name;
  tiInitPage('generator');
  setTimeout(tiGenerateTitles,500);
}

function tiSaveChannel(id,name,subs,vph,subniche,thumb){
  try{
    let saved=JSON.parse(localStorage.getItem('ti_saved_channels')||'[]');
    if(!saved.find(c=>c.id===id)){
      saved.push({id,name,subscribers:subs,avg_vph:vph,subniche,thumbnail:thumb,saved_at:new Date().toISOString()});
      localStorage.setItem('ti_saved_channels',JSON.stringify(saved));
      toast('💾 Canal salvo! Total: '+saved.length);
    }else toast('⚠️ Canal ja salvo!');
  }catch(e){toast('Erro: '+e.message);}
}

// ---- TREND SCANNER ----
async function tiScanTrends(){
  const cat=document.getElementById('ti-trend-cat').value;
  const lang=document.getElementById('ti-trend-lang').value;
  const r=await tiPost('/api/ti/trend_scanner',{category:cat,language:lang});
  if(r.error){document.getElementById('ti-trend-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  document.getElementById('ti-trend-result').innerHTML=`<div class="results-header"><h3>📈 Trend Analysis</h3><div class="results-count">${cat==='all'?'Todas as Categorias':cat} · ${lang}</div></div><div class="ai-text">${tiFormatAiText(r.trends||'')}</div>`;
}

// ---- CHANNEL STRATEGY ----
async function tiGetStrategy(){
  const type=document.getElementById('ti-strat-type').value.trim();
  const audience=document.getElementById('ti-strat-audience').value.trim();
  const lang=document.getElementById('ti-strat-lang').value;
  const titles=document.getElementById('ti-strat-titles').value.trim().split('\n').filter(t=>t.trim());
  if(!type){toast('Informe o tipo de canal');return;}
  const r=await tiPost('/api/ti/channel_strategy',{channel_type:type,target_audience:audience,language:lang,titles});
  document.getElementById('ti-strat-result').innerHTML=r.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`:`<div class="results-header"><h3>🏆 Estrategia de Canal</h3><div class="results-count">${escHtml(type)} · ${lang}</div></div><div class="ai-text">${tiFormatAiText(r.strategy||'')}</div>`;
}

// ---- STRATEGY REMIX A/B ----
async function tiGenerateRemix(){
  const topic=document.getElementById('ti-remix-topic').value.trim();
  const lang=document.getElementById('ti-remix-lang').value;
  const path_a=document.getElementById('ti-remix-a').value.trim()||'Fear & Danger';
  const path_b=document.getElementById('ti-remix-b').value.trim()||'Money & Corruption';
  if(!topic){toast('Informe o topico');return;}
  const r=await tiPost('/api/ti/strategy_remix',{topic,language:lang,path_a,path_b});
  document.getElementById('ti-remix-result').innerHTML=r.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`:`<div class="results-header"><h3>🔀 A/B Remix Strategy</h3><div class="results-count">${escHtml(topic)}</div></div><div class="ai-text">${tiFormatAiText(r.remix||'')}</div>`;
}

// ---- THUMBNAIL PROMPTS ----
async function tiGenerateThumbs(){
  const title=document.getElementById('ti-thumb-title').value.trim();
  if(!title){toast('Informe o titulo do video');return;}
  const r=await tiPost('/api/ti/thumb_prompt',{title});
  if(r.error){document.getElementById('ti-thumb-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`;return;}
  let html=`<div class="results-header"><h3>🖼️ 3 Conceitos de Thumbnail</h3></div>`;
  (r.thumbs&&r.thumbs.prompts||[]).forEach((p,idx)=>{
    html+=`<div class="score-card" style="margin-bottom:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <h4 style="color:#4ecca3;margin:0">Ideia ${idx+1}: ${escHtml(p.style||'')}</h4>
      </div>
      <p style="color:#ccc;font-size:13px;line-height:1.5;margin:8px 0"><b>Visuais:</b> ${escHtml(p.visuals||'')}</p>
      ${p.text_overlay?`<p style="color:#FFD700;font-size:13px;margin:8px 0"><b>Texto na Thumb:</b> "${escHtml(p.text_overlay)}"</p>`:''}
      <div style="background:var(--card);padding:12px;border-radius:6px;border:1px dashed var(--border);margin-top:12px;position:relative">
        <div style="font-size:11px;color:var(--dim);margin-bottom:4px">Midjourney Prompt:</div>
        <code style="color:#e6edf3;font-size:12px;font-family:monospace;word-break:break-all">${escHtml(p.midjourney_prompt||'')}</code>
        <button class="btn btn-secondary" style="position:absolute;top:8px;right:8px;padding:4px 8px;font-size:10px" onclick="navigator.clipboard.writeText(this.previousElementSibling.textContent);this.innerText='Copiado!';setTimeout(()=>this.innerText='Copiar',2000)">Copiar</button>
      </div>
    </div>`;
  });
  document.getElementById('ti-thumb-result').innerHTML=html;
}

// ---- VISION AUDITOR ----
let tiVisionImageB64='';
function tiPreviewVisionImage(event){
  const file=event.target.files[0];
  if(!file)return;
  const reader=new FileReader();
  reader.onload=function(e){
    tiVisionImageB64=e.target.result;
    const preview=document.getElementById('ti-vision-preview');
    if(preview){preview.src=tiVisionImageB64;preview.style.display='block';}
  };
  reader.readAsDataURL(file);
}
async function tiAuditThumbnail(){
  const title=document.getElementById('ti-vision-title').value.trim();
  if(!tiVisionImageB64){toast('Faca upload de uma thumbnail primeiro');return;}
  if(!title){toast('Informe o titulo do video para contexto');return;}
  loading(true,'Gemini Pro Vision analisando sua thumbnail...');
  try{
    const r=await fetch('/api/ti/vision_audit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:tiVisionImageB64,title,ai_api_key:localStorage.getItem('ai_api_key')||''})});
    const d=await r.json();
    document.getElementById('ti-vision-result').innerHTML=d.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`:`<div class="score-card" style="border:1px solid rgba(139,92,246,.4);box-shadow:0 0 20px rgba(139,92,246,.1)"><h2 style="color:var(--purple);text-align:center;font-size:20px;margin-bottom:16px">👁️ AI Vision Analysis Complete</h2><div class="ai-text">${tiFormatAiText(d.audit||'')}</div></div>`;
  }catch(e){document.getElementById('ti-vision-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
  finally{loading(false);}
}

// ---- YOUTUBE SCANNER ----
async function tiSaveYtKey(){
  const key=document.getElementById('ti-yt-key').value.trim();
  if(!key){toast('Cole sua YouTube API key');return;}
  localStorage.setItem('yt_api_key',key);
  const r=await fetch('/api/ti/youtube/save_key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
  const d=await r.json();
  const el=document.getElementById('ti-yt-key-status');
  if(el) el.textContent=d.status==='ok'?'✅ Salvo!':'❌ Erro';
}

async function tiScanChannel(){
  const ch=document.getElementById('ti-yt-ch-input').value.trim();
  if(!ch){toast('Informe o handle do canal');return;}
  loading(true,'Escaneando canal com YouTube API...');
  try{
    const payload={channel:ch,max_videos:30,yt_api_key:localStorage.getItem('yt_api_key')||''};
    const r=await fetch('/api/ti/youtube/channel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){document.getElementById('ti-yt-ch-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`;return;}
    const c=d.channel||{};
    let html=`<div class="score-card">
      <div style="display:flex;gap:16px;align-items:center;margin-bottom:16px">
        ${c.thumbnail?`<img src="${c.thumbnail}" style="width:80px;height:80px;border-radius:50%;object-fit:cover">`:'' }
        <div><h2 style="color:var(--accent);font-size:20px">${escHtml(c.title||'')}</h2>
        <div style="font-size:12px;color:var(--dim);margin-top:4px">${tiFmtNum(c.subscribers)} subs · ${tiFmtNum(c.total_views)} total views · ${c.video_count||0} videos · ${c.country||'?'}</div></div>
      </div>
      ${tiRenderMetrics(d.metrics||{})}
    </div>`;
    html+=tiRenderWordCloud(d.top_words);
    html+=tiRenderVideoList(d.videos||[],'📊 Todos os Videos (por VPH)');
    document.getElementById('ti-yt-ch-result').innerHTML=html;
  }catch(e){document.getElementById('ti-yt-ch-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
  finally{loading(false);}
}

async function tiScanNiche(){
  const q=document.getElementById('ti-yt-niche-q').value.trim();
  if(!q){toast('Informe a query do nicho');return;}
  const region=document.getElementById('ti-yt-niche-region').value;
  loading(true,'Mergulhando fundo no nicho...');
  try{
    const payload={query:q,region,yt_api_key:localStorage.getItem('yt_api_key')||''};
    const r=await fetch('/api/ti/youtube/niche',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){document.getElementById('ti-yt-niche-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`;return;}
    let html=`<div class="score-card"><h2 style="color:var(--accent);margin-bottom:8px">🎯 Nicho: "${escHtml(d.query||q)}"</h2>
      <div style="font-size:12px;color:var(--dim)">${d.total_videos||0} videos analisados</div>
      ${tiRenderMetrics(d.metrics||{})}
    </div>`;
    if(d.top_channels&&d.top_channels.length){
      html+='<div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin:12px 0"><h3 style="margin-bottom:8px">🏆 Top Canais no Nicho</h3>';
      d.top_channels.forEach((c,i)=>{
        html+=`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)"><span style="font-size:13px">#${i+1} ${escHtml(c.name)} (${c.videos} vids)</span><span style="font-size:12px;color:var(--green)">${tiFmtNum(c.total_views)} views</span></div>`;
      });
      html+='</div>';
    }
    html+=tiRenderWordCloud(d.top_words);
    if(d.outliers&&d.outliers.length)html+=tiRenderVideoList(d.outliers,'🔥 OUTLIERS (3x+ VPH)');
    html+=tiRenderVideoList(d.top_by_vph||[],'⚡ Top por VPH (ultimos 7 dias)');
    document.getElementById('ti-yt-niche-result').innerHTML=html;
  }catch(e){document.getElementById('ti-yt-niche-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
  finally{loading(false);}
}

async function tiScanTrending(){
  const q=document.getElementById('ti-yt-trend-q').value.trim();
  const region=document.getElementById('ti-yt-trend-region').value;
  const cat=document.getElementById('ti-yt-trend-cat').value;
  loading(true,'Escaneando videos trending...');
  try{
    const payload={query:q,region,category_id:cat,yt_api_key:localStorage.getItem('yt_api_key')||''};
    const r=await fetch('/api/ti/youtube/trending',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){document.getElementById('ti-yt-trend-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`;return;}
    let html=`<div class="score-card"><h2 style="color:var(--accent)">🔥 ${q?'Busca: "'+escHtml(q)+'"':'Trending Agora'} (${d.region||region})</h2></div>`;
    html+=tiRenderVideoList(d.videos||[]);
    document.getElementById('ti-yt-trend-result').innerHTML=html;
  }catch(e){document.getElementById('ti-yt-trend-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
  finally{loading(false);}
}

async function tiScanNewbornVirals(){
  const q=document.getElementById('ti-yt-newborn-q').value.trim();
  if(!q){toast('Informe um nicho ou tema');return;}
  loading(true,'Buscando NEWBORN VIRALS (canais pequenos virilizando)...');
  try{
    const payload={query:q,yt_api_key:localStorage.getItem('yt_api_key')||''};
    const r=await fetch('/api/ti/youtube/newborn_virals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){document.getElementById('ti-yt-newborn-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`;return;}
    let html=`<div class="score-card"><h2 style="color:var(--accent)">🚀 Newborn Virals: "${escHtml(q)}"</h2><div style="font-size:12px;color:var(--dim)">Canais com ≤35 videos mas VPH MASSIVO.</div></div>`;
    if(!d.virals||!d.virals.length){
      html+=`<div class="score-card" style="text-align:center;color:var(--dim)">Nenhum newborn viral encontrado. Tente uma busca mais ampla.</div>`;
    }else{
      d.virals.forEach(v=>{
        const ch=v.channel_stats||{};
        html+=`<div class="score-card" style="margin-bottom:16px">
          <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid var(--border)">
            ${ch.thumbnail?`<img src="${ch.thumbnail}" style="width:48px;height:48px;border-radius:50%">`:''}
            <div style="flex:1">
              <div style="font-weight:700;font-size:15px"><a href="https://youtube.com/channel/${v.channel_id}" target="_blank" style="color:var(--text);text-decoration:none">${escHtml(v.channel_name||'')} 🔗</a></div>
              <div style="font-size:12px;color:var(--dim)"><span style="color:var(--purple);font-weight:700">${ch.video_count||0} videos</span> · ${tiFmtNum(ch.subscribers||0)} subs · Criado: ${ch.published_at||'?'}</div>
            </div>
            <button class="btn btn-primary" style="padding:6px 12px;font-size:12px" onclick="tiRemixFromViral('${escHtml(v.title||'').replace(/'/g,"\\'")}','${escHtml(q).replace(/'/g,"\\'")}')">🛠️ Montar Estrategia A/B</button>
          </div>
          <div style="display:flex;gap:12px;align-items:center">
            ${v.thumbnail?`<a href="https://youtube.com/watch?v=${v.video_id}" target="_blank"><img src="${v.thumbnail}" style="width:120px;border-radius:8px"></a>`:''}
            <div style="flex:1">
              <a href="https://youtube.com/watch?v=${v.video_id}" target="_blank" style="color:var(--text);text-decoration:none;font-weight:600;font-size:14px">${escHtml(v.title||'')}</a>
              <div style="margin-top:6px;display:flex;gap:16px">
                <div><span style="font-size:16px;font-weight:800;color:var(--green)">${tiFmtNum(v.vph||0)}</span> <span style="font-size:10px;color:var(--dim)">VPH</span></div>
                <div><span style="font-size:16px;font-weight:800;color:var(--accent)">${tiFmtNum(v.views||0)}</span> <span style="font-size:10px;color:var(--dim)">VIEWS</span></div>
                <div style="font-size:11px;color:var(--dim);align-self:center">${v.published||''}</div>
              </div>
            </div>
          </div>
        </div>`;
      });
    }
    document.getElementById('ti-yt-newborn-result').innerHTML=html;
  }catch(e){document.getElementById('ti-yt-newborn-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
  finally{loading(false);}
}

async function tiRemixFromViral(title,niche){
  loading(true,'Extraindo DNA & Construindo Strategy Remix...');
  try{
    const payload={title,niche,language:'Portuguese',ai_api_key:localStorage.getItem('ai_api_key')||''};
    const r=await fetch('/api/ti/strategy_from_viral',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    tiInitPage('strategy');
    const typeEl=document.getElementById('ti-strat-type');
    if(typeEl) typeEl.value=niche;
    document.getElementById('ti-strat-result').innerHTML=d.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`:`<div class="results-header"><h3>🔥 Estrategia Extraida do Video Viral</h3><div class="results-count">Base: "${escHtml(title)}"</div></div><div class="ai-text">${tiFormatAiText(d.strategy||'')}</div>`;
  }catch(e){toast('Erro ao gerar estrategia: '+e.message);}
  finally{loading(false);}
}

async function tiCompareChannels(){
  const text=document.getElementById('ti-yt-compare-input').value.trim();
  if(!text){toast('Informe os handles dos canais');return;}
  const channels=text.split('\n').map(s=>s.trim()).filter(s=>s);
  loading(true,`Comparando ${channels.length} canais...`);
  try{
    const payload={channels,yt_api_key:localStorage.getItem('yt_api_key')||''};
    const r=await fetch('/api/ti/youtube/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){document.getElementById('ti-yt-compare-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`;return;}
    let html='';
    (d.comparisons||[]).forEach(comp=>{
      const c=comp.channel||{};
      html+=`<div class="score-card">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
          ${c.thumbnail?`<img src="${c.thumbnail}" style="width:60px;height:60px;border-radius:50%;object-fit:cover">`:''}
          <div><h3 style="color:var(--accent)">${escHtml(c.title||'')}</h3>
          <div style="font-size:12px;color:var(--dim)">${tiFmtNum(c.subscribers||0)} subs · ${c.video_count||0} videos</div></div>
        </div>
        <div class="yt-metric-grid">
          <div class="yt-metric-box"><div class="value">${tiFmtNum(comp.avg_vph||0)}</div><div class="label">Avg VPH</div></div>
          <div class="yt-metric-box"><div class="value">${tiFmtNum(comp.max_vph||0)}</div><div class="label">Peak VPH</div></div>
          <div class="yt-metric-box"><div class="value">${tiFmtNum(comp.avg_views||0)}</div><div class="label">Avg Views</div></div>
        </div>
        ${comp.best_video?`<div style="margin-top:8px;font-size:12px"><b style="color:var(--green)">Melhor:</b> ${escHtml(comp.best_video.title||'')} (${tiFmtNum(comp.best_video.vph||0)} VPH)</div>`:''}
        ${comp.worst_video?`<div style="font-size:12px;margin-top:4px"><b style="color:var(--red)">Pior:</b> ${escHtml(comp.worst_video.title||'')} (${tiFmtNum(comp.worst_video.vph||0)} VPH)</div>`:''}
      </div>`;
    });
    document.getElementById('ti-yt-compare-result').innerHTML=html;
  }catch(e){document.getElementById('ti-yt-compare-result').innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
  finally{loading(false);}
}

// ---- COMPETITOR RADAR ----
async function tiLoadSavedChannels(){
  const saved=JSON.parse(localStorage.getItem('ti_saved_channels')||'[]');
  let html='';
  if(!saved.length){
    document.getElementById('ti-yt-saved-list').innerHTML='<p style="color:var(--dim)">Nenhum canal salvo ainda. Valide subnichos para encontrar e salvar canais.</p>';
    return;
  }
  html+=`<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
    <h3 style="color:var(--accent)">📡 Competitor Radar</h3>
    <div style="display:flex;gap:8px">
      <button class="btn btn-primary" style="padding:8px 16px" onclick="tiSyncChannels()">🔄 Auto-Sync Radar</button>
      <button class="btn btn-secondary" style="padding:8px 16px" onclick="tiExportSavedChannelsCSV()">📥 CSV</button>
    </div>
  </div>
  <div id="ti-yt-radar-result" style="margin-bottom:20px"></div>`;
  saved.forEach(ch=>{
    html+=`<div class="score-card" style="margin-bottom:12px;display:flex;align-items:center;gap:16px">
      ${ch.thumbnail?`<img src="${ch.thumbnail}" style="width:50px;height:50px;border-radius:50%">`:''}
      <div style="flex:1">
        <h3 style="color:var(--accent);margin-bottom:4px">${escHtml(ch.name||'')}</h3>
        <div style="font-size:12px;color:var(--dim)">Subnicho: <span style="color:var(--purple);font-weight:600">${escHtml(ch.subniche||'?')}</span></div>
      </div>
      <div style="text-align:right">
        <div style="font-size:18px;font-weight:800;color:var(--green)">${tiFmtNum(ch.avg_vph||0)} <span style="font-size:10px;font-weight:normal;color:var(--dim)">VPH</span></div>
        <div style="font-size:12px;color:var(--dim)">${tiFmtNum(ch.subscribers||0)} subs</div>
      </div>
      <button class="btn btn-primary" style="padding:8px 12px;font-size:12px" onclick="document.getElementById('ti-yt-ch-input').value='${ch.id}';tiShowYtTab('ti-yt-channel');tiScanChannel()">📊 Deep Analyze</button>
    </div>`;
  });
  document.getElementById('ti-yt-saved-list').innerHTML=html;
}

async function tiSyncChannels(){
  const saved=JSON.parse(localStorage.getItem('ti_saved_channels')||'[]');
  if(!saved.length)return;
  const channel_ids=saved.map(c=>c.id);
  const el=document.getElementById('ti-yt-radar-result');
  if(!el)return;
  el.innerHTML='<div style="color:var(--accent);padding:12px;text-align:center"><div class="spinner" style="width:20px;height:20px;margin:0 auto 8px"></div>Escaneando todos os concorrentes...</div>';
  try{
    const payload={channels:channel_ids,yt_api_key:localStorage.getItem('yt_api_key')||''};
    const r=await fetch('/api/ti/youtube/sync_channels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const d=await r.json();
    if(d.error){el.innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(d.error)}</div>`;return;}
    if(!d.sync_results||!d.sync_results.length){el.innerHTML='<div class="score-card" style="color:var(--dim)">✅ Todos escaneados. Sem videos explosivos (VPH>10 em 48h).</div>';return;}
    let html=`<div class="score-card" style="border-left:4px solid var(--purple)"><h3 style="color:var(--purple);margin-bottom:12px">🚨 VIDEOS EXPLOSIVOS DETECTADOS!</h3>`;
    d.sync_results.forEach(v=>{
      html+=`<div style="display:flex;gap:12px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px solid var(--border)">
        <div style="flex:1">
          <div style="font-size:12px;color:var(--dim);margin-bottom:4px">${escHtml(v.channel_name||'')} · ${v.hours_ago||0}h atras</div>
          <a href="https://youtube.com/watch?v=${v.video_id}" target="_blank" style="color:var(--text);font-weight:600;font-size:14px;text-decoration:none">${escHtml(v.title||'')} 🔗</a>
        </div>
        <div style="text-align:right">
          <div style="font-size:18px;font-weight:800;color:#ff2a2a">${v.vph||0} <span style="font-size:10px;color:var(--dim)">VPH</span></div>
          <div style="font-size:12px;color:var(--dim)">${tiFmtNum(v.views||0)} views</div>
        </div>
      </div>`;
    });
    html+='</div>';
    el.innerHTML=html;
  }catch(e){el.innerHTML=`<div class="score-card" style="color:var(--red)">Erro: ${e.message}</div>`;}
}

function tiExportSavedChannelsCSV(){
  const saved=JSON.parse(localStorage.getItem('ti_saved_channels')||'[]');
  if(!saved.length)return;
  let csv='Channel Name,Subniche,Subscribers,Avg VPH,Channel URL\n';
  saved.forEach(c=>{csv+=`"${(c.name||'').replace(/"/g,'""')}","${(c.subniche||'').replace(/"/g,'""')}",${c.subscribers||0},${c.avg_vph||0},https://youtube.com/channel/${c.id}\n`;});
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=`ti_radar_${new Date().toISOString().slice(0,10)}.csv`;a.click();
}

// ---- MY CHANNELS (client-side localStorage) ----
function tiGetMyChannels(){try{return JSON.parse(localStorage.getItem('ti_my_channels')||'[]');}catch(e){return[];}}
function tiSaveMyChannels(arr){
  try{localStorage.setItem('ti_my_channels',JSON.stringify(arr));return true;}
  catch(e){if(e.name==='QuotaExceededError')toast('🚨 LocalStorage cheio. Delete canais antigos.');return false;}
}

function tiLoadChannels(){
  const channels=tiGetMyChannels();
  const el=document.getElementById('ti-channels-list');
  if(!el)return;
  if(!channels.length){el.innerHTML='<p style="color:var(--dim)">Nenhum canal cadastrado. Adicione um acima!</p>';return;}
  let html='';
  channels.forEach(c=>{
    const kws=(c.keywords||[]).map(k=>`<span class="tag tag-blue">${escHtml(k)}</span>`).join('');
    const subs=(c.subniches||[]).map(s=>`<span class="tag tag-green">${escHtml(s)}</span>`).join('');
    const structs=(c.reference_structures||[]).map(s=>`<span class="tag tag-purple">${escHtml(s)}</span>`).join('');
    const trends=(c.trending_themes||[]).map(t=>`<span class="tag tag-gold">${escHtml(t)}</span>`).join('');
    const cdata=JSON.stringify(c).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    html+=`<div class="niche-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h3>${escHtml(c.name||'')}</h3>
        <div style="display:flex;gap:6px">
          <button class="btn btn-primary" style="padding:6px 12px;font-size:11px" onclick='tiAnalyzeChannel(${cdata})'>🧠 Descobrir Subnichos</button>
          <button class="btn btn-secondary" style="padding:6px 12px;font-size:11px" onclick="tiDeleteChannel('${c.id}')">❌</button>
        </div>
      </div>
      <div style="font-size:12px;color:var(--dim);margin:4px 0">${escHtml(c.url||'')}</div>
      <div style="font-size:12px;color:#aaa;margin:4px 0">🎯 <b>Nicho:</b> ${escHtml(c.niche||'')} | <b>Micro:</b> ${escHtml(c.micro_niche||'')}</div>
      ${subs?'<div style="margin:6px 0"><b style="font-size:10px;color:#4ecca3">SUBNICHOS</b><div class="tags">'+subs+'</div></div>':''}
      ${kws?'<div style="margin:6px 0"><b style="font-size:10px;color:#3b82f6">KEYWORDS</b><div class="tags">'+kws+'</div></div>':''}
      ${structs?'<div style="margin:6px 0"><b style="font-size:10px;color:#8b5cf6">ESTRUTURAS</b><div class="tags">'+structs+'</div></div>':''}
      ${trends?'<div style="margin:6px 0"><b style="font-size:10px;color:#FFD700">TRENDING THEMES</b><div class="tags">'+trends+'</div></div>':''}
    </div>`;
  });
  el.innerHTML=html;
}

async function tiAddChannel(){
  const data={
    id:'ti_ch_'+Date.now(),
    name:document.getElementById('ti-ch-name').value.trim(),
    url:document.getElementById('ti-ch-url').value.trim(),
    niche:document.getElementById('ti-ch-niche').value.trim(),
    micro_niche:document.getElementById('ti-ch-micro').value.trim(),
    subniches:document.getElementById('ti-ch-subniches').value.split(',').map(s=>s.trim()).filter(s=>s),
    keywords:document.getElementById('ti-ch-keywords').value.split(',').map(s=>s.trim()).filter(s=>s),
    titles:document.getElementById('ti-ch-titles').value.split('\n').filter(t=>t.trim()),
    reference_structures:document.getElementById('ti-ch-structures').value.split('\n').filter(t=>t.trim()),
    trending_themes:document.getElementById('ti-ch-trends').value.split('\n').filter(t=>t.trim()),
  };
  if(!data.name){toast('Informe o nome do canal');return;}
  const channels=tiGetMyChannels();
  channels.push(data);
  if(!tiSaveMyChannels(channels))return;
  ['ti-ch-name','ti-ch-url','ti-ch-niche','ti-ch-micro','ti-ch-subniches','ti-ch-keywords'].forEach(id=>{const el=document.getElementById(id);if(el)el.value='';});
  ['ti-ch-titles','ti-ch-structures','ti-ch-trends'].forEach(id=>{const el=document.getElementById(id);if(el)el.value='';});
  toast('✅ Canal adicionado!');
  tiLoadChannels();
}

function tiDeleteChannel(id){
  if(!confirm('Deletar este canal?'))return;
  tiSaveMyChannels(tiGetMyChannels().filter(c=>c.id!==id));
  tiLoadChannels();
}

async function tiAnalyzeChannel(channel){
  loading(true,'Descobrindo subnichos com AI...');
  const r=await tiPost('/api/ti/channels/analyze',{channel,reference_structures:channel.reference_structures||[],trending_themes:channel.trending_themes||[]});
  const el=document.getElementById('ti-channel-analysis');
  if(el) el.innerHTML=r.error?`<div class="score-card" style="color:var(--red)">Erro: ${escHtml(r.error)}</div>`:`<div class="ai-text">${tiFormatAiText(r.analysis||'')}</div>`;
  if(r&&!r.error) loading(false);
}

// ---- AI MONITOR ----
async function tiLoadAiMonitor(){
  try{
    const r=await fetch('/api/ti/ai/status');
    if(!r.ok)throw new Error(`HTTP ${r.status}`);
    const d=await r.json();
    const avail=(d.providers||[]).filter(p=>p.available).length;
    const total=(d.providers||[]).length;
    const hColors={healthy:'#10b981',degraded:'#f59e0b',down:'#ef4444'};
    const elH=document.getElementById('ti-mon-health');
    const elA=document.getElementById('ti-mon-avail');
    const elC=document.getElementById('ti-mon-calls');
    const elCa=document.getElementById('ti-mon-cache');
    const elP=document.getElementById('ti-mon-providers');
    const elL=document.getElementById('ti-mon-log');
    if(elH){elH.textContent=(d.health||'UNKNOWN').toUpperCase();elH.style.color=hColors[d.health]||'#888';}
    if(elA){elA.textContent=`${avail}/${total}`;elA.style.color=avail>=3?'#10b981':avail>=1?'#f59e0b':'#ef4444';}
    if(elC) elC.textContent=d.stats?.total_calls||0;
    if(elCa) elCa.textContent=d.stats?.cache_hits||0;
    let html='';
    (d.providers||[]).forEach(p=>{
      const online=p.available;
      const cd=Math.round(p.cooldown_remaining||0);
      const color=online?'#10b981':'#ef4444';
      const typeColor=p.type==='gemini'?'#3b82f6':'#f97316';
      html+=`<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--card);border-radius:8px;border:1px solid ${online?'#1e3a2f':'#3a1e1e'}">
        <span style="font-size:18px">${online?'🟢':'🔴'}</span>
        <div style="flex:1"><div style="font-weight:600;font-size:13px">${escHtml(p.name||'Unknown')}</div>
        <div style="font-size:11px;color:var(--dim)">${escHtml(p.model||'')} <span style="color:${typeColor};font-weight:600">[${(p.type||'').toUpperCase()}]</span></div></div>
        <div style="text-align:right"><div style="font-size:12px;color:${color};font-weight:600">${online?'ONLINE':cd>0?'Cooldown '+cd+'s':'OFFLINE'}</div>
        <div style="font-size:11px;color:var(--dim)">${p.total_calls||0} calls / ${p.total_errors||0} erros</div></div>
      </div>`;
    });
    if(elP) elP.innerHTML=html;
    if(elL) elL.innerHTML=`<div style="color:var(--dim)">Gemini=${d.keys?.gemini?'✓':'✗'} Groq=${d.keys?.groq?'✓':'✗'} | Cache: ${d.cache_size||0} | Atualizado: ${new Date().toLocaleTimeString()}</div>`;
  }catch(e){
    const el=document.getElementById('ti-mon-providers');
    if(el) el.innerHTML=`<div style="color:#ef4444">Erro ao carregar status: ${e.message}</div>`;
  }
}

async function tiResetAiEngine(){
  if(!confirm('Resetar todos os cooldowns e cache do AI?'))return;
  try{await fetch('/api/ti/ai/reset',{method:'POST'});tiLoadAiMonitor();toast('✅ Reset concluido!');}
  catch(e){toast('Erro no reset: '+e.message);}
}

async function tiCheckAiStatus(){
  try{
    const r=await fetch('/api/ti/ai/status');
    const d=await r.json();
    const avail=(d.providers||[]).filter(p=>p.available).length;
    const total=(d.providers||[]).length;
    const badge=document.getElementById('ti-ai-badge');
    const hColors={healthy:'#10b981',degraded:'#f59e0b',down:'#ef4444'};
    if(badge){badge.style.borderColor=hColors[d.health]||'#888';badge.textContent=`🤖 AI ${avail}/${total} Online`;}
    let det=`AI Engine — ${(d.health||'').toUpperCase()}\n${'='.repeat(30)}\n`;
    (d.providers||[]).forEach(p=>{det+=`${p.name}: ${p.available?'✓ ONLINE':'✗ OFFLINE'} (${p.total_calls||0} calls)\n`;});
    det+=`\nCache: ${d.cache_size||0} | Total calls: ${d.stats?.total_calls||0}`;
    alert(det);
  }catch(e){alert('AI status unavailable: '+e.message);}
}

// Auto-check TI AI status on page load
(async function tiInitAiBadge(){
  try{
    const r=await fetch('/api/ti/ai/status');
    const d=await r.json();
    const badge=document.getElementById('ti-ai-badge');
    if(badge){
      const avail=(d.providers||[]).filter(p=>p.available).length;
      const total=(d.providers||[]).length;
      const hColors={healthy:'#10b981',degraded:'#f59e0b',down:'#ef4444'};
      badge.style.borderColor=hColors[d.health]||'#888';
      badge.textContent=`🤖 AI ${avail}/${total} Online`;
    }
  }catch(e){}
})();

// INIT
loadDashboard();
loadVoices();
loadKeys();
document.addEventListener('click', function(e) {
  var btn = e.target.closest('.nav-btn');
  if (!btn) return;
  var text = btn.textContent;
  if (text.includes('Thumbnail')) setTimeout(loadThumbnailDefaults, 100);
  if (text.includes('Agendador')) setTimeout(loadSchedules, 100);
  if (text.includes('Templates')) setTimeout(loadTemplates, 100);
  if (text.includes('Brand Kit')) setTimeout(loadBrandKit, 100);
  if (text.includes('YouTube Upload')) setTimeout(checkYtUploadStatus, 100);
  if (text.includes('Publicar')) setTimeout(loadSocialStatus, 100);
});
function loadThumbnailDefaults() {
  var videoEl = document.getElementById('thumb-video');
  if (window.currentAvatarPath && videoEl && !videoEl.value) videoEl.value = window.currentAvatarPath;
}
if(document.getElementById('brand-logo-opacity')) document.getElementById('brand-logo-opacity').addEventListener('input', function(){ document.getElementById('brand-logo-opacity-val').textContent = this.value+'%'; });
if(document.getElementById('brand-wm-opacity')) document.getElementById('brand-wm-opacity').addEventListener('input', function(){ document.getElementById('brand-wm-opacity-val').textContent = this.value+'%'; });


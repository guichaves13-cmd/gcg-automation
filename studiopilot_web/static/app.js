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
  statusEl.innerHTML = 'Executando diagnóstico profundo...';
  statusEl.style.color = 'var(--text)';
  try {
    const r = await fetch('/api/system/health');
    const d = await r.json();
    const ok = s => s ? '<span style="color:var(--green)">✅ OK</span>' : '<span style="color:var(--red)">❌ FALHA</span>';
    const warn = s => s ? '<span style="color:var(--green)">✅ OK</span>' : '<span style="color:var(--yellow)">⚠️ Nao instalado</span>';
    let txt = `<b>Status:</b> <span style="color:${d.status==='healthy'?'var(--green)':d.status==='warning'?'var(--yellow)':'var(--red)'}"><b>${d.status.toUpperCase()}</b></span><br>`;
    txt += `FFmpeg: ${ok(d.ffmpeg)}<br>`;
    txt += `FFprobe: ${ok(d.ffprobe)}<br>`;
    txt += `Chaves de API: ${ok(d.api_keys)} <span style="color:var(--dim);font-size:11px">(${(d.configured_keys||[]).join(', ')||'nenhuma'})</span><br>`;
    txt += `Whisper AI: ${warn(d.whisper)} <span style="color:var(--dim);font-size:11px">${d.whisper?'Transcricao ativa':'pip install openai-whisper'}</span><br>`;
    txt += `Armazenamento: ${ok(d.storage)}<br>`;
    txt += `<br><span style="color:var(--dim)">${d.message}</span>`;
    statusEl.innerHTML = txt;
    if(d.status === 'healthy') statusEl.style.color = 'var(--green)';
    else statusEl.style.color = 'var(--accent)';
  } catch(e) {
    statusEl.innerHTML = 'Falha ao conectar com o motor de IA: ' + e.message;
    statusEl.style.color = '#ef4444';
  }
}

// DASHBOARD
async function loadDashboard(){
  // Date
  const now=new Date();
  const days=['Domingo','Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira','Sexta-feira','Sábado'];
  const months=['janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
  document.getElementById('dash-date').textContent=`${days[now.getDay()]}, ${now.getDate()} de ${months[now.getMonth()]}  ${now.toLocaleTimeString('pt-BR')}`;
  // Greeting
  const h=now.getHours();
  const greeting=h<12?'Bom dia':h<18?'Boa tarde':'Boa noite';
  document.querySelector('.banner h2').innerHTML=`${greeting}, Guilherme <span class="wave">👋</span>`;
  // Stats
  try{
    const s=await(await fetch('/api/stats')).json();
    document.getElementById('stat-today').textContent=s.today||0;
    document.getElementById('stat-total').textContent=s.total||0;
    // Activity
    const al=document.getElementById('activity-list');
    if(s.history&&s.history.length){
      al.innerHTML=s.history.slice(0,5).map(h=>`<div class="activity-item"><span class="activity-dot"></span><span class="activity-text">✅ ${escHtml(h.f)}</span><span class="activity-time">${h.d}</span></div>`).join('');
    }
  }catch(e){}
  // Storage
  try{
    const st=await(await fetch('/api/storage')).json();
    document.getElementById('stat-storage').textContent=fmtBytes(st.total_bytes);
  }catch(e){}
  // Plans count
  try{
    const plans=await(await fetch('/api/plans')).json();
    document.getElementById('stat-pipeline').textContent=plans.length;
    const counts={idea:0,script:0,prompts:0,production:0,done:0};
    plans.forEach(p=>counts[p.status]=(counts[p.status]||0)+1);
    document.getElementById('pipe-plan').textContent=counts.idea+counts.script;
    document.getElementById('pipe-prod').textContent=counts.prompts+counts.production;
    document.getElementById('pipe-done').textContent=counts.done;
  }catch(e){}
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
  ['google_ai','youtube','pexels','pixabay','unsplash','youtube_channels'].forEach(k=>{
    const el=document.getElementById('key-'+k);
    if(el&&el.value.trim())data[k]=el.value.trim();
  });
  const r=await fetch('/api/keys/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const d=await r.json();
  document.getElementById('keys-status').textContent=`✅ ${d.saved} keys salvas!`;
  toast(d.saved+' keys salvas!');
}


// VEO3
function updateVeo3Count(){
  const t=document.getElementById('veo3-prompts')?.value||'';
  const count=t.split('\n').filter(l=>l.trim()).length;
  const el=document.getElementById('veo3-count');
  if(el) el.textContent=count;
}
document.getElementById('veo3-prompts')?.addEventListener('input',updateVeo3Count);

async function autoGenerateVeo3Prompts() {
  if(!currentAvatarPath){
    toast('Carregue um avatar primeiro na aba Sincronizador!');
    showPage('sync');
    return;
  }
  loading(true,'IA analisando video e gerando prompts VEO3 cinematicos...');
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
    toast(d.count+' prompts VEO3 gerados! Tema: '+(d.theme||'detectado'));
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
      toast('Erro no upload!');
    }
  };
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

  // quality report box
  if(st.quality_score!=null && phIdx>=8){
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

  // done state
  if(!d.running){
    if(d.error){
      document.getElementById('sync-msg').innerHTML='<span style="color:var(--red)">❌ Erro: '+escHtml(d.error)+'</span>';
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

function startMonitoringStream(){
  if(_sseSource){_sseSource.close();}
  const statusEl=document.getElementById('sync-status');
  if(statusEl) statusEl.style.display='block';

  // reset panel
  document.getElementById('quality-report').style.display='none';
  document.getElementById('live-log').innerHTML='';
  document.getElementById('sync-bar').style.background='var(--accent)';
  for(let i=1;i<=8;i++){
    const row=document.getElementById('ai-ph-'+i);
    const dot=document.getElementById('dot-'+i);
    if(row){row.className='ai-phase-row';}
    if(dot){dot.className='ai-ph-dot';dot.textContent='●';}
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
    // SSE failed — fall back to polling
    if(_sseSource){_sseSource.close();_sseSource=null;}
    _pollFallback();
  };
}

function _pollFallback(){
  const iv=setInterval(async()=>{
    try{
      const d=await(await fetch('/api/pipeline/status')).json();
      _updateMonitorPanel(d);
      if(!d.running){clearInterval(iv);}
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
    if(d.error){toast('Erro: '+d.error);return}
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
      if(d2.error){toast('Erro: '+d2.error);return;}
      
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

// INIT
loadDashboard();
loadVoices();
loadKeys(); // preenche canais YouTube e outros campos salvos

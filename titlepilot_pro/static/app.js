const API = '';
function showPage(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('page-'+id).classList.add('active');
  document.querySelector(`[data-page="${id}"]`).classList.add('active');
}
function loading(show,text){
  document.getElementById('loading').style.display=show?'flex':'none';
  if(text) document.getElementById('loading-text').textContent=text;
}
function getAIKey(){ return localStorage.getItem('ai_api_key')||''; }
function saveAIKey(){ 
  const k=document.getElementById('global-ai-key').value.trim();
  localStorage.setItem('ai_api_key',k);
  alert('Chave AI salva no seu navegador!');
}

async function post(url,data){
  loading(true,'Analyzing with Gemini AI...');
  try{
    data.ai_api_key = getAIKey();
    data.yt_api_key = localStorage.getItem('yt_api_key') || '';
    const r=await fetch(API+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    const text = await r.text();
    try {
      return JSON.parse(text);
    } catch(e) {
      return {error: `Server Error (${r.status}): ${text.substring(0, 100)}...`};
    }
  }catch(err){
    return {error: err.message};
  }finally{loading(false)}
}
function gradeClass(g){return 'grade-'+(g||'F')}
function gradeColor(g){return{S:'#FFD700',A:'#4ecca3',B:'#3b82f6',C:'#f59e0b',D:'#e94560',F:'#666'}[g]||'#666'}
function barColor(score){return score>=80?'#FFD700':score>=60?'#4ecca3':score>=40?'#3b82f6':score>=20?'#f59e0b':'#e94560'}

// TITLE ANALYZER
async function analyzeTitle(){
  const title=document.getElementById('analyze-input').value.trim();
  if(!title)return;
  const r=await post('/api/analyze',{title});
  document.getElementById('analyze-result').innerHTML=renderScoreCard(r);
}
async function deepAnalyze(){
  const title=document.getElementById('analyze-input').value.trim();
  if(!title)return;
  loading(true,'Running AI deep analysis...');
  const r=await post('/api/deep_analysis',{title});
  let html=renderScoreCard(r);
  if(r.ai_deep_analysis){
    html+=`<div class="ai-text">${escHtml(r.ai_deep_analysis)}</div>`;
  }
  document.getElementById('analyze-result').innerHTML=html;
}
function renderScoreCard(r){
  let structs=r.structures.map(s=>`<span class="tag tag-green">${s.name} <span style="opacity:.7">+${Math.round((s.ctr_boost-1)*100)}% CTR</span></span>`).join('');
  let emots=r.emotional_words.map(w=>`<span class="tag tag-purple">${w}</span>`).join('');
  let powers=r.power_words.map(w=>`<span class="tag tag-gold">${w}</span>`).join('');
  let issues=r.issues.map(i=>`<span class="tag tag-red">${i}</span>`).join('');
  let suggs=r.suggestions.map(s=>`<li>💡 ${escHtml(s)}</li>`).join('');
  return `<div class="score-card">
    <div class="score-header">
      <div class="score-circle ${gradeClass(r.grade)}">${r.grade}<div style="font-size:11px;font-weight:500">${r.score}/100</div></div>
      <div style="flex:1">
        <div class="score-title">${escHtml(r.title)}</div>
        <div class="score-meta">${r.length} characters · ${r.words} words · ${r.length>=70&&r.length<=95?'<span style="color:var(--green)">✓ Optimal length</span>':r.length<60?'<span style="color:var(--red)">✗ Too short</span>':r.length>100?'<span style="color:var(--red)">✗ Too long</span>':'<span style="color:var(--accent)">○ Acceptable length</span>'}</div>
      </div>
    </div>
    <div class="score-bar"><div class="score-fill" style="width:${r.score}%;background:${barColor(r.score)}"></div></div>
    
    ${structs?`<div style="margin:20px 0">
      <div class="section-label section-label-green">✅ VIRAL STRUCTURES DETECTED</div>
      <div class="tags">${structs}</div>
    </div>`:''}
    
    ${emots?`<div style="margin:20px 0">
      <div class="section-label section-label-purple">🎯 EMOTIONAL TRIGGERS</div>
      <div class="tags">${emots}</div>
    </div>`:''}
    
    ${powers?`<div style="margin:20px 0">
      <div class="section-label section-label-gold">⚡ POWER WORDS (ALL CAPS)</div>
      <div class="tags">${powers}</div>
    </div>`:''}
    
    ${issues?`<div style="margin:20px 0">
      <div class="section-label section-label-red">⚠️ ISSUES FOUND</div>
      <div class="tags">${issues}</div>
    </div>`:''}
    
    ${suggs?`<div style="margin:20px 0">
      <div class="section-label section-label-blue">💡 SUGGESTIONS TO IMPROVE</div>
      <ul style="list-style:none;padding:0;margin-top:8px">${suggs}</ul>
    </div>`:''}
  </div>`;
}

// GENERATOR
async function generateTitles(){
  const topic=document.getElementById('gen-topic').value.trim();
  if(!topic)return;
  const lang=document.getElementById('gen-lang').value;
  const niche=document.getElementById('gen-niche').value.trim();
  loading(true,'Generating viral titles with AI...');
  const r=await post('/api/generate',{topic,language:lang,niche});
  const titles=r.titles||[];
  const avgScore=titles.length?Math.round(titles.reduce((s,t)=>s+t.score,0)/titles.length):0;
  const avgLen=titles.length?Math.round(titles.reduce((s,t)=>s+t.length,0)/titles.length):0;
  
  let html=`<div class="results-header">
    <h3>⚡ Generated for: ${escHtml(r.topic)}</h3>
    <div class="results-stats">
      <div class="results-stat"><div class="val" style="color:var(--accent)">${titles.length}</div><div class="lbl">Titles</div></div>
      <div class="results-stat"><div class="val" style="color:${barColor(avgScore)}">${avgScore}</div><div class="lbl">Avg Score</div></div>
      <div class="results-stat"><div class="val" style="color:${avgLen>=70?'var(--green)':'var(--red)'}"> ${avgLen}c</div><div class="lbl">Avg Length</div></div>
    </div>
  </div>`;
  
  titles.forEach((t,i)=>{
    const lenColor=t.length>=70&&t.length<=95?'var(--green)':t.length>=55?'var(--accent)':'var(--red)';
    const structTags=(t.structures||[]).map(s=>`<span class="title-struct-tag">${s}</span>`).join('');
    html+=`<div class="title-item">
      <div class="title-score ${gradeClass(t.grade)}">${t.grade}<div style="font-size:11px;font-weight:500">${t.score}</div></div>
      <div style="flex:1">
        <div class="title-text">${escHtml(t.title)}</div>
        ${structTags?`<div class="title-structures">${structTags}</div>`:''}
        ${t.thumbnail_concept ? `<div style="font-size:12px;color:var(--dim);margin-top:6px;padding:6px;background:var(--card);border-left:2px solid var(--purple)">🖼️ <b>Thumbnail Concept:</b> ${escHtml(t.thumbnail_concept)}</div>` : ''}
      </div>
      <div class="title-len" style="color:${lenColor}">${t.length}c</div>
    </div>`;
  });
  document.getElementById('gen-result').innerHTML=html;
}

// Load niches into dropdown on page load
async function loadNichesDropdown(){
  try{
    const r=await fetch('/api/niche_list');
    const d=await r.json();
    if(d.niches && Object.keys(d.niches).length){
      let html='<option value="">All Themes (Auto-Discover)</option>';
      for(let [category, niches] of Object.entries(d.niches)){
        html+=`<optgroup label="${escHtml(category)}">`;
        niches.forEach(n=>{
          html+=`<option value="${escHtml(n)}">${escHtml(n)}</option>`;
        });
        html+=`</optgroup>`;
      }
      document.getElementById('sub-theme').innerHTML=html;
    }
  }catch(e){console.error('Failed to load niches',e)}
}
loadNichesDropdown();

// SUBNICHE
async function findSubniches(){
  const dropdownTheme=document.getElementById('sub-theme').value.trim();
  const customTheme=document.getElementById('sub-theme-custom').value.trim();
  const theme = customTheme || dropdownTheme;
  const lang=document.getElementById('sub-lang').value;
  loading(true,'Discovering subniches with AI...');
  const r=await post('/api/subniche',{theme,language:lang});
  let html='';
  if(r.niches&&r.niches.length){
    html+=`<div class="results-header"><h3>🔥 ${r.niches.length} Subniches Found</h3><div class="results-count">${escHtml(theme||'All themes')} · ${lang}</div></div>`;
    
    r.niches.forEach((n,i)=>{
      const demColor=n.demand>=7?'#4ecca3':n.demand>=4?'#f59e0b':'#e94560';
      const supColor=n.supply<=3?'#4ecca3':n.supply<=6?'#f59e0b':'#e94560';
      const oppColor=n.opportunity>=6?'#4ecca3':n.opportunity>=3?'#f59e0b':'#e94560';
      let kws=(n.keywords||[]).map(k=>`<span class="tag tag-blue">${k}</span>`).join('');
      
      html+=`<div class="niche-card">
        <h3>#${i+1} — ${escHtml(n.name)}</h3>
        
        <div class="niche-meter">
          <div class="meter">
            <div class="meter-label">📈 DEMAND</div>
            <div class="meter-bar"><div class="meter-fill" style="width:${n.demand*10}%;background:${demColor}"></div></div>
            <div style="font-size:13px;color:${demColor};margin-top:4px;font-weight:700">${n.demand}/10</div>
          </div>
          <div class="meter">
            <div class="meter-label">📉 SUPPLY</div>
            <div class="meter-bar"><div class="meter-fill" style="width:${n.supply*10}%;background:${supColor}"></div></div>
            <div style="font-size:13px;color:${supColor};margin-top:4px;font-weight:700">${n.supply}/10</div>
          </div>
          <div class="meter">
            <div class="meter-label">🎯 OPPORTUNITY</div>
            <div class="meter-bar"><div class="meter-fill" style="width:${n.opportunity*10}%;background:${oppColor}"></div></div>
            <div style="font-size:13px;color:${oppColor};margin-top:4px;font-weight:800">${n.opportunity}/10</div>
          </div>
        </div>
        
        <div style="margin:20px 0">
          <div class="section-label section-label-purple">👤 TARGET AUDIENCE</div>
          <div style="font-size:14px;color:#ccc;margin-top:6px;line-height:1.6">${escHtml(n.target_audience||'')}</div>
        </div>
        
        <div style="margin:20px 0">
          <div class="section-label section-label-red">😤 AUDIENCE PAIN POINT</div>
          <div style="font-size:14px;color:#ccc;margin-top:6px;line-height:1.6">${escHtml(n.audience_pain||'')}</div>
        </div>
        
        <div style="margin:20px 0">
          <div class="section-label section-label-orange">🎯 CONTENT ANGLE</div>
          <div style="font-size:14px;color:#ccc;margin-top:6px;line-height:1.6">${escHtml(n.content_angle||'')}</div>
        </div>
        
        ${n.estimated_views_per_video?`<div style="margin:20px 0">
          <div class="section-label section-label-gold">📊 ESTIMATED VIEWS</div>
          <div style="font-size:16px;color:var(--accent);margin-top:6px;font-weight:700">${escHtml(n.estimated_views_per_video)}</div>
        </div>`:''}
        
        <div style="margin:20px 0">
          <div class="section-label section-label-blue">🔑 KEYWORDS</div>
          <div class="tags">${kws}</div>
        </div>
        
        <div style="margin:20px 0">
          <div class="section-label section-label-green">📝 EXAMPLE VIRAL TITLES</div>
          ${(n.example_titles||[]).map(t=>`<div class="title-item" style="margin:6px 0">
            <div class="title-text">${escHtml(t)}</div>
            <div class="title-len">${t.length}c</div>
          </div>`).join('')}
        </div>
        
        <div style="display:flex;gap:8px;margin-top:16px">
          <button class="btn-primary" style="flex:1;padding:10px;font-size:13px;border-radius:10px;border:none;cursor:pointer;font-family:inherit;font-weight:700" onclick="validateSubniche('${escHtml(n.name).replace(/'/g,"\\'")}', ${JSON.stringify(n.keywords||[]).replace(/"/g,'&quot;')}, 'validate-${i}')">📊 Validar com YouTube (Dados Reais)</button>
          <button class="btn-secondary" style="padding:10px 16px;font-size:13px;border-radius:10px;cursor:pointer;font-family:inherit" onclick="generateFromSubniche('${escHtml(n.name).replace(/'/g,"\\'")}')">⚡ Gerar Títulos</button>
        </div>
        <div id="validate-${i}" style="margin-top:12px"></div>
      </div>`;
    });
  } else if(r.raw){
    html=`<div class="ai-text">${escHtml(r.raw)}</div>`;
  }
  document.getElementById('sub-result').innerHTML=html;
}

// VALIDATE SUBNICHE with YouTube Data API
async function validateSubniche(name, keywords, targetId){
  const el=document.getElementById(targetId);
  if(!el)return;
  el.innerHTML='<div style="padding:16px;text-align:center;color:var(--accent)"><div class="spinner" style="width:24px;height:24px;border-width:3px;margin:0 auto 8px"></div>Buscando dados reais do YouTube...</div>';
  try{
    const r=await fetch('/api/subniche_validate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name,keywords:typeof keywords==='string'?JSON.parse(keywords):keywords})});
    const d=await r.json();
    if(d.error){el.innerHTML=`<div style="color:var(--red);padding:12px">⚠️ ${escHtml(d.error)}</div>`;return}
    
    let html=`<div style="background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;margin-top:8px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <div class="section-label section-label-gold" style="margin:0;padding:0;border:none">📡 YOUTUBE VALIDATION — ${escHtml(d.subniche)}</div>
        <div style="display:flex;gap:12px">
          <div style="text-align:center"><div style="font-size:20px;font-weight:800;color:var(--accent)">${d.total_videos}</div><div style="font-size:10px;color:var(--dim)">VIDEOS</div></div>
          <div style="text-align:center"><div style="font-size:20px;font-weight:800;color:var(--green)">${d.total_channels}</div><div style="font-size:10px;color:var(--dim)">CHANNELS</div></div>
          <div style="text-align:center"><div style="font-size:20px;font-weight:800;color:var(--purple)">${fmtNum(d.avg_vph)}</div><div style="font-size:10px;color:var(--dim)">AVG VPH</div></div>
        </div>
      </div>`;
    
    // Trending channels
    if(d.channels&&d.channels.length){
      html+=`<div class="section-label section-label-purple" style="margin-top:16px">📺 TRENDING CHANNELS IN THIS SUBNICHE</div>`;
      d.channels.forEach(ch=>{
        const subs=ch.subscribers>=1000000?(ch.subscribers/1000000).toFixed(1)+'M':ch.subscribers>=1000?Math.round(ch.subscribers/1000)+'K':ch.subscribers;
        html+=`<div style="display:flex;align-items:center;gap:12px;padding:10px;background:var(--card2);border-radius:10px;margin:6px 0">
          ${ch.thumbnail?`<img src="${ch.thumbnail}" style="width:36px;height:36px;border-radius:50%">`:''}
          <div style="flex:1">
            <div style="font-weight:600;font-size:14px">${escHtml(ch.name)}</div>
            <div style="font-size:11px;color:var(--dim)">${subs} subs · ${ch.videos_found} videos found · VPH: <span style="color:var(--green);font-weight:700">${fmtNum(ch.avg_vph)}</span></div>
          </div>
          <button class="btn-secondary" style="font-size:11px;padding:6px 12px;border-radius:8px;cursor:pointer;font-family:inherit" onclick="saveChannel('${ch.id}','${escHtml(ch.name).replace(/'/g,"\\'")}',${ch.subscribers||0},${ch.avg_vph||0},'${escHtml(name).replace(/'/g,"\\'")}','${ch.thumbnail||''}')">💾 Salvar</button>
        </div>`;
      });
    }
    
    // Top videos
    if(d.videos&&d.videos.length){
      html+=`<div class="section-label section-label-green" style="margin-top:20px">🔥 TOP VIDEOS (BY VIEWS/HOUR)</div>`;
      d.videos.slice(0,8).forEach(v=>{
        const vphColor=v.vph>=1000?'#ff6b35':v.vph>=100?'var(--accent)':v.vph>=10?'var(--green)':'var(--dim)';
        html+=`<div style="display:flex;align-items:center;gap:12px;padding:10px;background:var(--card2);border-radius:10px;margin:6px 0">
          ${v.thumbnail?`<img src="${v.thumbnail}" style="width:64px;height:36px;border-radius:6px;object-fit:cover">`:''}
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escHtml(v.title)}</div>
            <div style="font-size:11px;color:var(--dim)">${escHtml(v.channel_name)} · ${v.published} · ${fmtNum(v.views)} views</div>
          </div>
          <div style="text-align:center;min-width:60px">
            <div style="font-size:16px;font-weight:800;color:${vphColor}">${fmtNum(v.vph)}</div>
            <div style="font-size:9px;color:var(--dim)">VPH</div>
          </div>
        </div>`;
      });
    }
    
    html+=`</div>`;
    el.innerHTML=html;
  }catch(e){el.innerHTML=`<div style="color:var(--red);padding:12px">Erro: ${e.message}</div>`}
}

function fmtNum(n){
  if(!n)return '0';
  if(n>=1000000)return (n/1000000).toFixed(1)+'M';
  if(n>=1000)return Math.round(n/1000)+'K';
  return Math.round(n).toString();
}

// SAVED TREND CHANNELS (Local Storage)
function saveChannel(id,name,subs,vph,subniche,thumb){
  try{
    let saved = JSON.parse(localStorage.getItem('saved_channels') || '[]');
    if(!saved.find(c => c.id === id)){
      saved.push({id, name, subscribers:subs, avg_vph:vph, subniche, thumbnail:thumb, saved_at: new Date().toISOString()});
      localStorage.setItem('saved_channels', JSON.stringify(saved));
      toast('💾 Canal salvo no seu painel! Total: '+saved.length);
    } else {
      toast('⚠️ Canal já estava salvo!');
    }
  }catch(e){toast('Erro: '+e.message)}
}

function generateFromSubniche(name){
  document.getElementById('gen-topic').value=name;
  showPage('generator');
  setTimeout(generateTitles,500);
}

// TRENDS
async function scanTrends(){
  const cat=document.getElementById('trend-cat').value;
  const lang=document.getElementById('trend-lang').value;
  loading(true,'Scanning YouTube trends...');
  const r=await post('/api/trend_scanner',{category:cat,language:lang});
  const text=r.trends||'';
  document.getElementById('trend-result').innerHTML=`<div class="results-header"><h3>📈 Trend Analysis</h3><div class="results-count">${cat==='all'?'All Categories':cat} · ${lang}</div></div><div class="ai-text">${formatAiText(text)}</div>`;
}

// STRATEGY
async function getStrategy(){
  const type=document.getElementById('strat-type').value.trim();
  const audience=document.getElementById('strat-audience').value.trim();
  const lang=document.getElementById('strat-lang').value;
  const titles=document.getElementById('strat-titles').value.trim().split('\n').filter(t=>t.trim());
  if(!type){alert('Enter channel type');return}
  loading(true,'Building channel strategy...');
  const r=await post('/api/channel_strategy',{channel_type:type,target_audience:audience,language:lang,titles});
  const text=r.strategy||'';
  document.getElementById('strat-result').innerHTML=`<div class="results-header"><h3>🏆 Channel Strategy</h3><div class="results-count">${escHtml(type)} · ${lang}</div></div><div class="ai-text">${formatAiText(text)}</div>`;
}

// STRATEGY REMIX (CAMINHO A/B)
async function generateRemix(){
  const topic=document.getElementById('remix-topic').value.trim();
  const lang=document.getElementById('remix-lang').value;
  const path_a=document.getElementById('remix-path-a').value.trim() || 'Curiosity & Mystery';
  const path_b=document.getElementById('remix-path-b').value.trim() || 'Fear & Consequence';
  
  if(!topic){alert('Enter a topic for the A/B strategy');return}
  loading(true,'Generating Dual-Path A/B Strategy...');
  try{
    const r=await post('/api/strategy_remix',{topic,language:lang,path_a,path_b});
    const text=r.remix||'';
    document.getElementById('remix-result').innerHTML=`<div class="results-header"><h3>🔀 A/B Remix Strategy</h3><div class="results-count">${escHtml(topic)}</div></div><div class="ai-text">${formatAiText(text)}</div>`;
  }catch(e){
    document.getElementById('remix-result').innerHTML=`<div style="color:var(--red);padding:12px">Erro: ${e.message}</div>`;
  }
}

// FORMAT AI TEXT — converts raw text into structured HTML
function formatAiText(text){
  if(!text) return '';
  let html = escHtml(text);
  // Headers: lines starting with # or numbers followed by period
  html = html.replace(/^(#{1,3})\s*(.+)$/gm, (m,h,t) => {
    const size = h.length === 1 ? '20px' : h.length === 2 ? '17px' : '15px';
    return `<div style="font-size:${size};font-weight:800;color:var(--accent);margin:24px 0 12px;letter-spacing:-0.5px">${t}</div>`;
  });
  // Bold markers: **text** or __text__
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text);font-weight:700">$1</strong>');
  // Numbered items with bold titles
  html = html.replace(/^(\d+[\.\)]\s*)([A-Z][^:]+:)/gm, '<div style="margin-top:16px"><span style="color:var(--accent);font-weight:800">$1$2</span></div>');
  // Bullet points
  html = html.replace(/^[-•]\s+(.+)$/gm, '<div style="padding:4px 0 4px 16px;border-left:2px solid var(--border);margin:4px 0;font-size:14px">$1</div>');
  // Line breaks
  html = html.replace(/\n\n/g, '<div style="margin:12px 0"></div>');
  html = html.replace(/\n/g, '<br>');
  return html;
}

// HOOK ANALYZER
async function analyzeHook(){
  const hook=document.getElementById('hook-input').value.trim();
  const title=document.getElementById('hook-title').value.trim();
  const lang=document.getElementById('hook-lang').value;
  
  if(!hook){alert('Please paste your script intro.');return;}
  if(!title){alert('Please provide the video title so the AI understands the context.');return;}
  
  loading(true,'Analyzing hook retention...');
  try{
    const r=await post('/api/analyze_hook',{hook,title,language:lang});
    if(r.error) {
      document.getElementById('hook-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(r.error)}</div>`;
      return;
    }
    document.getElementById('hook-result').innerHTML=`<div class="ai-text" style="font-size:15px;line-height:1.6">${formatAiText(r.analysis)}</div>`;
  }catch(e){
    document.getElementById('hook-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`;
  }
}

// SEO OPTIMIZER
async function generateSEO(){
  const title=document.getElementById('seo-title').value.trim();
  const context=document.getElementById('seo-context').value.trim();
  const lang=document.getElementById('seo-lang').value;
  
  if(!title){alert('Please provide the video title.');return;}
  
  loading(true,'Generating SEO Description & Tags...');
  try{
    const r=await post('/api/seo_optimize',{title,context,language:lang});
    if(r.error) {
      document.getElementById('seo-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(r.error)}</div>`;
      return;
    }
    document.getElementById('seo-result').innerHTML=`<div class="ai-text" style="font-size:14px;line-height:1.6">${formatAiText(r.seo)}</div>`;
  }catch(e){
    document.getElementById('seo-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`;
  }
}

// BATCH
async function batchAnalyze(){
  const text=document.getElementById('batch-input').value.trim();
  if(!text)return;
  const titles=text.split('\n').filter(t=>t.trim());
  loading(true,`Analyzing ${titles.length} titles...`);
  const r=await post('/api/analyze_batch',{titles});
  let html=`<div class="score-card">
    <div style="display:flex;gap:20px;margin-bottom:16px">
      <div style="flex:1;text-align:center"><div style="font-size:32px;font-weight:900;color:${barColor(r.avg_score)}">${r.avg_score}</div><div style="font-size:11px;color:#666">Avg Score</div></div>
      <div style="flex:1;text-align:center"><div style="font-size:32px;font-weight:900;color:#FFD700">${r.count}</div><div style="font-size:11px;color:#666">Titles</div></div>
    </div>
    <div style="margin-bottom:12px"><b style="color:#4ecca3;font-size:11px">BEST:</b> ${escHtml(r.best.title)} (${r.best.score})</div>
    <div><b style="color:#e94560;font-size:11px">WORST:</b> ${escHtml(r.worst.title)} (${r.worst.score})</div>
  </div>`;
  if(r.structures){
    html+='<div class="section"><h3>📊 Structure Usage</h3>';
    for(let[s,c]of Object.entries(r.structures)){
      const pct=Math.round(c/r.count*100);
      html+=`<div style="margin:6px 0;display:flex;align-items:center;gap:8px"><div style="width:120px;font-size:12px">${s}</div><div style="flex:1;height:8px;background:#1a1a2e;border-radius:4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:#4ecca3;border-radius:4px"></div></div><div style="font-size:11px;color:#4ecca3;width:40px;text-align:right">${pct}%</div></div>`;
    }
    html+='</div>';
  }
  html+='<h3 style="color:#FFD700;margin:16px 0 8px">All Titles (ranked)</h3>';
  (r.results||[]).forEach(t=>{
    html+=`<div class="title-item"><div class="title-score ${gradeClass(t.grade)}" style="font-size:12px">${t.grade}<br>${t.score}</div><div class="title-text">${escHtml(t.title)}</div><div class="title-len">${t.length}c</div></div>`;
  });
  document.getElementById('batch-result').innerHTML=html;
}

function escHtml(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')}

// MY CHANNELS
async function loadChannels(){
  const r=await fetch('/api/channels');
  const d=await r.json();
  const el=document.getElementById('channels-list');
  if(!el)return;
  // Populate metrics dropdown
  const sel=document.getElementById('metric-channel');
  if(sel){
    sel.innerHTML='<option value="">Select channel...</option>';
    (d.channels||[]).forEach(c=>{
      sel.innerHTML+=`<option value="${c.id}">${escHtml(c.name)}</option>`;
    });
  }
  if(!d.channels||!d.channels.length){el.innerHTML='<p style="color:#666">No channels saved yet.</p>';return}
  let html='';
  d.channels.forEach(c=>{
    let kws=(c.keywords||[]).map(k=>`<span class="tag tag-blue">${escHtml(k)}</span>`).join('');
    let subs=(c.subniches||[]).map(s=>`<span class="tag tag-green">${escHtml(s)}</span>`).join('');
    let structs=(c.reference_structures||[]).map(s=>`<span class="tag tag-purple">${escHtml(s)}</span>`).join('');
    let trends=(c.trending_themes||[]).map(t=>`<span class="tag tag-gold">${escHtml(t)}</span>`).join('');
    let metricsCount=(c.metrics||[]).length;
    let cdata=JSON.stringify(c).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
    html+=`<div class="niche-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <h3>${escHtml(c.name)}</h3>
        <div style="display:flex;gap:6px">
          <button class="btn-primary" style="padding:6px 12px;font-size:11px" onclick='analyzeChannel(${cdata})'>🧠 Discover Subniches</button>
          <button class="btn-secondary" style="padding:6px 12px;font-size:11px" onclick="deleteChannel(${c.id})">❌</button>
        </div>
      </div>
      <div style="font-size:12px;color:#888;margin:4px 0">${escHtml(c.url||'')}</div>
      <div style="font-size:12px;color:#aaa;margin:4px 0">🎯 <b>Niche:</b> ${escHtml(c.niche||'')} | <b>Micro:</b> ${escHtml(c.micro_niche||'')}</div>
      ${subs?'<div style="margin:6px 0"><b style="font-size:10px;color:#4ecca3">SUBNICHES</b><div class="tags">'+subs+'</div></div>':''}
      ${kws?'<div style="margin:6px 0"><b style="font-size:10px;color:#3b82f6">KEYWORDS</b><div class="tags">'+kws+'</div></div>':''}
      ${structs?'<div style="margin:6px 0"><b style="font-size:10px;color:#8b5cf6">TITLE STRUCTURES</b><div class="tags">'+structs+'</div></div>':''}
      ${trends?'<div style="margin:6px 0"><b style="font-size:10px;color:#FFD700">TRENDING THEMES</b><div class="tags">'+trends+'</div></div>':''}
      ${metricsCount?`<div style="font-size:11px;color:#4ecca3;margin-top:6px">📊 ${metricsCount} performance entries tracked</div>`:''}
    </div>`;
  });
  el.innerHTML=html;
}

async function addChannel(){
  const data={
    id: 'ch_' + Date.now(),
    name:document.getElementById('ch-name').value.trim(),
    url:document.getElementById('ch-url').value.trim(),
    niche:document.getElementById('ch-niche').value.trim(),
    micro_niche:document.getElementById('ch-micro').value.trim(),
    subniches:document.getElementById('ch-subniches').value.split(',').map(s=>s.trim()).filter(s=>s),
    keywords:document.getElementById('ch-keywords').value.split(',').map(s=>s.trim()).filter(s=>s),
    language:document.getElementById('ch-lang').value,
    titles:document.getElementById('ch-titles').value.split('\n').filter(t=>t.trim()),
    reference_structures:document.getElementById('ch-structures').value.split('\n').filter(t=>t.trim()),
    trending_themes:document.getElementById('ch-trends').value.split('\n').filter(t=>t.trim()),
  };
  if(!data.name){alert('Enter channel name');return}
  const channels = getMyChannels();
  channels.push(data);
  saveMyChannels(channels);
  ['ch-name','ch-url','ch-niche','ch-micro','ch-subniches','ch-keywords'].forEach(id=>document.getElementById(id).value='');
  ['ch-titles','ch-structures','ch-trends'].forEach(id=>document.getElementById(id).value='');
  loadChannels();
}

async function deleteChannel(id){
  if(!confirm('Delete this channel?'))return;
  let channels = getMyChannels();
  channels = channels.filter(c => c.id !== id);
  saveMyChannels(channels);
  loadChannels();
}

async function analyzeChannel(channel){
  loading(true,'Discovering NEW subniches with AI...');
  const r=await post('/api/channels/analyze',{
    channel,
    reference_structures: channel.reference_structures||[],
    trending_themes: channel.trending_themes||[],
  });
  document.getElementById('channel-analysis').innerHTML=`<div class="ai-text">${escHtml(r.analysis||'')}</div>`;
}

async function addMetrics(){
  const chId=document.getElementById('metric-channel').value;
  if(!chId){alert('Select a channel');return}
  const metrics={
    title:document.getElementById('metric-title').value.trim(),
    views:parseInt(document.getElementById('metric-views').value)||0,
    ctr:parseFloat(document.getElementById('metric-ctr').value)||0,
    likes:parseInt(document.getElementById('metric-likes').value)||0,
    comments:parseInt(document.getElementById('metric-comments').value)||0,
    date: new Date().toISOString()
  };
  
  const channels = getMyChannels();
  const c = channels.find(c => c.id === chId);
  if(c) {
    if(!c.metrics) c.metrics = [];
    c.metrics.push(metrics);
    saveMyChannels(channels);
  }
  
  ['metric-title','metric-views','metric-ctr','metric-likes','metric-comments'].forEach(id=>document.getElementById(id).value='');
  loadChannels();
}

// Auto-load channels when page shown
const origShowPage=showPage;
showPage=function(id){origShowPage(id);if(id==='channels')loadChannels();}

// =============================================
// YOUTUBE SCANNER
// =============================================
function showYtTab(id){
  document.querySelectorAll('.yt-panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.yt-tab').forEach(b=>{b.classList.remove('active');b.className=b.className.replace('btn-primary','btn-secondary')});
  document.getElementById(id).classList.add('active');
  document.querySelector(`[data-yttab="${id}"]`).classList.add('active');
  document.querySelector(`[data-yttab="${id}"]`).className=document.querySelector(`[data-yttab="${id}"]`).className.replace('btn-secondary','btn-primary');
}

async function saveYtKey(){
  const key=document.getElementById('yt-api-key').value.trim();
  if(!key){alert('Paste your YouTube API key');return}
  localStorage.setItem('yt_api_key', key);
  const r=await fetch('/api/youtube/save_key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
  const d=await r.json();
  document.getElementById('yt-key-status').textContent=d.status==='ok'?'✅ Saved to Browser & Server!':'❌ Error';
}

function vphBadge(vph,mult){
  let cls='vph-cold',txt=`${vph} VPH`;
  if(mult>=5){cls='vph-fire';txt=`🔥 ${vph} VPH (${mult}x)`}
  else if(mult>=2){cls='vph-hot';txt=`⚡ ${vph} VPH (${mult}x)`}
  else if(mult>=1){cls='vph-warm';txt=`${vph} VPH (${mult}x)`}
  return `<span class="vph-badge ${cls}">${txt}</span>`;
}
function fmtNum(n){if(n>=1e6)return (n/1e6).toFixed(1)+'M';if(n>=1e3)return (n/1e3).toFixed(1)+'K';return n.toString()}

function renderVideoList(videos,title){
  let html=title?`<h3 style="color:var(--accent);margin:16px 0 8px">${title}</h3>`:'';
  videos.forEach(v=>{
    const mult=v.vph_multiplier||1;
    const outlier=v.outlier_score||1;
    let outlierBadge='';
    if(outlier >= 3) {
      outlierBadge = `<span class="vph-badge" style="background:#ff2a2a;color:#fff;margin-left:6px;animation:pulse 2s infinite">🚀 ${outlier}x OUTLIER</span>`;
    } else if (outlier >= 1.5) {
      outlierBadge = `<span class="vph-badge" style="background:var(--accent);color:#fff;margin-left:6px">📈 ${outlier}x OUTLIER</span>`;
    }

    html+=`<div class="yt-video">
      <div style="flex:1">
        <div style="font-size:13px;font-weight:600;margin-bottom:4px"><a href="https://youtube.com/watch?v=${v.id||v.video_id}" target="_blank" style="color:var(--text);text-decoration:none">${escHtml(v.title)}</a></div>
        <div style="font-size:11px;color:var(--dim)">${escHtml(v.channel_title||v.channel_name||'')} · ${v.duration_text||''} · ${v.days_ago||0}d ago</div>
        <div style="margin-top:4px">${vphBadge(v.vph,mult)} ${outlierBadge}</div>
      </div>
      <div class="yt-stat"><div class="num">${fmtNum(v.views)}</div><div class="label">Views</div></div>
      <div class="yt-stat"><div class="num">${fmtNum(v.likes)}</div><div class="label">Likes</div></div>
      <div class="yt-stat"><div class="num">${v.engagement}%</div><div class="label">Engage</div></div>
    </div>`;
  });
  return html;
}

function renderMetrics(m){
  return `<div class="yt-metric-grid">
    <div class="yt-metric-box"><div class="value">${fmtNum(m.avg_vph||0)}</div><div class="label">Avg VPH</div></div>
    <div class="yt-metric-box"><div class="value">${fmtNum(m.max_vph||0)}</div><div class="label">Peak VPH</div></div>
    <div class="yt-metric-box"><div class="value">${fmtNum(m.avg_views||0)}</div><div class="label">Avg Views</div></div>
    <div class="yt-metric-box"><div class="value">${m.avg_engagement||0}%</div><div class="label">Avg Engage</div></div>
    ${m.avg_duration_text?`<div class="yt-metric-box"><div class="value">${m.avg_duration_text}</div><div class="label">Avg Duration</div></div>`:''}
    ${m.total_analyzed?`<div class="yt-metric-box"><div class="value">${m.total_analyzed}</div><div class="label">Videos</div></div>`:''}
  </div>`;
}

function renderWordCloud(words){
  if(!words||!Object.keys(words).length)return '';
  let html='<div style="margin:12px 0"><b style="font-size:11px;color:var(--accent)">TOP TITLE WORDS</b><div class="tags" style="margin-top:6px">';
  const sorted=Object.entries(words).sort((a,b)=>b[1]-a[1]).slice(0,20);
  const max=sorted[0]?sorted[0][1]:1;
  sorted.forEach(([w,c])=>{
    const size=Math.max(11,Math.min(18,11+Math.round((c/max)*7)));
    const op=Math.max(0.5,c/max);
    html+=`<span class="tag tag-blue" style="font-size:${size}px;opacity:${op}">${w} (${c})</span>`;
  });
  return html+'</div></div>';
}

// CHANNEL ANALYZER
async function scanChannel(){
  const ch=document.getElementById('yt-ch-input').value.trim();
  if(!ch){alert('Enter channel handle');return}
  loading(true,'Scanning channel with YouTube API...');
  try{
    const r=await fetch('/api/youtube/channel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:ch,max_videos:30})});
    const d=await r.json();
    if(d.error){document.getElementById('yt-ch-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(d.error)}</div>`;return}
    
    const c=d.channel;
    let html=`<div class="score-card">
      <div style="display:flex;gap:16px;align-items:center;margin-bottom:16px">
        ${c.thumbnail?`<img src="${c.thumbnail}" style="width:80px;height:80px;border-radius:50%;object-fit:cover">`:'' }
        <div><h2 style="color:var(--accent);font-size:20px">${escHtml(c.title)}</h2>
        <div style="font-size:12px;color:var(--dim);margin-top:4px">${fmtNum(c.subscribers)} subs · ${fmtNum(c.total_views)} total views · ${c.video_count} videos · ${c.country||'?'}</div></div>
      </div>
      ${renderMetrics(d.metrics)}
    </div>`;
    html+=renderWordCloud(d.top_words);
    html+=renderVideoList(d.videos,'📊 All Videos (ranked by VPH)');
    document.getElementById('yt-ch-result').innerHTML=html;
  }catch(e){document.getElementById('yt-ch-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`}
  finally{loading(false)}
}

// NICHE DEEP DIVE
async function scanNiche(){
  const q=document.getElementById('yt-niche-q').value.trim();
  if(!q){alert('Enter niche query');return}
  const region=document.getElementById('yt-niche-region').value;
  loading(true,'Deep-diving into niche...');
  try{
    const r=await fetch('/api/youtube/niche',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q,region})});
    const d=await r.json();
    if(d.error){document.getElementById('yt-niche-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(d.error)}</div>`;return}
    
    let html=`<div class="score-card"><h2 style="color:var(--accent);margin-bottom:8px">🎯 Niche: "${escHtml(d.query)}"</h2>
      <div style="font-size:12px;color:var(--dim)">${d.total_videos} videos analyzed</div>
      ${renderMetrics(d.metrics)}
    </div>`;
    
    // Top channels
    if(d.top_channels&&d.top_channels.length){
      html+='<div class="section"><h3>🏆 Top Channels in Niche</h3>';
      d.top_channels.forEach((c,i)=>{
        html+=`<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border)">
          <span style="font-size:13px">#${i+1} ${escHtml(c.name)} (${c.videos} vids)</span>
          <span style="font-size:12px;color:var(--green)">${fmtNum(c.total_views)} views</span>
        </div>`;
      });
      html+='</div>';
    }
    
    html+=renderWordCloud(d.top_words);
    if(d.outliers&&d.outliers.length)html+=renderVideoList(d.outliers,'🔥 OUTLIERS (3x+ avg VPH)');
    html+=renderVideoList(d.top_by_vph||[],'⚡ Top by VPH (last 7 days)');
    document.getElementById('yt-niche-result').innerHTML=html;
  }catch(e){document.getElementById('yt-niche-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`}
  finally{loading(false)}
}

// TRENDING
async function scanTrending(){
  const q=document.getElementById('yt-trend-q').value.trim();
  const region=document.getElementById('yt-trend-region').value;
  const cat=document.getElementById('yt-trend-cat').value;
  loading(true,'Scanning trending videos...');
  try{
    const r=await fetch('/api/youtube/trending',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q,region,category_id:cat})});
    const d=await r.json();
    if(d.error){document.getElementById('yt-trend-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(d.error)}</div>`;return}
    let html=`<div class="score-card"><h2 style="color:var(--accent)">🔥 ${q?'Search: "'+escHtml(q)+'"':'Trending Now'} (${d.region})</h2></div>`;
    html+=renderVideoList(d.videos||[]);
    document.getElementById('yt-trend-result').innerHTML=html;
  }catch(e){document.getElementById('yt-trend-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`}
  finally{loading(false)}
}

// COMPARE
async function compareChannels(){
  const text=document.getElementById('yt-compare-input').value.trim();
  if(!text){alert('Enter channel handles');return}
  const channels=text.split('\n').map(s=>s.trim()).filter(s=>s);
  loading(true,`Comparing ${channels.length} channels...`);
  try{
    const r=await fetch('/api/youtube/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channels})});
    const d=await r.json();
    if(d.error){document.getElementById('yt-compare-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(d.error)}</div>`;return}
    let html='';
    (d.comparisons||[]).forEach(comp=>{
      const c=comp.channel;
      html+=`<div class="score-card">
        <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px">
          ${c.thumbnail?`<img src="${c.thumbnail}" style="width:60px;height:60px;border-radius:50%;object-fit:cover">`:''}
          <div><h3 style="color:var(--accent)">${escHtml(c.title)}</h3>
          <div style="font-size:12px;color:var(--dim)">${fmtNum(c.subscribers)} subs · ${c.video_count} videos</div></div>
        </div>
        <div class="yt-metric-grid">
          <div class="yt-metric-box"><div class="value">${fmtNum(comp.avg_vph)}</div><div class="label">Avg VPH</div></div>
          <div class="yt-metric-box"><div class="value">${fmtNum(comp.max_vph)}</div><div class="label">Peak VPH</div></div>
          <div class="yt-metric-box"><div class="value">${fmtNum(comp.avg_views)}</div><div class="label">Avg Views</div></div>
        </div>
        ${comp.best_video?`<div style="margin-top:8px;font-size:12px"><b style="color:var(--green)">Best:</b> ${escHtml(comp.best_video.title)} (${fmtNum(comp.best_video.vph)} VPH)</div>`:''}
        ${comp.worst_video?`<div style="font-size:12px;margin-top:4px"><b style="color:var(--red)">Worst:</b> ${escHtml(comp.worst_video.title)} (${fmtNum(comp.worst_video.vph)} VPH)</div>`:''}
      </div>`;
    });
    document.getElementById('yt-compare-result').innerHTML=html;
  }catch(e){document.getElementById('yt-compare-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`}
  finally{loading(false)}
}

// SAVED EMERGING CHANNELS
async function loadSavedChannels(){
  loading(true, 'Loading tracked channels...');
  try {
    const saved = JSON.parse(localStorage.getItem('saved_channels') || '[]');
    let html = '';
    if(!saved.length) {
      document.getElementById('yt-saved-list').innerHTML='<p style="color:var(--dim)">No channels saved yet. Validate subniches to find and save them.</p>';
      return;
    }
    
    saved.forEach(ch => {
      html += `<div class="score-card" style="margin-bottom:12px;display:flex;align-items:center;gap:16px">
        ${ch.thumbnail?`<img src="${ch.thumbnail}" style="width:50px;height:50px;border-radius:50%">`:''}
        <div style="flex:1">
          <h3 style="color:var(--accent);margin-bottom:4px">${escHtml(ch.name)}</h3>
          <div style="font-size:12px;color:var(--dim)">
            Subniche: <span style="color:var(--purple);font-weight:600">${escHtml(ch.subniche||'Unknown')}</span>
          </div>
        </div>
        <div style="text-align:right">
          <div style="font-size:18px;font-weight:800;color:var(--green)">${fmtNum(ch.avg_vph)} <span style="font-size:10px;font-weight:normal;color:var(--dim)">VPH</span></div>
          <div style="font-size:12px;color:var(--dim)">${fmtNum(ch.subscribers)} subs</div>
        </div>
        <div>
          <button class="btn-primary" style="padding:8px 12px;font-size:12px" onclick="document.getElementById('yt-ch-input').value='${ch.id}';showYtTab('yt-channel');scanChannel();">📊 Analyze Deep</button>
        </div>
      </div>`;
    });
    document.getElementById('yt-saved-list').innerHTML=html;
  } catch(e) {
    document.getElementById('yt-saved-list').innerHTML=`<div style="color:var(--red)">Error: ${e.message}</div>`;
  } finally {
    loading(false);
  }
}

// NEWBORN VIRALS (SMALL CHANNELS VIRALIZING)
async function scanNewbornVirals(){
  const q=document.getElementById('yt-newborn-q').value.trim();
  if(!q){alert('Enter a niche or theme');return}
  loading(true,'Scanning for NEWBORN VIRALS (channels < 40 days or few videos)...');
  try{
    const r=await fetch('/api/youtube/newborn_virals',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q})});
    const d=await r.json();
    if(d.error){document.getElementById('yt-newborn-result').innerHTML=`<div class="score-card" style="color:var(--red)">${escHtml(d.error)}</div>`;return}
    
    let html=`<div class="score-card"><h2 style="color:var(--accent)">🚀 Newborn Virals: "${escHtml(q)}"</h2>
      <div style="font-size:12px;color:var(--dim)">Showing channels with ≤35 videos but MASSIVE VPH.</div>
    </div>`;
    
    if(!d.virals||!d.virals.length){
      html+=`<div class="score-card" style="text-align:center;color:var(--dim)">No newborn virals found right now. Try a broader search.</div>`;
    }else{
      d.virals.forEach(v=>{
        const ch=v.channel_stats||{};
        html+=`<div class="score-card" style="margin-bottom:16px">
          <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;padding-bottom:12px;border-bottom:1px solid var(--border)">
            ${ch.thumbnail?`<img src="${ch.thumbnail}" style="width:48px;height:48px;border-radius:50%">`:''}
            <div style="flex:1">
              <div style="font-weight:700;font-size:15px">
                <a href="https://youtube.com/channel/${v.channel_id}" target="_blank" style="color:var(--text);text-decoration:none">${escHtml(v.channel_name)} 🔗</a>
              </div>
              <div style="font-size:12px;color:var(--dim)">
                <span style="color:var(--purple);font-weight:700">${ch.video_count} videos</span> · 
                ${fmtNum(ch.subscribers)} subs · Created: ${ch.published_at}
              </div>
            </div>
            <button class="btn-primary" style="padding:6px 12px;font-size:12px" onclick="remixStrategyFromViral('${escHtml(v.title).replace(/'/g,"\\'")}','${escHtml(q).replace(/'/g,"\\'")}')">🛠️ Montar Estratégia A/B</button>
          </div>
          
          <div style="display:flex;gap:12px;align-items:center">
            ${v.thumbnail?`<a href="https://youtube.com/watch?v=${v.video_id}" target="_blank"><img src="${v.thumbnail}" style="width:120px;border-radius:8px"></a>`:''}
            <div style="flex:1">
              <a href="https://youtube.com/watch?v=${v.video_id}" target="_blank" style="color:var(--text);text-decoration:none;font-weight:600;font-size:14px">${escHtml(v.title)}</a>
              <div style="margin-top:6px;display:flex;gap:16px">
                <div><span style="font-size:16px;font-weight:800;color:var(--green)">${fmtNum(v.vph)}</span> <span style="font-size:10px;color:var(--dim)">VPH</span></div>
                <div><span style="font-size:16px;font-weight:800;color:var(--accent)">${fmtNum(v.views)}</span> <span style="font-size:10px;color:var(--dim)">VIEWS</span></div>
                <div style="font-size:11px;color:var(--dim);align-self:center">${v.published}</div>
              </div>
            </div>
          </div>
        </div>`;
      });
    }
    document.getElementById('yt-newborn-result').innerHTML=html;
  }catch(e){document.getElementById('yt-newborn-result').innerHTML=`<div class="score-card" style="color:var(--red)">Error: ${e.message}</div>`}
  finally{loading(false)}
}

// 1-CLICK STRATEGY REMIXER
async function remixStrategyFromViral(title, niche){
  loading(true, 'Extracting DNA & Building Strategy Remix...');
  try{
    const r=await fetch('/api/strategy_from_viral',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title,niche,language:document.getElementById('sub-lang').value})});
    const d=await r.json();
    
    // Switch to strategy tab and show result
    showPage('strategy');
    document.getElementById('strat-type').value=niche;
    document.getElementById('strat-result').innerHTML=`
      <div class="results-header">
        <h3>🔥 Strategy Extracted from Viral Video</h3>
        <div class="results-count">Base: "${escHtml(title)}"</div>
      </div>
      <div class="ai-text">${formatAiText(d.strategy)}</div>
    `;
  }catch(e){
    alert('Erro ao gerar estratégia: '+e.message);
  }finally{
    loading(false);
  }
}

// Enter key
document.getElementById('analyze-input')?.addEventListener('keydown',e=>{if(e.key==='Enter')analyzeTitle()});
document.getElementById('gen-topic')?.addEventListener('keydown',e=>{if(e.key==='Enter')generateTitles()});
document.getElementById('yt-ch-input')?.addEventListener('keydown',e=>{if(e.key==='Enter')scanChannel()});
document.getElementById('yt-niche-q')?.addEventListener('keydown',e=>{if(e.key==='Enter')scanNiche()});

// AI ENGINE STATUS MONITOR
async function checkAiStatus(){
  try{
    const r=await fetch('/api/ai/status');
    const d=await r.json();
    const badge=document.getElementById('ai-badge');
    const avail=(d.providers||[]).filter(p=>p.available).length;
    const total=(d.providers||[]).length;
    
    // Update badge
    const healthColors={healthy:'#10b981',degraded:'#f59e0b',down:'#ef4444'};
    badge.style.borderColor=healthColors[d.health]||'#888';
    badge.textContent=`🤖 AI ${avail}/${total} Online`;
    badge.title=`Health: ${(d.health||'').toUpperCase()}\nGemini: ${d.keys?.gemini?'✓':'✗'}\nGroq: ${d.keys?.groq?'✓':'✗'}\nCache: ${d.cache_size} entries\n\nClick for details`;
    
    // Show details in alert if clicked
    let details=`AI Engine v3 — ${d.health?.toUpperCase()}\n${'='.repeat(35)}\n`;
    (d.providers||[]).forEach(p=>{
      const status=p.available?'✓ ONLINE':`✗ cooldown ${Math.round(p.cooldown_remaining)}s`;
      details+=`${p.name}: ${status} (${p.total_calls} calls)\n`;
    });
    details+=`\nKeys: Gemini=${d.keys?.gemini?'✓':'✗'} Groq=${d.keys?.groq?'✓':'✗'}`;
    details+=`\nCache: ${d.cache_size} entries`;
    details+=`\nTotal calls: ${d.stats?.total_calls||0}`;
    alert(details);
  }catch(e){
    alert('AI Engine status unavailable: '+e.message);
  }
}

// Auto-check AI status on load
(async function(){
  try{
    const r=await fetch('/api/ai/status');
    const d=await r.json();
    const badge=document.getElementById('ai-badge');
    if(badge){
      const avail=(d.providers||[]).filter(p=>p.available).length;
      const total=(d.providers||[]).length;
      const healthColors={healthy:'#10b981',degraded:'#f59e0b',down:'#ef4444'};
      badge.style.borderColor=healthColors[d.health]||'#888';
      badge.textContent=`🤖 AI ${avail}/${total} Online`;
    }
  }catch(e){}
})();

// AI MONITOR PAGE
async function loadAiMonitor(){
  try{
    const r=await fetch('/api/ai/status');
    const d=await r.json();
    const avail=(d.providers||[]).filter(p=>p.available).length;
    const total=(d.providers||[]).length;
    const hColors={healthy:'#10b981',degraded:'#f59e0b',down:'#ef4444'};
    
    document.getElementById('mon-health').textContent=d.health?.toUpperCase()||'UNKNOWN';
    document.getElementById('mon-health').style.color=hColors[d.health]||'#888';
    document.getElementById('mon-available').textContent=`${avail}/${total}`;
    document.getElementById('mon-available').style.color=avail>=3?'#10b981':avail>=1?'#f59e0b':'#ef4444';
    document.getElementById('mon-calls').textContent=d.stats?.total_calls||0;
    document.getElementById('mon-cache').textContent=d.stats?.cache_hits||0;
    
    let html='';
    (d.providers||[]).forEach(p=>{
      const online=p.available;
      const cd=Math.round(p.cooldown_remaining||0);
      const color=online?'#10b981':'#ef4444';
      const icon=online?'🟢':'🔴';
      const typeColor=p.type==='gemini'?'#3b82f6':'#f97316';
      html+=`<div style="display:flex;align-items:center;gap:12px;padding:10px 14px;background:#0d1117;border-radius:8px;border:1px solid ${online?'#1e3a2f':'#3a1e1e'}">
        <span style="font-size:18px">${icon}</span>
        <div style="flex:1">
          <div style="font-weight:600;font-size:13px">${p.name}</div>
          <div style="font-size:11px;color:#666">${p.model} <span style="color:${typeColor};font-weight:600">[${p.type?.toUpperCase()}]</span></div>
        </div>
        <div style="text-align:right">
          <div style="font-size:12px;color:${color};font-weight:600">${online?'ONLINE':cd>0?'Cooldown '+cd+'s':'OFFLINE'}</div>
          <div style="font-size:11px;color:#555">${p.total_calls||0} calls / ${p.total_errors||0} errors</div>
        </div>
      </div>`;
    });
    document.getElementById('mon-providers').innerHTML=html;
    
    let log=`<div style="color:#555">Keys: Gemini=${d.keys?.gemini?'✓':'✗'} Groq=${d.keys?.groq?'✓':'✗'} | Cache: ${d.cache_size} entries | Updated: ${new Date().toLocaleTimeString()}</div>`;
    document.getElementById('mon-log').innerHTML=log;
  }catch(e){
    document.getElementById('mon-providers').innerHTML=`<div style="color:#ef4444">Error loading AI status: ${e.message}</div>`;
  }
}

async function resetAiEngine(){
  if(!confirm('Reset all AI provider cooldowns and cache?')) return;
  try{
    await fetch('/api/ai/reset',{method:'POST'});
    loadAiMonitor();
    alert('All cooldowns and cache cleared!');
  }catch(e){alert('Reset failed: '+e.message)}
}

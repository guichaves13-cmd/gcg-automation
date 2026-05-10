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
async function post(url,data){
  loading(true,'Analyzing with Gemini AI...');
  try{
    const r=await fetch(API+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    return await r.json();
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
  let structs=r.structures.map(s=>`<span class="tag tag-green">${s.name} +${Math.round((s.ctr_boost-1)*100)}%</span>`).join('');
  let emots=r.emotional_words.map(w=>`<span class="tag tag-purple">${w}</span>`).join('');
  let powers=r.power_words.map(w=>`<span class="tag tag-gold">${w}</span>`).join('');
  let issues=r.issues.map(i=>`<span class="tag tag-red">${i}</span>`).join('');
  let suggs=r.suggestions.map(s=>`<li>💡 ${escHtml(s)}</li>`).join('');
  return `<div class="score-card">
    <div class="score-header">
      <div class="score-circle ${gradeClass(r.grade)}">${r.grade}<div style="font-size:10px;font-weight:500">${r.score}/100</div></div>
      <div><div class="score-title">${escHtml(r.title)}</div>
      <div class="score-meta">${r.length} chars · ${r.words} words</div></div>
    </div>
    <div class="score-bar"><div class="score-fill" style="width:${r.score}%;background:${barColor(r.score)}"></div></div>
    ${structs?'<div style="margin:8px 0"><b style="font-size:11px;color:#4ecca3">VIRAL STRUCTURES</b><div class="tags">'+structs+'</div></div>':''}
    ${emots?'<div style="margin:8px 0"><b style="font-size:11px;color:#8b5cf6">EMOTIONAL TRIGGERS</b><div class="tags">'+emots+'</div></div>':''}
    ${powers?'<div style="margin:8px 0"><b style="font-size:11px;color:#FFD700">POWER WORDS</b><div class="tags">'+powers+'</div></div>':''}
    ${issues?'<div style="margin:8px 0"><b style="font-size:11px;color:#e94560">ISSUES</b><div class="tags">'+issues+'</div></div>':''}
    ${suggs?'<div class="section"><h3>💡 Suggestions</h3><ul>'+suggs+'</ul></div>':''}
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
  let html='<h3 style="color:#FFD700;margin-bottom:12px">Generated for: '+escHtml(r.topic)+'</h3>';
  (r.titles||[]).forEach((t,i)=>{
    html+=`<div class="title-item">
      <div class="title-score ${gradeClass(t.grade)}" style="font-size:12px">${t.grade}<br>${t.score}</div>
      <div class="title-text">${escHtml(t.title)}</div>
      <div class="title-len">${t.length}c</div>
    </div>`;
  });
  document.getElementById('gen-result').innerHTML=html;
}

// SUBNICHE
async function findSubniches(){
  const theme=document.getElementById('sub-theme').value.trim();
  const lang=document.getElementById('sub-lang').value;
  loading(true,'Scanning YouTube for trending videos...');
  const r=await post('/api/subniche',{theme,language:lang});
  let html='';
  
  // 1. REAL YouTube trending videos
  if(r.videos&&r.videos.length){
    html+=`<div class="score-card" style="margin-bottom:16px">
      <h3 style="color:#FFD700;margin-bottom:4px">&#128293; Trending on YouTube: "${escHtml(theme)}"</h3>
      <div style="font-size:11px;color:#888;margin-bottom:12px">${r.videos.length} videos found (last 14 days, sorted by VPH)</div>`;
    r.videos.forEach((v,i)=>{
      const vphColor=v.vph>=500?'#FFD700':v.vph>=200?'#4ecca3':v.vph>=100?'#f59e0b':'#aaa';
      const subsText=v.subs?fmtNum(v.subs)+' subs':'';
      const ytLink=v.video_id?`https://youtube.com/watch?v=${v.video_id}`:'#';
      const chName=encodeURIComponent(v.channel||'');
      html+=`<div style="display:flex;align-items:center;padding:10px 0;border-bottom:1px solid #1a1a2e;gap:12px">
        <div style="width:28px;text-align:center;font-size:13px;font-weight:700;color:#555">${i+1}</div>
        <div style="flex:1">
          <a href="${ytLink}" target="_blank" style="font-size:13px;font-weight:600;color:#eee;text-decoration:none">${escHtml(v.title)}</a>
          <div style="font-size:11px;color:#888;margin-top:3px">&#128250; ${escHtml(v.channel)} ${subsText?' &middot; '+subsText:''} &middot; ${v.duration_text||''} &middot; ${v.days_ago}d ago</div>
        </div>
        <div style="text-align:right;min-width:80px">
          <div style="font-size:15px;font-weight:700;color:${vphColor}">&#9889; ${v.vph}</div>
          <div style="font-size:10px;color:#666">VPH</div>
        </div>
        <div style="text-align:right;min-width:70px">
          <div style="font-size:13px;font-weight:600;color:#aaa">${fmtNum(v.views)}</div>
          <div style="font-size:10px;color:#666">views</div>
        </div>
        <div style="display:flex;gap:4px">
          <button class="btn-primary" style="padding:4px 8px;font-size:10px" onclick="goToStrategy('${encodeURIComponent(v.title)}')">&#128640; Strategy</button>
          <button class="btn-secondary" style="padding:4px 8px;font-size:10px" onclick="saveRisingChannel('${chName}','${v.subs}','${encodeURIComponent(v.title)}')">&#128190; Save</button>
        </div>
      </div>`;
    });
    html+=`</div>`;
  } else {
    html+=`<div class="score-card" style="color:#f59e0b">&#9888; YouTube API not active or no results. Add YouTube API key in YouTube Scanner tab.</div>`;
  }
  
  // 2. AI Subniches
  if(r.niches&&r.niches.length){
    html+=`<h3 style="color:#4ecca3;margin:20px 0 12px">&#127793; New Subniche Opportunities (AI-powered)</h3>`;
    r.niches.forEach((n,i)=>{
      const demColor=n.demand>=7?'#4ecca3':n.demand>=4?'#f59e0b':'#e94560';
      const supColor=n.supply<=3?'#4ecca3':n.supply<=6?'#f59e0b':'#e94560';
      const oppColor=n.opportunity>=6?'#4ecca3':n.opportunity>=3?'#f59e0b':'#e94560';
      let titles=(n.example_titles||[]).map(t=>`<div class="title-item" style="margin:4px 0"><div class="title-text">${escHtml(t)}</div><div class="title-len">${t.length}c</div></div>`).join('');
      let kws=(n.keywords||[]).map(k=>`<span class="tag tag-blue">${k}</span>`).join('');
      let structs=(n.title_structures||[]).map(s=>`<span class="tag tag-purple">${escHtml(s)}</span>`).join('');
      html+=`<div class="niche-card">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <h3>#${i+1} ${escHtml(n.name)}</h3>
          <button class="btn-primary" style="padding:6px 14px;font-size:11px" onclick="goToStrategy('${encodeURIComponent(n.name)}')">&#128640; Create Strategy</button>
        </div>
        <div class="niche-meter">
          <div class="meter"><div class="meter-label">DEMAND</div><div class="meter-bar"><div class="meter-fill" style="width:${n.demand*10}%;background:${demColor}"></div></div><div style="font-size:11px;color:${demColor};margin-top:2px">${n.demand}/10</div></div>
          <div class="meter"><div class="meter-label">SUPPLY</div><div class="meter-bar"><div class="meter-fill" style="width:${n.supply*10}%;background:${supColor}"></div></div><div style="font-size:11px;color:${supColor};margin-top:2px">${n.supply}/10</div></div>
          <div class="meter"><div class="meter-label">OPPORTUNITY</div><div class="meter-bar"><div class="meter-fill" style="width:${n.opportunity*10}%;background:${oppColor}"></div></div><div style="font-size:11px;color:${oppColor};margin-top:2px;font-weight:700">${n.opportunity}/10</div></div>
        </div>
        ${n.why_now?`<div style="font-size:12px;color:#FFD700;margin:8px 0">&#128293; <b>Why now:</b> ${escHtml(n.why_now)}</div>`:''}
        ${n.reference_channel?`<div style="font-size:12px;color:#aaa;margin:4px 0">&#128250; <b>Reference:</b> ${escHtml(n.reference_channel)}</div>`:''}
        <div style="font-size:12px;color:#aaa;margin:4px 0">&#127919; <b>Angle:</b> ${escHtml(n.content_angle||'')}</div>
        ${structs?'<div style="margin:8px 0"><b style="font-size:10px;color:#8b5cf6">TITLE FORMULAS</b><div class="tags">'+structs+'</div></div>':''}
        <div class="tags" style="margin:8px 0">${kws}</div>
        <div style="margin-top:8px"><b style="font-size:11px;color:#4ecca3">EXAMPLE TITLES</b>${titles}</div>
      </div>`;
    });
  }
  document.getElementById('sub-result').innerHTML=html;
}

function saveRisingChannel(name,subs,titleRef){
  const chName=decodeURIComponent(name);
  const title=decodeURIComponent(titleRef);
  // Save to My Channels with "Rising" tag
  const data={
    name:chName,
    url:'',
    niche:document.getElementById('sub-theme').value||'',
    micro_niche:'Rising Channel',
    subniches:['Rising','Trending'],
    keywords:[],
    language:'English',
    titles:[title],
    reference_structures:[],
    trending_themes:[document.getElementById('sub-theme').value||''],
  };
  fetch('/api/channels/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
    .then(()=>alert('Channel "'+chName+'" saved to My Channels!'))
    .catch(e=>alert('Error: '+e.message));
}

function goToStrategy(nicheName){
  showPage('strategy');
  document.getElementById('strat-type').value=decodeURIComponent(nicheName);
  document.getElementById('strat-type').focus();
}

// TRENDS
async function scanTrends(){
  const cat=document.getElementById('trend-cat').value;
  const lang=document.getElementById('trend-lang').value;
  loading(true,'Scanning YouTube trends...');
  const r=await post('/api/trend_scanner',{category:cat,language:lang});
  document.getElementById('trend-result').innerHTML=`<div class="ai-text">${escHtml(r.trends||'')}</div>`;
}

// STRATEGY
async function getStrategy(){
  const type=document.getElementById('strat-type').value.trim();
  const audience=document.getElementById('strat-audience').value.trim();
  const lang=document.getElementById('strat-lang').value;
  const titles=document.getElementById('strat-titles').value.trim().split('\n').filter(t=>t.trim());
  if(!type){alert('Enter channel type/niche');return}
  loading(true,'Scanning YouTube + Building data-driven strategy...');
  const r=await post('/api/channel_strategy',{channel_type:type,target_audience:audience,language:lang,titles});
  let html='';
  
  // Show viral videos used for strategy
  if(r.viral_videos&&r.viral_videos.length){
    html+=`<div class="score-card" style="margin-bottom:16px">
      <h3 style="color:#FFD700;margin-bottom:8px">&#128293; YouTube Data Used for Strategy</h3>
      <div style="font-size:11px;color:#888;margin-bottom:8px">Real trending videos in "${escHtml(r.channel_type)}" (last 14 days)</div>`;
    r.viral_videos.forEach((v,i)=>{
      const vphColor=v.vph>=500?'#FFD700':v.vph>=200?'#4ecca3':v.vph>=100?'#f59e0b':'#aaa';
      html+=`<div style="display:flex;align-items:center;padding:6px 0;border-bottom:1px solid #1a1a2e;gap:8px">
        <div style="width:20px;color:#555;font-size:11px">${i+1}</div>
        <div style="flex:1;font-size:12px;color:#ddd">${escHtml(v.title)}</div>
        <div style="font-size:11px;color:#888">${escHtml(v.channel)}</div>
        <div style="font-size:13px;font-weight:700;color:${vphColor};min-width:60px;text-align:right">&#9889;${v.vph}</div>
        <div style="font-size:11px;color:#888;min-width:50px;text-align:right">${fmtNum(v.views)}</div>
      </div>`;
    });
    html+=`</div>`;
  }
  
  html+=`<div class="ai-text">${escHtml(r.strategy||'')}</div>`;
  document.getElementById('strat-result').innerHTML=html;
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
  await post('/api/channels/add',data);
  ['ch-name','ch-url','ch-niche','ch-micro','ch-subniches','ch-keywords'].forEach(id=>document.getElementById(id).value='');
  ['ch-titles','ch-structures','ch-trends'].forEach(id=>document.getElementById(id).value='');
  loadChannels();
}

async function deleteChannel(id){
  if(!confirm('Delete this channel?'))return;
  await post('/api/channels/delete',{id});
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
  const chId=parseInt(document.getElementById('metric-channel').value);
  if(!chId){alert('Select a channel');return}
  const metrics={
    title:document.getElementById('metric-title').value.trim(),
    views:parseInt(document.getElementById('metric-views').value)||0,
    ctr:parseFloat(document.getElementById('metric-ctr').value)||0,
    likes:parseInt(document.getElementById('metric-likes').value)||0,
    comments:parseInt(document.getElementById('metric-comments').value)||0,
  };
  await post('/api/channels/update_metrics',{id:chId,metrics});
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
  const r=await fetch('/api/youtube/save_key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})});
  const d=await r.json();
  document.getElementById('yt-key-status').textContent=d.status==='ok'?'✅ Saved!':'❌ Error';
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
    html+=`<div class="yt-video">
      <div style="flex:1">
        <div style="font-size:13px;font-weight:600;margin-bottom:4px">${escHtml(v.title)}</div>
        <div style="font-size:11px;color:var(--dim)">${escHtml(v.channel_title||'')} · ${v.duration_text} · ${v.days_ago}d ago</div>
        <div style="margin-top:4px">${vphBadge(v.vph,mult)}</div>
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

// Enter key
document.getElementById('analyze-input')?.addEventListener('keydown',e=>{if(e.key==='Enter')analyzeTitle()});
document.getElementById('gen-topic')?.addEventListener('keydown',e=>{if(e.key==='Enter')generateTitles()});
document.getElementById('yt-ch-input')?.addEventListener('keydown',e=>{if(e.key==='Enter')scanChannel()});
document.getElementById('yt-niche-q')?.addEventListener('keydown',e=>{if(e.key==='Enter')scanNiche()});

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
    const text = await r.text();
    try {
      const json = JSON.parse(text);
      if(!r.ok && !json.error) json.error = `HTTP Error ${r.status}`;
      if(json.error) {
         alert("TitlePilot AI Error:\n" + json.error);
      }
      return json;
    } catch(e) {
      alert(`Invalid JSON Response (${r.status}): ${text.substring(0,100)}...`);
      return {error: `Invalid JSON Response (${r.status}): ${text.substring(0,100)}...`};
    }
  }catch(err){
    alert(`Network Request Failed: ${err.message}`);
    return {error: `Network Request Failed: ${err.message}`};
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
  if(r.error){
    document.getElementById('analyze-result').innerHTML=`<div class="score-card" style="color:#e94560">${escHtml(r.error)}</div>`;
    return;
  }
  document.getElementById('analyze-result').innerHTML=renderScoreCard(r);
}
async function deepAnalyze(){
  const title=document.getElementById('analyze-input').value.trim();
  if(!title)return;
  loading(true,'Running AI deep analysis...');
  const r=await post('/api/deep_analysis',{title});
  if(r.error){
    document.getElementById('analyze-result').innerHTML=`<div class="score-card" style="color:#e94560">${escHtml(r.error)}</div>`;
    return;
  }
  let html=renderScoreCard(r);
  
  if(r.ai_deep_analysis){
    const d = r.ai_deep_analysis;
    if(d.error) {
       html+=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p>${escHtml(d.error)}</p></div>`;
    } else {
       html += `<div class="score-card" style="margin-top:16px;border-left:4px solid #8b5cf6">
         <h3 style="color:#8b5cf6;margin-bottom:8px">🧠 Deep AI Analysis</h3>
         <div style="font-size:14px;color:#ddd;line-height:1.5;margin-bottom:12px"><b>Verdict:</b> ${escHtml(d.verdict)}</div>
         
         <div style="display:flex;gap:12px;margin-bottom:12px">
           <div style="flex:1;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
             <div style="font-size:11px;color:#aaa">Primary Emotion</div>
             <div style="font-size:15px;font-weight:bold;color:#f59e0b">${escHtml(d.emotional_mapping?.primary_emotion || '')} <span style="font-size:11px;color:#fff">(Level ${d.emotional_mapping?.intensity||0})</span></div>
           </div>
           <div style="flex:1;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
             <div style="font-size:11px;color:#aaa">Predicted Dropoff (0:30)</div>
             <div style="font-size:15px;font-weight:bold;color:#e94560">${escHtml(d.retention_prediction?.hook_dropoff || '')}</div>
           </div>
         </div>
         
         <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;margin-bottom:12px">
           <div style="font-size:12px;color:#aaa;margin-bottom:4px"><b>Retention Risk:</b> ${escHtml(d.retention_prediction?.retention_risk || '')}</div>
         </div>
         
         <div class="niche-card" style="border-left:3px solid #4ecca3;padding:12px;margin-bottom:12px">
           <div style="font-size:13px;color:#4ecca3;font-weight:bold;margin-bottom:4px">🖼️ Thumbnail Concept</div>
           <div style="font-size:12px;color:#ddd;margin-bottom:4px"><b>Visual:</b> ${escHtml(d.thumbnail_concept?.visual || '')}</div>
           <div style="font-size:12px;color:#ddd"><b>Text:</b> <span class="tag tag-gold">${escHtml(d.thumbnail_concept?.text || '')}</span></div>
         </div>
         
         <div style="font-size:13px;color:#fff;font-weight:bold;margin-bottom:8px">🔥 Improved Versions:</div>
         <ul style="font-size:13px;color:#aaa;padding-left:20px;margin:0">
           ${(d.improved_versions||[]).map(v => `<li>"${escHtml(v)}"</li>`).join('')}
         </ul>
       </div>`;
    }
  }
  document.getElementById('analyze-result').innerHTML=html;
}

// HOOK BLUEPRINT
async function generateHookBlueprint() {
  const title=document.getElementById('analyze-input').value.trim();
  if(!title)return;
  loading(true,'Architecting 60-second Hook Blueprint...');
  const r=await post('/api/hook_blueprint',{title});
  
  if(r.error) {
    document.getElementById('analyze-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p>${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.blueprint_data || {};
  let html = `<div class="score-card" style="margin-top:16px;border-left:4px solid #f59e0b">
    <h3 style="color:#f59e0b;margin-bottom:8px">🎬 60-Second Hook Blueprint</h3>
    <div style="font-size:13px;color:#ddd;line-height:1.5;margin-bottom:16px;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
      <b>🧠 Hook Analysis:</b> ${escHtml(d.hook_analysis)}
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">`;
    
  (d.script_blocks||[]).forEach(b => {
    html += `<div style="background:#1f2937;padding:12px;border-radius:8px;border-left:2px solid #f59e0b">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:12px;font-weight:bold;color:#f59e0b">⏱️ ${escHtml(b.timestamp)}</span>
        <span class="tag tag-gold">${escHtml(b.retention_tactic)}</span>
      </div>
      <div style="font-size:12px;color:#aaa;margin-bottom:4px"><b>🎥 Visual:</b> ${escHtml(b.visual)}</div>
      <div style="font-size:13px;color:#fff;font-style:italic"><b>🔊 Audio:</b> "${escHtml(b.audio)}"</div>
    </div>`;
  });
  
  html += `</div></div>`;
  document.getElementById('analyze-result').innerHTML=html;
}

// AB SIMULATOR
async function simulateABTest() {
  const title_a = document.getElementById('ab-title-a').value.trim();
  const title_b = document.getElementById('ab-title-b').value.trim();
  const niche = document.getElementById('ab-niche').value.trim();
  const lang = document.getElementById('ab-lang').value;
  
  if(!title_a || !title_b) {
    alert("Please provide both Title A and Title B");
    return;
  }
  if(!niche) {
    alert("Please provide the main niche context");
    return;
  }
  
  loading(true, 'Simulating A/B Test and Generating Thumbnail Concept...');
  const r = await post('/api/ab_simulate', { title_a, title_b, niche, language: lang });
  
  if(r.error) {
    document.getElementById('ab-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.ab_data || {};
  const winColor = d.winner === 'A' ? '#f59e0b' : '#4ecca3';
  
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid ${winColor}">
    <h2 style="color:${winColor};margin-bottom:8px">🏆 WINNER: Title ${escHtml(d.winner)}</h2>
    <div style="display:flex;gap:12px;margin-bottom:12px">
      <div style="flex:1;background:#0d1117;padding:12px;border-radius:8px;border:1px solid ${d.winner === 'A' ? winColor : '#30363d'}">
        <div style="font-size:11px;color:#aaa">Title A Score</div>
        <div style="font-size:24px;font-weight:bold;color:${d.winner === 'A' ? winColor : '#aaa'}">${d.winner === 'A' ? d.winner_score : d.loser_score}%</div>
      </div>
      <div style="flex:1;background:#0d1117;padding:12px;border-radius:8px;border:1px solid ${d.winner === 'B' ? winColor : '#30363d'}">
        <div style="font-size:11px;color:#aaa">Title B Score</div>
        <div style="font-size:24px;font-weight:bold;color:${d.winner === 'B' ? winColor : '#aaa'}">${d.winner === 'B' ? d.winner_score : d.loser_score}%</div>
      </div>
    </div>
    <div style="font-size:14px;color:#ddd;line-height:1.5;margin-bottom:12px"><b>Why it wins:</b> ${escHtml(d.analysis)}</div>
  </div>`;
  
  if(d.thumbnail_concept) {
    html += `<div class="niche-card" style="border-left:3px solid #e94560">
      <h3 style="color:#e94560;margin-bottom:12px">🖼️ Viral Thumbnail Concept</h3>
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
        <div style="font-size:13px;color:#aaa;margin-bottom:4px"><b>Visual:</b> ${escHtml(d.thumbnail_concept.visual)}</div>
        <div style="font-size:13px;color:#aaa;margin-bottom:4px"><b>Overlay Text:</b> <span class="tag tag-gold">${escHtml(d.thumbnail_concept.text)}</span></div>
        <div style="font-size:13px;color:#aaa"><b>Emotion:</b> ${escHtml(d.thumbnail_concept.emotion)}</div>
      </div>
    </div>`;
  }
  
  if(d.video_hook) {
    html += `<div class="niche-card" style="border-left:3px solid #8b5cf6">
      <h3 style="color:#8b5cf6;margin-bottom:12px">🪝 Perfect Video Hook (First 15s)</h3>
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;font-style:italic;color:#ddd;line-height:1.5">
        "${escHtml(d.video_hook)}"
      </div>
    </div>`;
  }
  
  document.getElementById('ab-result').innerHTML = html;
}

// VPH RADAR
async function runVphRadar() {
  const niche = document.getElementById('vph-niche').value.trim();
  const lang = document.getElementById('vph-lang').value;
  if(!niche) {
    alert("Please enter a niche to scan.");
    return;
  }
  
  loading(true, 'Fetching top VPH videos from the last 14 days and running AI analysis...');
  const r = await post('/api/vph_radar', { niche, language: lang });
  
  if(r.error) {
    document.getElementById('vph-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.radar_data || {};
  let html = '';
  
  // RAW DATA / VIDEOS
  if(d.top_videos && d.top_videos.length) {
    html += `<div class="niche-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560;margin-bottom:12px">📈 Top VPH Videos (Last 14 Days)</h3>`;
    html += `<div style="max-height:200px;overflow-y:auto;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">`;
    d.top_videos.forEach(v => {
      html += `<div style="margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #1f2937">
        <a href="https://youtube.com/watch?v=${v.id}" target="_blank" style="color:#fff;text-decoration:none;font-size:13px;font-weight:bold">▶️ ${escHtml(v.title)}</a>
        <div style="font-size:11px;color:#aaa;margin-top:4px">
          <span style="color:#e94560"><b>${Math.round(v.vph)} VPH</b></span> • ${v.views.toLocaleString()} views • 📺 ${escHtml(v.channel_title||'')}
        </div>
      </div>`;
    });
    html += `</div></div>`;
  }
  
  // VIRAL THEMES
  if(d.viral_themes && d.viral_themes.length) {
    html += `<div class="niche-card" style="border-left:3px solid #f59e0b"><h3 style="color:#f59e0b;margin-bottom:12px">🔥 Core Viral Themes</h3>`;
    d.viral_themes.forEach(t => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="font-size:14px;font-weight:bold;color:#fff">${escHtml(t.theme)}</div>
        <div style="font-size:12px;color:#aaa;margin-top:4px"><i>Why it's hot:</i> ${escHtml(t.why_its_hot)}</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  // VIRAL STRUCTURES
  if(d.viral_structures && d.viral_structures.length) {
    html += `<div class="niche-card" style="border-left:3px solid #4ecca3"><h3 style="color:#4ecca3;margin-bottom:12px">📐 Validated Title Structures</h3>`;
    d.viral_structures.forEach(s => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="font-size:14px;font-weight:bold;color:#fff">${escHtml(s.name)}</div>
        <div style="font-size:12px;color:#aaa;margin:4px 0"><i>Pattern:</i> ${escHtml(s.pattern)}</div>
        <div style="font-size:11px;color:#4ecca3">Example: "${escHtml(s.example_from_data)}"</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  // NEW PERSPECTIVES
  if(d.new_perspectives && d.new_perspectives.length) {
    html += `<div class="niche-card" style="border-left:3px solid #8b5cf6"><h3 style="color:#8b5cf6;margin-bottom:12px">💡 New Angles & Subthemes</h3>`;
    d.new_perspectives.forEach(p => {
      let ex = (p.generated_titles||[]).map(t=>`<li>"${escHtml(t)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:14px;font-weight:bold;color:#fff">${escHtml(p.new_angle)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(p.new_angle).replace(/'/g, "\\'")}')">🔀 Use in Remix</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin:6px 0"><i>Why it wins:</i> ${escHtml(p.why_it_will_win)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px;margin-top:6px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('vph-result').innerHTML = html;
}

// CROSSOVER ENGINE
async function generateCrossover() {
  const nicheA = document.getElementById('cross-niche-a').value.trim();
  const nicheB = document.getElementById('cross-niche-b').value.trim();
  const mechanic = document.getElementById('cross-mechanic').value;
  const lang = document.getElementById('cross-lang').value;
  
  if(!nicheA || !nicheB) {
    alert("Please enter both Niche A and Niche B.");
    return;
  }
  
  loading(true, 'Igniting Crossover Engine... Fusing ' + nicheA + ' with ' + nicheB + '...');
  const r = await post('/api/crossover_engine', { niche_a: nicheA, niche_b: nicheB, mechanic, language: lang });
  
  if(r.error) {
    document.getElementById('crossover-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.crossover_data || {};
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #8b5cf6">
    <h2 style="color:#8b5cf6;margin-bottom:12px">🧬 The Crossover Concept</h2>
    <div style="font-size:14px;color:#ddd;line-height:1.5;margin-bottom:16px">${escHtml(d.crossover_concept)}</div>
    
    <h3 style="color:#4ecca3;margin-bottom:8px">🌊 Cascading Narrative (Cause & Effect)</h3>
    <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;margin-bottom:16px">
      ${(d.cascading_narrative||[]).map(step => `<div style="font-size:13px;color:#aaa;margin-bottom:4px">▶️ ${escHtml(step)}</div>`).join('')}
    </div>
    
    <div style="font-size:12px;color:#f59e0b;margin-bottom:16px"><b>🧠 Audience Psychology:</b> ${escHtml(d.audience_psychology)}</div>
  </div>`;
  
  if(d.viral_crossover_titles && d.viral_crossover_titles.length) {
    html += `<div class="niche-card" style="border-left:3px solid #f59e0b"><h3 style="color:#f59e0b;margin-bottom:12px">🔥 Viral Crossover Titles</h3>`;
    d.viral_crossover_titles.forEach(t => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(t.title)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="document.getElementById('ab-title-a').value='${escHtml(t.title).replace(/'/g, "\\'") }';showPage('abtest')">⚖️ A/B Test</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin-top:6px"><i>Structure:</i> ${escHtml(t.structure)}</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('crossover-result').innerHTML = html;
}

// TREND HIJACKER
async function generateHijack() {
  const news = document.getElementById('hijack-news').value.trim();
  const niche = document.getElementById('hijack-niche').value.trim();
  const lang = document.getElementById('hijack-lang').value;
  
  if(!news || !niche) {
    alert("Please enter both Breaking News and Target Niche.");
    return;
  }
  
  loading(true, 'Hijacking Trend... Extracting psychology from news...');
  const r = await post('/api/trend_hijacker', { news_event: news, target_niche: niche, language: lang });
  
  if(r.error) {
    document.getElementById('hijacker-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.hijack_data || {};
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #e94560">
    <h2 style="color:#e94560;margin-bottom:12px">🚀 Evergreen Adaptation</h2>
    <div style="font-size:14px;color:#ddd;line-height:1.5;margin-bottom:16px">${escHtml(d.evergreen_concept)}</div>
    
    <div style="font-size:12px;color:#f59e0b;margin-bottom:16px"><b>🧠 Psychological Trigger:</b> ${escHtml(d.psychological_trigger)}</div>
  </div>`;
  
  if(d.titles && d.titles.length) {
    html += `<div class="niche-card" style="border-left:3px solid #4ecca3"><h3 style="color:#4ecca3;margin-bottom:12px">📐 Evergreen Titles</h3>`;
    d.titles.forEach(t => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(t.title)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="document.getElementById('ab-title-a').value='${escHtml(t.title).replace(/'/g, "\\'") }';showPage('abtest')">⚖️ A/B Test</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin-top:6px"><i>Structure:</i> ${escHtml(t.structure)}</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('hijacker-result').innerHTML = html;
}

// OUTLIER FINDER
async function generateOutliers() {
  const niche = document.getElementById('outlier-niche').value.trim();
  const lang = document.getElementById('outlier-lang').value;
  
  if(!niche) {
    alert("Please enter a niche.");
    return;
  }
  
  loading(true, 'Hunting Outliers... Scanning YouTube and calculating Outlier Scores...');
  const r = await post('/api/outlier_finder', { niche, language: lang });
  
  if(r.error) {
    document.getElementById('outlier-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const outliers = r.outliers_found || [];
  const ai = r.ai_analysis || {};
  
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #10b981">
    <h2 style="color:#10b981;margin-bottom:12px">🎯 Outliers Found</h2>
    <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px">`;
    
  outliers.forEach(o => {
    html += `<div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;position:relative">
      <div style="position:absolute;top:-10px;right:-10px;background:#e94560;color:white;padding:4px 8px;border-radius:12px;font-size:12px;font-weight:bold">${o.outlier_score}x OUTLIER</div>
      <a href="${o.url}" target="_blank" style="font-size:14px;font-weight:bold;color:#fff;text-decoration:none;display:block;margin-bottom:6px">${escHtml(o.title)}</a>
      <div style="font-size:12px;color:#aaa;display:flex;gap:12px">
        <span>👁️ ${o.views.toLocaleString()} views</span>
        <span>👥 ${o.subscribers.toLocaleString()} subs</span>
        <span>📺 ${escHtml(o.channel_title)}</span>
      </div>
    </div>`;
  });
  
  html += `</div>
    <h3 style="color:#f59e0b;margin-bottom:8px">🧠 The Outlier Secret</h3>
    <div style="font-size:13px;color:#ddd;line-height:1.5;margin-bottom:16px;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
      ${escHtml(ai.outlier_secret)}
    </div>
  </div>`;
  
  if(ai.cloned_titles && ai.cloned_titles.length) {
    html += `<div class="niche-card" style="border-left:3px solid #8b5cf6"><h3 style="color:#8b5cf6;margin-bottom:12px">🧬 Cloned Formats</h3>`;
    ai.cloned_titles.forEach(t => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(t.title)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="document.getElementById('ab-title-a').value='${escHtml(t.title).replace(/'/g, "\\'") }';showPage('abtest')">⚖️ A/B Test</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin-top:6px"><i>Structure:</i> ${escHtml(t.structure)}</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('outlier-result').innerHTML = html;
}

// COMPETITOR X-RAY
async function generateXRay() {
  const channel = document.getElementById('xray-handle').value.trim();
  
  if(!channel) {
    alert("Please enter a channel handle.");
    return;
  }
  
  loading(true, 'Running X-Ray on ' + channel + '... Scraping videos and estimating RPM...');
  const r = await post('/api/competitor_xray', { channel });
  
  if(r.error) {
    document.getElementById('xray-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const stats = r.channel_stats || {};
  const metrics = r.nexlev_metrics || {};
  const ai = r.ai_analysis || {};
  
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #e94560">
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
      ${stats.thumbnail ? `<img src="${stats.thumbnail}" style="width:60px;height:60px;border-radius:50%">` : ''}
      <div>
        <h2 style="color:#fff;margin:0">${escHtml(stats.title)}</h2>
        <div style="color:#aaa;font-size:13px">👥 ${parseInt(stats.subscribers).toLocaleString()} subs • 👁️ ${parseInt(stats.total_views).toLocaleString()} total views</div>
      </div>
    </div>
    
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase">Avg Views (Recent)</div>
        <div style="font-size:18px;font-weight:bold;color:#4ecca3">${parseInt(metrics.avg_views).toLocaleString()}</div>
      </div>
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase">Upload Velocity</div>
        <div style="font-size:18px;font-weight:bold;color:#f59e0b">${escHtml(metrics.upload_velocity)}</div>
      </div>
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;grid-column:1 / -1">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase">Estimated AdSense ($4 RPM)</div>
        <div style="font-size:22px;font-weight:bold;color:#10b981">${escHtml(metrics.est_revenue)}</div>
      </div>
    </div>
    
    <h3 style="color:#e94560;margin-bottom:8px">🔥 Top Recent Outlier</h3>
    <div style="background:#1a1a2e;padding:12px;border-radius:8px;border-left:2px solid #e94560;margin-bottom:16px">
      <a href="${metrics.top_recent_video.url}" target="_blank" style="color:#fff;font-weight:bold;text-decoration:none;display:block;margin-bottom:4px">${escHtml(metrics.top_recent_video.title)}</a>
      <div style="font-size:12px;color:#aaa">👁️ ${parseInt(metrics.top_recent_video.views).toLocaleString()} views</div>
    </div>
    
    <h3 style="color:#8b5cf6;margin-bottom:8px">🧠 The Secret Sauce (AI Analysis)</h3>
    <div style="font-size:13px;color:#ddd;line-height:1.5;margin-bottom:16px;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
      <b>Content Pillars:</b><br>
      ${(ai.content_pillars||[]).map(p => `• ${escHtml(p)}`).join('<br>')}<br><br>
      <b>Title Framework:</b><br>
      ${escHtml(ai.title_framework)}<br><br>
      <b>⚠️ Weakness (Your Opportunity):</b><br>
      <span style="color:#f59e0b">${escHtml(ai.weakness)}</span>
    </div>
  </div>`;
  
  document.getElementById('xray-result').innerHTML = html;
}

// NICHE SCORER
async function generateNicheScore() {
  const niche = document.getElementById('scorer-niche').value.trim();
  
  if(!niche) {
    alert("Please enter a niche.");
    return;
  }
  
  loading(true, 'Calculating Niche Profitability & Saturation Score...');
  const r = await post('/api/niche_scorer', { niche });
  
  if(r.error) {
    document.getElementById('scorer-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const m = r.metrics || {};
  const ai = r.ai_analysis || {};
  
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid ${m.color}">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <h2 style="color:${m.color};margin:0">Niche Score: ${m.score}/100</h2>
      <div style="background:#0d1117;padding:4px 8px;border-radius:12px;font-size:12px;border:1px solid ${m.color};color:${m.color}">${escHtml(m.saturation_label)}</div>
    </div>
    
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px">
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase">Avg Recent Views</div>
        <div style="font-size:18px;font-weight:bold;color:#fff">${m.avg_views.toLocaleString()}</div>
      </div>
      <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase">Avg Competitor Size</div>
        <div style="font-size:18px;font-weight:bold;color:#fff">${m.avg_subs.toLocaleString()} subs</div>
      </div>
    </div>
    
    <h3 style="color:#8b5cf6;margin-bottom:8px">🧠 Monetization & Pivot Strategy</h3>
    <div style="font-size:13px;color:#ddd;line-height:1.5;margin-bottom:16px;background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d">
      <b>Verdict:</b><br>
      ${escHtml(ai.verdict)}<br><br>
      <b>Monetization Strategy:</b><br>
      ${escHtml(ai.monetization_strategy)}<br><br>
      <b>💡 Subniche Pivot (Blue Ocean):</b><br>
      <span style="color:#4ecca3">${escHtml(ai.subniche_pivot)}</span>
    </div>
  </div>`;
  
  document.getElementById('scorer-result').innerHTML = html;
}

// SHORTS ENGINE
async function generateShorts() {
  const niche = document.getElementById('shorts-niche').value.trim();
  const topic = document.getElementById('shorts-topic').value.trim();
  const lang = document.getElementById('shorts-lang').value;
  
  if(!niche || !topic) {
    alert("Please enter both Niche and Topic.");
    return;
  }
  
  loading(true, 'Engineering TikTok/Shorts Viral Loop...');
  const r = await post('/api/shorts_engine', { niche, topic, language: lang });
  
  if(r.error) {
    document.getElementById('shorts-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #f59e0b">
    <h2 style="color:#f59e0b;margin-bottom:12px">📱 Viral Loop Generated</h2>
    
    <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;margin-bottom:16px">
      <div style="font-size:11px;color:#aaa;text-transform:uppercase;margin-bottom:4px">🛑 Scroll Stopper (First 3s)</div>
      <div style="font-size:16px;font-weight:bold;color:#fff">${escHtml(r.scroll_stopper)}</div>
    </div>
    
    <div style="background:#0d1117;padding:12px;border-radius:8px;border:1px solid #30363d;margin-bottom:16px">
      <div style="font-size:11px;color:#aaa;text-transform:uppercase;margin-bottom:8px">⏱️ 60-Second Pacing</div>
      <div style="display:flex;flex-direction:column;gap:8px">`;
      
  if(r.script_structure) {
    r.script_structure.forEach(s => {
      html += `<div style="display:flex;gap:12px;align-items:center;background:#1a1a2e;padding:8px;border-radius:6px">
        <span style="background:#f59e0b;color:#000;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:bold;min-width:50px;text-align:center">${escHtml(s.time)}</span>
        <span style="font-size:13px;color:#ddd">${escHtml(s.action)}</span>
      </div>`;
    });
  }
  
  html += `</div>
    </div>
    
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
      <div style="background:#1a1a2e;padding:12px;border-radius:8px;border-left:2px solid #10b981">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase;margin-bottom:4px">🔄 Perfect Loop Audio</div>
        <div style="font-size:13px;color:#10b981;font-weight:bold">${escHtml(r.perfect_loop_phrase)}</div>
      </div>
      <div style="background:#1a1a2e;padding:12px;border-radius:8px;border-left:2px solid #8b5cf6">
        <div style="font-size:11px;color:#aaa;text-transform:uppercase;margin-bottom:4px">🎵 Audio Vibe</div>
        <div style="font-size:13px;color:#8b5cf6;font-weight:bold">${escHtml(r.viral_audio_vibe)}</div>
      </div>
    </div>
  </div>`;
  
  document.getElementById('shorts-result').innerHTML = html;
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
  loading(true,'Discovering subniches with AI...');
  const r=await post('/api/subniche',{theme,language:lang});
  let html='';
  if(r.error){
    html=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ AI Error</h3><p style="font-size:13px;color:#aaa;margin:8px 0">${escHtml(r.error)}</p><p style="font-size:12px;color:#666">The Gemini API quota may be exhausted. Try again in 60 seconds or check your API key in Settings.</p></div>`;
  } else if(r.niches&&r.niches.length){
    r.niches.forEach((n,i)=>{
      const demColor=n.demand>=7?'#4ecca3':n.demand>=4?'#f59e0b':'#e94560';
      const supColor=n.supply<=3?'#4ecca3':n.supply<=6?'#f59e0b':'#e94560';
      const oppColor=n.opportunity>=6?'#4ecca3':n.opportunity>=3?'#f59e0b':'#e94560';
      let titles=(n.example_titles||[]).map(t=>`<div class="title-item" style="margin:4px 0"><div class="title-text">${escHtml(t)}</div><div class="title-len">${t.length}c</div></div>`).join('');
      let kws=(n.keywords||[]).map(k=>`<span class="tag tag-blue">${k}</span>`).join('');
      const bos = n.blue_ocean_score || 0;
      const bosColor = bos >= 80 ? '#4ecca3' : bos >= 60 ? '#f59e0b' : '#e94560';
      html+=`<div class="niche-card">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <h3 style="margin:0">#${i+1} ${escHtml(n.name)}</h3>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <span style="background:${bosColor}20;color:${bosColor};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700">BOS ${bos}</span>
            <button class="btn-primary" style="font-size:11px;padding:4px 8px;background:#7c3aed" onclick="findMicroNicho('${escHtml(n.name).replace(/'/g, "\\'")}','${escHtml(theme).replace(/'/g, "\\'")}')">🔬 Micro-Nichos</button>
            <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(n.name).replace(/'/g, "\\'")}')">🔀 Remix</button>
          </div>
        </div>
        <div class="niche-meter" style="margin-top:12px">
          <div class="meter"><div class="meter-label">DEMAND</div><div class="meter-bar"><div class="meter-fill" style="width:${n.demand*10}%;background:${demColor}"></div></div><div style="font-size:11px;color:${demColor};margin-top:2px">${n.demand}/10</div></div>
          <div class="meter"><div class="meter-label">SUPPLY</div><div class="meter-bar"><div class="meter-fill" style="width:${n.supply*10}%;background:${supColor}"></div></div><div style="font-size:11px;color:${supColor};margin-top:2px">${n.supply}/10</div></div>
          <div class="meter"><div class="meter-label">OPPORTUNITY</div><div class="meter-bar"><div class="meter-fill" style="width:${n.opportunity*10}%;background:${oppColor}"></div></div><div style="font-size:11px;color:${oppColor};margin-top:2px;font-weight:700">${n.opportunity}/10</div></div>
        </div>
        <div style="font-size:12px;color:#aaa;margin:8px 0">👤 <b>Audience:</b> ${escHtml(n.target_audience||'')}</div>
        <div style="font-size:12px;color:#aaa;margin:4px 0">😤 <b>Pain:</b> ${escHtml(n.audience_pain||'')}</div>
        <div style="font-size:12px;color:#aaa;margin:4px 0">🎯 <b>Angle:</b> ${escHtml(n.content_angle||'')}</div>
        ${n.first_video_idea ? `<div style="font-size:12px;color:#4ecca3;margin:4px 0;padding:6px;background:rgba(78,204,163,0.08);border-radius:4px">🎬 <b>1º Vídeo:</b> ${escHtml(n.first_video_idea)}</div>` : ''}
        ${n.rpm_estimate ? `<div style="font-size:12px;color:#f59e0b;margin:4px 0">💰 <b>RPM:</b> ${escHtml(n.rpm_estimate)}</div>` : ''}
        <div style="font-size:12px;color:#aaa;margin:4px 0">📊 <b>Est. views:</b> ${escHtml(n.estimated_views_per_video||'')}</div>
        <div class="tags" style="margin:8px 0">${kws}</div>
        <div style="margin-top:8px"><b style="font-size:11px;color:#4ecca3">EXAMPLE TITLES</b>${titles}</div>
      </div>`;

    });
  } else if(r.raw){
    html=`<div class="ai-text">${escHtml(r.raw)}</div>`;
  }
  document.getElementById('sub-result').innerHTML=html;
}

// TRENDS
async function scanTrends(){
  const cat=document.getElementById('trend-cat').value;
  const lang=document.getElementById('trend-lang').value;
  loading(true,'Scanning YouTube trends...');
  const r=await post('/api/trend_scanner',{category:cat,language:lang});
  
  if(r.error) {
    document.getElementById('trend-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.trends_data || {};
  let html = '';
  
  // TRENDING THEMES
  if(d.trending_themes && d.trending_themes.length) {
    html += `<div class="niche-card" style="border-left:3px solid #FFD700"><h3 style="color:#FFD700;margin-bottom:12px">🔥 Top Trending Themes</h3>`;
    d.trending_themes.forEach((t, i) => {
      const demColor = t.demand>=7?'#4ecca3':t.demand>=4?'#f59e0b':'#e94560';
      const compColor = t.competition<=3?'#4ecca3':t.competition<=6?'#f59e0b':'#e94560';
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">#${i+1} ${escHtml(t.name)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(t.name).replace(/'/g, "\\'")}')">🔀 Use in Remix</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin:6px 0"><i>Why:</i> ${escHtml(t.why_trending)}</div>
        <div style="display:flex;gap:12px;margin:8px 0">
          <div style="font-size:11px">Demand: <b style="color:${demColor}">${t.demand}/10</b></div>
          <div style="font-size:11px">Competition: <b style="color:${compColor}">${t.competition}/10</b></div>
        </div>
        <div style="font-size:12px;color:#4ecca3"><b>Angle:</b> ${escHtml(t.best_angle)}</div>
        <div style="font-size:12px;color:#ddd;margin-top:4px">"<i>${escHtml(t.example_title)}</i>"</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  // EMERGING NICHES
  if(d.emerging_niches && d.emerging_niches.length) {
    html += `<div class="niche-card" style="border-left:3px solid #4ecca3"><h3 style="color:#4ecca3;margin-bottom:12px">🌱 Emerging Micro-Niches</h3>`;
    d.emerging_niches.forEach(t => {
      let ex = (t.example_titles||[]).map(x=>`<li>"${escHtml(x)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(t.name)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(t.name).replace(/'/g, "\\'")}')">🔀 Use in Remix</button>
        </div>
        <div style="font-size:12px;color:#FFD700;margin:6px 0"><b>Opportunity:</b> ${t.opportunity_score}/10</div>
        <div style="font-size:12px;color:#aaa"><i>Audience:</i> ${escHtml(t.target_audience)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px;margin-top:6px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }
  
  // DYING NICHES
  if(d.dying_niches && d.dying_niches.length) {
    html += `<div class="niche-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560;margin-bottom:12px">💀 Dying Niches (Avoid)</h3>`;
    d.dying_niches.forEach(t => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="font-size:14px;font-weight:bold;color:#fff">${escHtml(t.name)}</div>
        <div style="font-size:12px;color:#aaa;margin-top:4px">${escHtml(t.reason)}</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('trend-result').innerHTML=html;
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
  
  if(r.error) {
    document.getElementById('strat-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.strategy_data || {};
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #FFD700">
    <h2 style="color:#FFD700;margin-bottom:8px">🧠 Audience Insight</h2>
    <div style="font-size:14px;color:#ddd;line-height:1.5">${escHtml(d.audience_insight || '')}</div>
  </div>`;
  
  // 1. BEST TITLE STRUCTURES
  if(d.best_structures && d.best_structures.length) {
    html += `<div class="niche-card" style="border-left:3px solid #4ecca3"><h3 style="color:#4ecca3;margin-bottom:12px">📐 Best Title Structures</h3>`;
    d.best_structures.forEach(s => {
      let ex = (s.example_titles||[]).map(t=>`<li>"${escHtml(t)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="font-size:14px;font-weight:bold;color:#fff">${escHtml(s.name)}</div>
        <div style="font-size:13px;color:#4ecca3;margin:4px 0">${escHtml(s.template)}</div>
        <div style="font-size:12px;color:#aaa;margin-bottom:6px"><i>Why it works:</i> ${escHtml(s.why_it_works)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }

  // 2. RECOMMENDED SUBTHEMES
  if(d.recommended_subthemes && d.recommended_subthemes.length) {
    html += `<div class="niche-card" style="border-left:3px solid #8b5cf6"><h3 style="color:#8b5cf6;margin-bottom:12px">🔥 Recommended Subthemes</h3>`;
    d.recommended_subthemes.forEach(s => {
      let ex = (s.example_titles||[]).map(t=>`<li>"${escHtml(t)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(s.name)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(s.name).replace(/'/g, "\\'")}')">🔀 Use in Remix</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin:6px 0"><i>Why it works:</i> ${escHtml(s.why_it_works)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }
  
  // 3. NEW PERSPECTIVES
  if(d.new_perspectives && d.new_perspectives.length) {
    html += `<div class="niche-card" style="border-left:3px solid #f59e0b"><h3 style="color:#f59e0b;margin-bottom:12px">💡 Disruptive Perspectives</h3>`;
    d.new_perspectives.forEach(s => {
      let ex = (s.example_titles||[]).map(t=>`<li>"${escHtml(t)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="font-size:14px;font-weight:bold;color:#fff">${escHtml(s.concept)}</div>
        <div style="font-size:12px;color:#aaa;margin:4px 0"><i>Why it works:</i> ${escHtml(s.why_it_works)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }
  
  // 4. ADJACENT SUBNICHES
  if(d.adjacent_subniches && d.adjacent_subniches.length) {
    html += `<div class="niche-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560;margin-bottom:12px">🔄 Adjacent Pivot Subniches</h3>`;
    d.adjacent_subniches.forEach(s => {
      let ex = (s.example_titles||[]).map(t=>`<li>"${escHtml(t)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(s.niche_name)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(s.niche_name).replace(/'/g, "\\'")}')">🔀 Use in Remix</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin:6px 0"><i>Crossover reason:</i> ${escHtml(s.crossover_reason)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('strat-result').innerHTML = html;
}

function useInRemix(subniche) {
  const type = document.getElementById('strat-type').value.trim();
  const lang = document.getElementById('strat-lang').value;
  
  // Pre-fill Remix
  document.getElementById('remix-niche').value = type;
  document.getElementById('remix-subniches').value = subniche;
  document.getElementById('remix-lang').value = lang;
  
  // Navigate to Remix
  showPage('remix');
  
  // Auto-trigger scan
  scanViralStructures();
}

// BATCH
async function batchAnalyze(){
  const text=document.getElementById('batch-input').value.trim();
  if(!text)return;
  const titles=text.split('\n').filter(t=>t.trim());
  loading(true,`Analyzing ${titles.length} titles...`);
  try {
    const r=await post('/api/analyze_batch',{titles});
    if(r.error){
      document.getElementById('batch-result').innerHTML=`<div class="score-card" style="color:#e94560">${escHtml(r.error)}</div>`;
      return;
    }
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
  } catch (e) {
    document.getElementById('batch-result').innerHTML=`<div class="score-card" style="color:#e94560">Error: ${escHtml(e.message)}</div>`;
  }
}

// =============================================
// STRATEGY REMIX ENGINE
// =============================================
let _scannedStructures = [];

async function scanViralStructures(){
  const niche=document.getElementById('remix-niche').value.trim();
  const subs=document.getElementById('remix-subniches').value.split(',').map(s=>s.trim()).filter(s=>s);
  if(!niche && !subs.length){alert('Enter main niche or subniches to scan');return}
  const lang=document.getElementById('remix-lang').value;

  loading(true,'Phase 1: Scanning viral structures across subniches...');
  const r=await post('/api/scan_viral_structures',{niche,subniches:subs,language:lang});

  if(r.error){
    document.getElementById('remix-structures').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }

  _scannedStructures = r.structures || [];
  let html=`<div class="score-card" style="border-left:3px solid #FFD700"><h3 style="color:#FFD700">📊 ${_scannedStructures.length} Viral Structures Found</h3></div>`;

  // Group by subniche
  const grouped={};
  _scannedStructures.forEach(s=>{
    const key=s.subniche||'General';
    if(!grouped[key])grouped[key]=[];
    grouped[key].push(s);
  });

  for(const[sub,items]of Object.entries(grouped)){
    html+=`<div class="niche-card"><h3 style="color:#4ecca3">📂 ${escHtml(sub)}</h3>`;
    items.forEach(s=>{
      html+=`<div style="padding:10px;margin:6px 0;background:#0d1117;border-radius:8px;border-left:3px solid #8b5cf6">
        <div style="font-size:14px;font-weight:600;margin-bottom:4px">
          ${s.url ? `<a href="${escHtml(s.url)}" target="_blank" style="color:#fff;text-decoration:none;display:flex;align-items:center;gap:6px;"><span style="color:#ff0000">▶️</span> "${escHtml(s.title)}"</a>` : `"${escHtml(s.title)}"`}
        </div>
        ${s.channel ? `<div style="font-size:12px;color:#aaa;margin-bottom:8px">📺 <b>${escHtml(s.channel)}</b></div>` : ''}
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:6px">
          <span class="tag tag-gold">TEMA: ${escHtml(s.tema)}</span>
          <span class="tag tag-green">SUBTEMA: ${escHtml(s.subtema)}</span>
          <span class="tag tag-purple">📐 ${escHtml(s.structure)}</span>
          ${s.views?`<span class="tag tag-blue">👁️ ${escHtml(s.views)} views</span>`:''}
        </div>
      </div>`;
    });
    html+=`</div>`;
  }

  document.getElementById('remix-structures').innerHTML=html;
  document.getElementById('remix-generate-section').style.display='block';
}

async function generateRemix(){
  if(!_scannedStructures.length){alert('First scan viral structures (Phase 1)');return}
  const niche=document.getElementById('remix-niche').value.trim();
  const lang=document.getElementById('remix-lang').value;
  const channel=document.getElementById('remix-channel').value.trim();
  const subs=document.getElementById('remix-subniches').value.split(',').map(s=>s.trim()).filter(s=>s);

  loading(true,'Phase 2: Generating remixed viral titles with dual-path system...');
  const r=await post('/api/strategy_remix',{
    viral_structures:_scannedStructures,
    niche,subniches:subs,language:lang,channel_name:channel
  });

  if(r.error){
    document.getElementById('remix-result').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }

  let html='';

  // STRATEGY A — Swap TEMA
  const stA=r.strategy_a||[];
  if(stA.length){
    html+=`<div class="niche-card" style="border-left:4px solid #FFD700">
      <h3 style="color:#FFD700;font-size:18px;margin-bottom:4px">🔀 CAMINHO A — Trocar TEMA (manter subtema)</h3>
      <p style="font-size:12px;color:#888;margin-bottom:12px">Pega um título viral, mantém o SUBTEMA que funciona, e troca o TEMA por outro em alta</p>`;
    stA.forEach((t,i)=>{
      const potColor={Explosive:'#FFD700',High:'#4ecca3',Medium:'#f59e0b',Low:'#e94560'}[t.estimated_potential]||'#888';
      html+=`<div style="padding:12px;margin:8px 0;background:#0d1117;border-radius:10px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:15px;font-weight:700;color:#fff;flex:1">${escHtml(t.new_title)}</div>
          ${t.grade?`<div class="score-circle ${gradeClass(t.grade)}" style="width:36px;height:36px;font-size:14px;margin-left:8px">${t.grade}<div style="font-size:8px">${t.score||''}</div></div>`:''}
        </div>
        <div style="font-size:11px;color:#666;margin-bottom:6px">Base: "${escHtml(t.original_title||'')}"</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <span class="tag" style="background:rgba(233,69,96,0.2);color:#e94560">✗ TEMA: ${escHtml(t.original_tema||'')} → ${escHtml(t.new_tema||'')}</span>
          <span class="tag" style="background:rgba(78,204,163,0.2);color:#4ecca3">✓ SUBTEMA: ${escHtml(t.kept_subtema||'')}</span>
          <span class="tag" style="background:rgba(${potColor==='#FFD700'?'255,215,0':potColor==='#4ecca3'?'78,204,163':potColor==='#f59e0b'?'245,158,11':'233,69,96'},0.2);color:${potColor}">🎯 ${t.estimated_potential||'?'}</span>
          ${t.length?`<span class="tag tag-blue">${t.length}c</span>`:''}
        </div>
        ${t.why_it_works?`<div style="font-size:11px;color:#4ecca3;margin-top:6px;font-style:italic">💡 ${escHtml(t.why_it_works)}</div>`:''}
      </div>`;
    });
    html+=`</div>`;
  }

  // STRATEGY B — Swap SUBTEMA
  const stB=r.strategy_b||[];
  if(stB.length){
    html+=`<div class="niche-card" style="border-left:4px solid #8b5cf6">
      <h3 style="color:#8b5cf6;font-size:18px;margin-bottom:4px">🔀 CAMINHO B — Trocar SUBTEMA (manter tema)</h3>
      <p style="font-size:12px;color:#888;margin-bottom:12px">Pega o título do concorrente, mantém o TEMA, e troca o SUBTEMA por um mais viral</p>`;
    stB.forEach((t,i)=>{
      const potColor={Explosive:'#FFD700',High:'#4ecca3',Medium:'#f59e0b',Low:'#e94560'}[t.estimated_potential]||'#888';
      html+=`<div style="padding:12px;margin:8px 0;background:#0d1117;border-radius:10px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <div style="font-size:15px;font-weight:700;color:#fff;flex:1">${escHtml(t.new_title)}</div>
          ${t.grade?`<div class="score-circle ${gradeClass(t.grade)}" style="width:36px;height:36px;font-size:14px;margin-left:8px">${t.grade}<div style="font-size:8px">${t.score||''}</div></div>`:''}
        </div>
        <div style="font-size:11px;color:#666;margin-bottom:6px">Base: "${escHtml(t.original_title||'')}"</div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <span class="tag" style="background:rgba(78,204,163,0.2);color:#4ecca3">✓ TEMA: ${escHtml(t.kept_tema||'')}</span>
          <span class="tag" style="background:rgba(233,69,96,0.2);color:#e94560">✗ SUBTEMA: ${escHtml(t.original_subtema||'')} → ${escHtml(t.new_subtema||'')}</span>
          <span class="tag" style="background:rgba(${potColor==='#FFD700'?'255,215,0':potColor==='#4ecca3'?'78,204,163':potColor==='#f59e0b'?'245,158,11':'233,69,96'},0.2);color:${potColor}">🎯 ${t.estimated_potential||'?'}</span>
          ${t.length?`<span class="tag tag-blue">${t.length}c</span>`:''}
        </div>
        ${t.why_it_works?`<div style="font-size:11px;color:#8b5cf6;margin-top:6px;font-style:italic">💡 ${escHtml(t.why_it_works)}</div>`:''}
      </div>`;
    });
    html+=`</div>`;
  }

  if(!stA.length && !stB.length){
    html=`<div class="score-card"><p style="color:#888">No strategies generated. Try different subniches.</p></div>`;
  }

  document.getElementById('remix-result').innerHTML=html;
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
  
  if(r.error) {
    document.getElementById('channel-analysis').innerHTML=`<div class="score-card" style="border-left:3px solid #e94560"><h3 style="color:#e94560">⚠️ Error</h3><p style="font-size:13px;color:#aaa">${escHtml(r.error)}</p></div>`;
    return;
  }
  
  const d = r.analysis_data || {};
  let html = `<div class="score-card" style="margin-bottom:16px;border-left:4px solid #4ecca3">
    <h2 style="color:#4ecca3;margin-bottom:8px">🧬 Channel DNA Analysis</h2>
    <div style="font-size:14px;color:#ddd;line-height:1.5">${escHtml(d.dna_analysis || '')}</div>
  </div>`;
  
  if(d.new_subniches && d.new_subniches.length) {
    html += `<div class="niche-card" style="border-left:3px solid #f59e0b"><h3 style="color:#f59e0b;margin-bottom:12px">💎 Discovered Subniches</h3>`;
    d.new_subniches.forEach(s => {
      let ex = (s.example_titles||[]).map(t=>`<li>"${escHtml(t)}"</li>`).join('');
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div style="font-size:15px;font-weight:bold;color:#fff">${escHtml(s.name)}</div>
          <button class="btn-primary" style="font-size:11px;padding:4px 8px" onclick="useInRemix('${escHtml(s.name).replace(/'/g, "\\'")}')">🔀 Use in Remix</button>
        </div>
        <div style="font-size:12px;color:#aaa;margin:4px 0"><i>Why:</i> ${escHtml(s.why_it_works)}</div>
        <div style="font-size:12px;color:#aaa;margin:4px 0"><i>Pain point:</i> ${escHtml(s.pain_point)}</div>
        <div style="font-size:11px;color:#f59e0b;margin-top:6px">Competition: ${escHtml(s.competition)}</div>
        <ul style="font-size:12px;color:#ddd;margin-left:16px;margin-top:8px">${ex}</ul>
      </div>`;
    });
    html += `</div>`;
  }
  
  if(d.action_plan && d.action_plan.length) {
    html += `<div class="niche-card" style="border-left:3px solid #8b5cf6"><h3 style="color:#8b5cf6;margin-bottom:12px">📅 Weekly Action Plan</h3>`;
    d.action_plan.forEach((p,i) => {
      html += `<div style="background:#0d1117;padding:12px;border-radius:8px;margin-bottom:8px;border:1px solid #30363d">
        <div style="font-size:14px;font-weight:bold;color:#fff">Video ${i+1}: ${escHtml(p.topic)}</div>
        <div style="font-size:12px;color:#4ecca3;margin-top:4px">🎯 Target: ${escHtml(p.priority_subniche)}</div>
        <div style="font-size:12px;color:#aaa;margin-top:2px">📐 Structure: ${escHtml(p.structure_used)}</div>
      </div>`;
    });
    html += `</div>`;
  }
  
  document.getElementById('channel-analysis').innerHTML = html;
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

// ─── NOVO: MICRO-NICHO ───
async function findMicroNicho(subniche, parentNiche) {
  loading(true, `🔬 Analisando micro-nichos de "${subniche}"...`);
  try {
    const lang = document.getElementById('sub-lang')?.value || 'Portuguese';
    const r = await post('/api/micronicho', { subniche, parent_niche: parentNiche || '', language: lang });
    if (r.error) { alert('Erro: ' + r.error); return; }
    const micros = r.micronichos || [];
    
    let html = `<div class="score-card" style="margin-top:16px">
      <h3 style="color:var(--accent);margin-bottom:12px">🔬 Micro-nichos de: ${escHtml(subniche)}</h3>
      <div style="display:grid;gap:12px">`;
    
    micros.forEach((m, i) => {
      const bos = m.blue_ocean_score || 0;
      const bosColor = bos >= 80 ? '#4ecca3' : bos >= 60 ? '#f59e0b' : '#e94560';
      const compColor = {'Very Low':'#4ecca3','Low':'#86efac','Medium':'#f59e0b','High':'#f97316','Very High':'#e94560'}[m.competition_level] || '#666';
      html += `<div style="border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:14px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
          <strong style="color:#fff;font-size:14px">${i+1}. ${escHtml(m.name)}</strong>
          <div style="display:flex;gap:8px">
            <span style="background:${bosColor}20;color:${bosColor};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700">BOS ${bos}</span>
            <span style="background:${compColor}20;color:${compColor};padding:2px 8px;border-radius:20px;font-size:11px">${escHtml(m.competition_level||'')}</span>
          </div>
        </div>
        ${m.unique_angle ? `<div style="font-size:12px;color:#a0a0a0;margin-bottom:6px">🎯 <b style="color:#4ecca3">Ângulo único:</b> ${escHtml(m.unique_angle)}</div>` : ''}
        ${m.target_avatar ? `<div style="font-size:12px;color:#a0a0a0;margin-bottom:6px">👤 <b>Avatar:</b> ${escHtml(m.target_avatar)}</div>` : ''}
        ${m.content_gap ? `<div style="font-size:12px;color:#a0a0a0;margin-bottom:6px">📭 <b>Gap:</b> ${escHtml(m.content_gap)}</div>` : ''}
        ${m.first_3_videos && m.first_3_videos.length > 0 ? `
        <div style="margin-top:8px">
          <div style="font-size:11px;color:#666;margin-bottom:4px">PRIMEIROS 3 VÍDEOS:</div>
          ${m.first_3_videos.map((v,vi) => `<div style="font-size:12px;color:#e0e0e0;padding:3px 0;border-bottom:1px solid rgba(255,255,255,0.05)">${vi+1}. ${escHtml(v)}</div>`).join('')}
        </div>` : ''}
        <div style="display:flex;gap:12px;margin-top:8px;font-size:11px;color:#666">
          ${m.estimated_rpm ? `<span>💰 ${escHtml(m.estimated_rpm)}</span>` : ''}
          ${m.time_to_monetize ? `<span>⏱️ ${escHtml(m.time_to_monetize)}</span>` : ''}
          ${m.why_now ? `<span title="${escHtml(m.why_now)}">🔥 Por que agora</span>` : ''}
        </div>
      </div>`;
    });
    
    html += `</div></div>`;
    
    // Inserir após o card do sub-nicho pai
    const container = document.getElementById('sub-result');
    if (container) {
      const existing = container.querySelector('.micros-container-' + subniche.replace(/\s/g,'_'));
      if (existing) existing.remove();
      const div = document.createElement('div');
      div.className = 'micros-container-' + subniche.replace(/\s/g,'_');
      div.innerHTML = html;
      container.appendChild(div);
    }
  } catch(e) { alert('Erro: ' + e.message); }
  finally { loading(false); }
}

// ─── NOVO: ESTRATÉGIA COMPLETA 3 NÍVEIS ───
async function nicheStrategyComplete() {
  const niche = document.getElementById('sub-theme')?.value?.trim();
  if (!niche) { alert('Digite um nicho!'); return; }
  const lang = document.getElementById('sub-lang')?.value || 'Portuguese';
  
  loading(true, `🗺️ Gerando mapa estratégico completo de "${niche}"...`);
  try {
    const r = await post('/api/niche_strategy_complete', { niche, language: lang });
    if (r.error) { alert('Erro: ' + r.error); return; }
    const st = r.strategy || {};
    const mn = st.main_niche || {};
    const subs = st.subniches || [];
    
    let html = `<div class="score-card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 style="color:var(--accent);margin:0">🗺️ Mapa Estratégico: ${escHtml(mn.name || niche)}</h3>
        <div style="font-size:12px;color:#666">${subs.length} sub-nichos · ${subs.reduce((a,s)=>a+(s.micronichos||[]).length,0)} micro-nichos</div>
      </div>`;
    
    if (mn.opportunity_summary) {
      html += `<div style="background:rgba(78,204,163,0.08);border:1px solid rgba(78,204,163,0.2);border-radius:8px;padding:12px;margin-bottom:16px;font-size:13px;color:#a0a0a0">${escHtml(mn.opportunity_summary)}</div>`;
    }
    
    if (mn.avg_rpm || mn.market_size) {
      html += `<div style="display:flex;gap:16px;margin-bottom:16px;font-size:12px">
        ${mn.avg_rpm ? `<span style="color:#4ecca3">💰 RPM médio: ${escHtml(mn.avg_rpm)}</span>` : ''}
        ${mn.market_size ? `<span style="color:#a0a0a0">📊 Mercado: ${escHtml(mn.market_size)}</span>` : ''}
        ${mn.competition ? `<span style="color:#f59e0b">⚔️ Competição: ${escHtml(mn.competition)}</span>` : ''}
      </div>`;
    }
    
    subs.forEach((sub, si) => {
      const bos = sub.blue_ocean_score || 0;
      const bosColor = bos >= 80 ? '#4ecca3' : bos >= 60 ? '#f59e0b' : '#e94560';
      const micros = sub.micronichos || [];
      
      html += `<details style="margin-bottom:10px" open>
        <summary style="cursor:pointer;padding:12px;background:rgba(255,255,255,0.04);border-radius:8px;list-style:none;display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:600;color:#fff">${si+1}. ${escHtml(sub.name)}</span>
          <div style="display:flex;gap:8px;align-items:center">
            <span style="background:${bosColor}20;color:${bosColor};padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700">BOS ${bos}</span>
            ${sub.rpm_estimate ? `<span style="color:#666;font-size:11px">${escHtml(sub.rpm_estimate)}</span>` : ''}
            <span style="color:#666;font-size:11px">${micros.length} micro</span>
          </div>
        </summary>
        <div style="padding:12px;border:1px solid rgba(255,255,255,0.06);border-top:none;border-radius:0 0 8px 8px">
          ${sub.content_angle ? `<div style="font-size:12px;color:#a0a0a0;margin-bottom:6px">🎯 ${escHtml(sub.content_angle)}</div>` : ''}
          ${sub.first_video_idea ? `<div style="font-size:12px;color:#4ecca3;margin-bottom:8px;padding:6px;background:rgba(78,204,163,0.08);border-radius:4px">🎬 1º vídeo: ${escHtml(sub.first_video_idea)}</div>` : ''}
          ${sub.example_titles && sub.example_titles.length ? `<div style="margin-bottom:10px">${sub.example_titles.map(t=>`<div style="font-size:12px;padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.05);color:#e0e0e0">• ${escHtml(t)}</div>`).join('')}</div>` : ''}
          ${micros.length > 0 ? `
          <div style="margin-top:10px">
            <div style="font-size:11px;color:#666;margin-bottom:8px;letter-spacing:0.5px">MICRO-NICHOS:</div>
            <div style="display:grid;gap:8px">
              ${micros.map((mc, mi) => {
                const mbos = mc.blue_ocean_score || 0;
                const mc_color = mbos >= 80 ? '#4ecca3' : mbos >= 60 ? '#f59e0b' : '#e94560';
                return `<div style="padding:10px;background:rgba(255,255,255,0.02);border-radius:6px;border-left:3px solid ${mc_color}">
                  <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                    <span style="font-size:12px;font-weight:600;color:#fff">${escHtml(mc.name)}</span>
                    <span style="font-size:10px;color:${mc_color}">BOS ${mbos}</span>
                  </div>
                  ${mc.unique_angle ? `<div style="font-size:11px;color:#808080;margin-bottom:3px">→ ${escHtml(mc.unique_angle)}</div>` : ''}
                  ${mc.first_3_videos && mc.first_3_videos.length ? `<div style="font-size:11px;color:#606060">${mc.first_3_videos.slice(0,2).map((v,vi)=>`${vi+1}. ${escHtml(v)}`).join(' · ')}</div>` : ''}
                  <div style="font-size:10px;color:#555;margin-top:4px">${mc.estimated_rpm ? mc.estimated_rpm : ''} ${mc.time_to_monetize ? '· '+mc.time_to_monetize : ''}</div>
                </div>`;
              }).join('')}
            </div>
          </div>` : ''}
        </div>
      </details>`;
    });
    
    html += `</div>`;
    
    const res = document.getElementById('sub-result');
    if (res) res.innerHTML = html;
  } catch(e) { alert('Erro: ' + e.message); }
  finally { loading(false); }
}


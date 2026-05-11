// AvatarPilot Pro v2 — Frontend Logic
'use strict';

let currentEngine  = 'edge-tts';
let pollingInterval = null;
let currentPlan    = 'unlimited';
let allTemplates   = [];
let batchJobIds    = [];
let batchPollIv    = null;

// ============================================================================
// NAVIGATION
// ============================================================================
function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  const btn = document.querySelector(`[data-page="${id}"]`);
  if (btn) btn.classList.add('active');

  if (id === 'backgrounds') loadBackgrounds();
  if (id === 'settings')    { checkModels(); loadDashboard(); loadSettingsPage(); }
  if (id === 'history')     loadHistory();
  if (id === 'voices')      loadVoiceList();
  if (id === 'batch')       initBatchPage();
}

function loading(show, text) {
  document.getElementById('loading').style.display = show ? 'flex' : 'none';
  if (text) document.getElementById('loading-text').textContent = text;
}

function toast(msg, type = 'info') {
  let t = document.getElementById('toast-container');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast-container';
    t.style.cssText = 'position:fixed;bottom:24px;right:24px;z-index:99999;display:flex;flex-direction:column;gap:8px';
    document.body.appendChild(t);
  }
  const el = document.createElement('div');
  const colors = { info: '#7c3aed', success: '#10b981', error: '#ef4444', warn: '#f59e0b' };
  el.style.cssText = `background:${colors[type]||colors.info};color:#fff;padding:12px 18px;border-radius:10px;font-size:13px;font-weight:600;max-width:340px;box-shadow:0 4px 20px rgba(0,0,0,0.4);animation:fadeIn 0.3s`;
  el.textContent = msg;
  t.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ============================================================================
// IMAGE PREVIEW
// ============================================================================
function previewImage(input) {
  if (input.files && input.files[0]) {
    const reader = new FileReader();
    reader.onload = e => {
      document.getElementById('img-preview').src = e.target.result;
      document.getElementById('img-preview').style.display = 'block';
      document.getElementById('upload-placeholder').style.display = 'none';
    };
    reader.readAsDataURL(input.files[0]);
  }
}

// ============================================================================
// SCRIPT COUNTER + PLAN LIMIT WARNING
// ============================================================================
const scriptEl = document.getElementById('script');
if (scriptEl) {
  scriptEl.addEventListener('input', updateScriptStats);
}

function updateScriptStats() {
  const text  = (document.getElementById('script') || {}).value || '';
  const chars = text.length;
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const secs  = Math.round(words / 2.5);
  const mins  = (secs / 60).toFixed(1);

  const cc = document.getElementById('char-count');
  const wc = document.getElementById('word-count');
  const ed = document.getElementById('est-duration');
  const pl = document.getElementById('plan-limit-info');

  if (cc) cc.textContent = chars + ' chars';
  if (wc) wc.textContent = words + ' words';
  if (ed) ed.textContent = `~${secs}s`;

  // Plan limit warning
  if (pl) {
    const limits = { free: 5, starter: 30, pro: 60, unlimited: null };
    const lim = limits[currentPlan];
    if (lim && secs > lim * 60) {
      pl.textContent = `⚠️ ~${mins}min exceeds ${lim}min plan limit`;
      pl.style.color = 'var(--red)';
    } else if (lim) {
      pl.textContent = `Plan limit: ${lim}min`;
      pl.style.color = 'var(--dim)';
    } else {
      pl.textContent = '';
    }
  }
}

// EXPRESSION SLIDER
const expSlider = document.getElementById('expression-scale');
if (expSlider) {
  expSlider.addEventListener('input', () => {
    document.getElementById('exp-val').textContent = expSlider.value;
  });
}
// OPACITY SLIDER
const opSlider = document.getElementById('avatar-opacity');
if (opSlider) {
  opSlider.addEventListener('input', () => {
    document.getElementById('opacity-val').textContent = opSlider.value;
  });
}

// ============================================================================
// ENGINE SWITCH
// ============================================================================
function setEngine(engine, btn) {
  currentEngine = engine;
  document.querySelectorAll('.engine-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('edge-options').style.display   = engine === 'edge-tts'   ? 'block' : 'none';
  document.getElementById('eleven-options').style.display = engine === 'elevenlabs' ? 'block' : 'none';
}

// ============================================================================
// VOICE FILTER (by language in create page)
// ============================================================================
function filterVoicesByLang() {
  const lang = document.getElementById('voice-lang').value;
  // Load matching voices into voice-select
  fetch('/api/voices').then(r => r.json()).then(d => {
    const sel = document.getElementById('voice-select');
    sel.innerHTML = '';
    for (const [locale, voices] of Object.entries(d.voices || {})) {
      if (!lang || locale.startsWith(lang.split('-')[0])) {
        voices.forEach(v => {
          sel.innerHTML += `<option value="${v.name}">${v.display || v.name} (${v.gender})</option>`;
        });
      }
    }
  }).catch(() => {});
}

// ============================================================================
// PREVIEW AUDIO
// ============================================================================
async function previewAudio() {
  const script = document.getElementById('script').value.trim();
  if (!script) { toast('Write a script first', 'warn'); return; }
  loading(true, 'Generating voice preview...');
  try {
    const body = {
      script: script.substring(0, 300),
      engine: currentEngine,
      voice:    document.getElementById('voice-select').value,
      voice_id: document.getElementById('eleven-voice-id')?.value || '',
    };
    const r = await fetch('/api/preview_audio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    const audio = document.getElementById('audio-preview');
    audio.src = d.audio_url;
    audio.style.display = 'block';
    audio.play();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// GENERATE AVATAR
// ============================================================================
async function generateAvatar() {
  const imgInput   = document.getElementById('img-input');
  const script     = document.getElementById('script').value.trim();
  const audioInput = document.getElementById('audio-input');

  if (!imgInput.files || !imgInput.files[0]) { toast('Upload an avatar image first', 'warn'); return; }
  if (!script && (!audioInput.files || !audioInput.files[0])) { toast('Write a script or upload audio', 'warn'); return; }

  const formData = new FormData();
  formData.append('image',            imgInput.files[0]);
  formData.append('script',           script);
  formData.append('engine',           currentEngine);
  formData.append('voice',            document.getElementById('voice-select').value);
  formData.append('voice_id',         document.getElementById('eleven-voice-id')?.value || '');
  formData.append('preprocess',       document.getElementById('preprocess').value);
  formData.append('still_mode',       document.getElementById('still-mode').checked);
  formData.append('expression_scale', document.getElementById('expression-scale').value);
  formData.append('enhancer',         document.getElementById('enhancer').value);
  formData.append('background',       document.getElementById('bg-select').value);
  formData.append('size',             document.getElementById('size').value);
  formData.append('avatar_position',  document.getElementById('avatar-position').value);
  formData.append('avatar_size',      document.getElementById('avatar-size').value);
  formData.append('avatar_opacity',   document.getElementById('avatar-opacity').value);

  if (audioInput.files && audioInput.files[0]) {
    formData.append('audio', audioInput.files[0]);
  }

  document.getElementById('result').style.display        = 'none';
  document.getElementById('progress-area').style.display = 'block';
  document.getElementById('progress-fill').style.width   = '5%';
  document.getElementById('progress-status').textContent = 'Starting generation...';
  document.getElementById('progress-text').textContent   = 'Uploading files...';

  try {
    const r = await fetch('/api/generate', { method: 'POST', body: formData });
    const d = await r.json();
    if (d.error) {
      toast(d.error, 'error');
      document.getElementById('progress-area').style.display = 'none';
      return;
    }
    pollJob(d.job_id);
  } catch (e) {
    toast('Error: ' + e.message, 'error');
    document.getElementById('progress-area').style.display = 'none';
  }
}

function pollJob(jobId) {
  if (pollingInterval) clearInterval(pollingInterval);
  const startTime = Date.now();

  pollingInterval = setInterval(async () => {
    try {
      const r = await fetch('/api/job/' + jobId);
      const d = await r.json();

      const statusMap = {
        'queued':           'Queued...',
        'generating_audio': 'Generating voice audio...',
        'generating_video': 'Running SadTalker lip sync...',
        'compositing':      'Applying background...',
        'done':             'Done!',
        'error':            'Error',
      };

      document.getElementById('progress-status').textContent = statusMap[d.status] || d.status;
      document.getElementById('progress-fill').style.width   = (d.progress || 0) + '%';
      document.getElementById('progress-text').textContent   = `${d.progress || 0}% — ${d.message || ''}`;

      // elapsed
      const elapsed = Math.round((Date.now() - startTime) / 1000);
      const durEl   = document.getElementById('progress-duration');
      if (durEl) durEl.textContent = `Elapsed: ${elapsed}s${d.audio_duration ? ' | Audio: ' + d.audio_duration + 's' : ''}`;

      if (d.status === 'done') {
        clearInterval(pollingInterval);
        document.getElementById('progress-area').style.display = 'none';
        document.getElementById('result').style.display        = 'block';
        document.getElementById('result-video').src   = '/outputs/' + d.output_filename;
        document.getElementById('download-link').href = '/outputs/' + d.output_filename;
        toast('Avatar video ready!', 'success');
      }
      if (d.status === 'error') {
        clearInterval(pollingInterval);
        document.getElementById('progress-text').textContent    = 'Error: ' + (d.error || 'Unknown');
        document.getElementById('progress-fill').style.background = 'var(--red)';
        toast('Error: ' + (d.error || 'Unknown'), 'error');
      }
    } catch (e) { console.error('Poll error:', e); }
  }, 2000);
}

// ============================================================================
// BACKGROUNDS
// ============================================================================
async function loadBackgrounds() {
  try {
    const r = await fetch('/api/backgrounds');
    const d = await r.json();
    const gallery = document.getElementById('bg-gallery');
    const sel     = document.getElementById('bg-select');

    let html = '';
    (d.backgrounds || []).forEach(bg => {
      html += `<div class="bg-card" onclick="selectBg('${bg.name}')">
        <img src="${bg.url}" alt="${bg.name}" loading="lazy">
        <div class="bg-name">${bg.name}</div>
      </div>`;
    });
    gallery.innerHTML = html || '<p style="color:var(--dim);padding:20px">No backgrounds uploaded yet.</p>';

    sel.innerHTML = '<option value="">No background (original)</option>';
    (d.backgrounds || []).forEach(bg => {
      sel.innerHTML += `<option value="${bg.name}">${bg.name}</option>`;
    });
  } catch (e) { console.error(e); }
}

function selectBg(name) {
  document.getElementById('bg-select').value = name;
  toast(`Background selected: ${name}`, 'success');
  showPage('create');
}

async function uploadBackground(input) {
  if (!input.files || !input.files[0]) return;
  const formData = new FormData();
  formData.append('file', input.files[0]);
  loading(true, 'Uploading background...');
  try {
    await fetch('/api/backgrounds/upload', { method: 'POST', body: formData });
    loadBackgrounds();
    toast('Background uploaded!', 'success');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// VOICES PAGE
// ============================================================================
async function loadVoiceList() {
  loading(true, 'Loading voices...');
  try {
    const r      = await fetch('/api/voices');
    const d      = await r.json();
    const filter = document.getElementById('voices-filter-lang')?.value || '';
    let html = '', count = 0;

    for (const [lang, voices] of Object.entries(d.voices || {})) {
      if (filter && !lang.toLowerCase().startsWith(filter.toLowerCase())) continue;
      voices.forEach(v => {
        const gClass = v.gender === 'Male' ? 'voice-gender-m' : 'voice-gender-f';
        const gIcon  = v.gender === 'Male' ? '👨' : '👩';
        html += `<div class="voice-item" onclick="selectVoice('${v.name}')">
          <span class="${gClass}">${gIcon}</span>
          <span class="voice-name">${v.display || v.name}</span>
          <span class="voice-meta">${v.locale}</span>
          <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="event.stopPropagation();testVoice('${v.name}')">▶ Test</button>
        </div>`;
        count++;
      });
    }
    document.getElementById('voices-list').innerHTML =
      `<div style="margin-bottom:8px;font-size:12px;color:var(--dim)">${count} voices found</div>` + html;
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

function selectVoice(name) {
  const sel = document.getElementById('voice-select');
  let found = false;
  for (let opt of sel.options) { if (opt.value === name) { found = true; break; } }
  if (!found) sel.innerHTML += `<option value="${name}">${name}</option>`;
  sel.value = name;
  toast(`Voice selected: ${name}`, 'success');
  showPage('create');
}

async function testVoice(name) {
  loading(true, 'Testing voice...');
  try {
    const r = await fetch('/api/preview_audio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script: 'Hello! This is a test of this voice. How does it sound?', voice: name, engine: 'edge-tts' })
    });
    const d = await r.json();
    if (d.audio_url) { const a = new Audio(d.audio_url); a.play(); }
  } catch (e) { console.error(e); }
  finally { loading(false); }
}

// ============================================================================
// HISTORY
// ============================================================================
async function loadHistory() {
  const grid  = document.getElementById('history-grid');
  const stats = document.getElementById('history-stats');
  if (!grid) return;
  grid.innerHTML = '<div style="color:var(--dim);padding:20px">Loading...</div>';

  try {
    const r = await fetch('/api/history');
    const d = await r.json();
    stats.textContent = `${d.total} videos · ${d.total_size_mb} MB used`;

    if (!d.videos || d.videos.length === 0) {
      grid.innerHTML = '<div style="color:var(--dim);padding:40px;text-align:center">No videos generated yet.<br>Go to Create to make your first avatar!</div>';
      return;
    }

    grid.innerHTML = d.videos.map(v => {
      const date    = new Date(v.created).toLocaleString();
      const thumbEl = v.thumbnail
        ? `<img src="${v.thumbnail}?t=${Date.now()}" class="history-thumb" loading="lazy" onerror="this.src='/static/thumb_placeholder.png'">`
        : `<div class="history-thumb-placeholder">🎭</div>`;
      const dur     = v.duration ? `${v.duration}s` : '—';
      const size    = v.size_mb ? `${v.size_mb} MB` : '—';
      return `
      <div class="history-card" id="hcard-${v.id}">
        <div class="history-thumb-wrap" onclick="previewVideo('${v.filename}','${v.id}')">${thumbEl}<div class="history-play">▶</div></div>
        <div class="history-info">
          <div class="history-filename">${v.filename || v.id}</div>
          <div class="history-meta">${date} · ${dur} · ${size} · ${v.voice || '—'}</div>
          ${v.script_preview ? `<div class="history-script">"${v.script_preview}"</div>` : ''}
        </div>
        <div class="history-actions">
          <a href="/outputs/${v.filename}" download class="btn-secondary" style="padding:6px 12px;font-size:12px">⬇️ Download</a>
          <button class="btn-secondary" style="padding:6px 12px;font-size:12px;color:var(--red);border-color:var(--red)" onclick="deleteVideo('${v.id}')">🗑️</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    grid.innerHTML = `<div style="color:var(--red);padding:20px">Error loading history: ${e.message}</div>`;
  }
}

function previewVideo(filename, id) {
  const existing = document.getElementById('modal-player');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'modal-player';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  overlay.innerHTML = `
    <div style="background:var(--card);border-radius:16px;padding:20px;max-width:780px;width:95%">
      <video src="/outputs/${filename}" controls autoplay style="width:100%;border-radius:10px"></video>
      <div style="display:flex;gap:10px;margin-top:12px;justify-content:center">
        <a href="/outputs/${filename}" download class="btn-primary">⬇️ Download</a>
        <button class="btn-secondary" onclick="document.getElementById('modal-player').remove()">✕ Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
}

async function deleteVideo(videoId) {
  if (!confirm('Delete this video?')) return;
  try {
    await fetch(`/api/history/${videoId}`, { method: 'DELETE' });
    document.getElementById(`hcard-${videoId}`)?.remove();
    toast('Video deleted', 'success');
    loadHistory();
  } catch (e) { toast('Error deleting: ' + e.message, 'error'); }
}

async function clearHistory() {
  if (!confirm('Delete ALL history? This cannot be undone.')) return;
  try {
    await fetch('/api/history/clear', { method: 'POST' });
    toast('History cleared', 'success');
    loadHistory();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

// ============================================================================
// AI FEATURES
// ============================================================================
function openAIModal()  { document.getElementById('ai-modal').style.display = 'flex'; }
function closeAIModal() { document.getElementById('ai-modal').style.display = 'none'; }

async function generateAIScript() {
  const topic  = document.getElementById('ai-topic').value.trim();
  if (!topic) { toast('Enter a topic first', 'warn'); return; }

  document.getElementById('ai-loading').style.display = 'block';
  try {
    const r = await fetch('/api/ai/generate_script', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        topic,
        language: document.getElementById('ai-lang').value,
        style:    document.getElementById('ai-style').value,
        length:   document.getElementById('ai-length').value,
      })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    document.getElementById('script').value = d.script;
    updateScriptStats();
    closeAIModal();
    toast('Script generated!', 'success');
  } catch (e) { toast('AI Error: ' + e.message, 'error'); }
  finally { document.getElementById('ai-loading').style.display = 'none'; }
}

async function enhanceScript() {
  const script = document.getElementById('script').value.trim();
  if (!script) { toast('Write a script first', 'warn'); return; }
  loading(true, 'AI is enhancing your script...');
  try {
    const r = await fetch('/api/ai/enhance_script', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script, goal: 'make it more natural, engaging, and clear for video narration' })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    document.getElementById('script').value = d.script;
    updateScriptStats();
    toast('Script enhanced!', 'success');
  } catch (e) { toast('AI Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

async function suggestVoice() {
  const script = document.getElementById('script').value.trim();
  if (!script) { toast('Write a script first', 'warn'); return; }
  loading(true, 'AI is suggesting a voice...');
  try {
    const r = await fetch('/api/ai/suggest_voice', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script, content_type: 'video narration' })
    });
    const d = await r.json();
    if (d.voice) {
      const sel = document.getElementById('voice-select');
      let found = false;
      for (let opt of sel.options) { if (opt.value === d.voice) { found = true; break; } }
      if (!found) sel.innerHTML += `<option value="${d.voice}">${d.voice}</option>`;
      sel.value = d.voice;
      toast(`AI suggests: ${d.voice} — ${d.reason || ''}`, 'success');
    }
  } catch (e) { toast('AI Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// AI IMAGE GENERATION
// ============================================================================
function openImageGenModal()  { document.getElementById('img-gen-modal').style.display = 'flex'; }
function closeImageGenModal() {
  document.getElementById('img-gen-modal').style.display = 'none';
  document.getElementById('img-gen-result').style.display = 'none';
  document.getElementById('img-gen-loading').style.display = 'none';
}

async function generateAvatarImage() {
  const prompt = document.getElementById('img-gen-prompt').value.trim();
  if (!prompt) { toast('Describe your avatar first', 'warn'); return; }

  const style  = document.getElementById('img-gen-style').value;
  const size   = parseInt(document.getElementById('img-gen-size').value);

  document.getElementById('img-gen-loading').style.display = 'block';
  document.getElementById('img-gen-result').style.display  = 'none';

  try {
    const r = await fetch('/api/ai/generate_image', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, style, width: size, height: size })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }

    const preview = document.getElementById('img-gen-preview');
    preview.src = d.image_url + '?t=' + Date.now();
    preview.dataset.serverPath = d.path;
    document.getElementById('img-gen-result').style.display = 'block';
    toast('Image generated!', 'success');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { document.getElementById('img-gen-loading').style.display = 'none'; }
}

async function useGeneratedImage() {
  const preview = document.getElementById('img-gen-preview');
  const imgUrl  = preview.src;
  try {
    const resp = await fetch(imgUrl);
    const blob = await resp.blob();
    const file = new File([blob], 'ai_generated_avatar.png', { type: 'image/png' });
    const dt   = new DataTransfer();
    dt.items.add(file);
    const input = document.getElementById('img-input');
    input.files = dt.files;

    const reader = new FileReader();
    reader.onload = e => {
      document.getElementById('img-preview').src = e.target.result;
      document.getElementById('img-preview').style.display = 'block';
      document.getElementById('upload-placeholder').style.display = 'none';
    };
    reader.readAsDataURL(file);
    closeImageGenModal();
    toast('AI image set as avatar!', 'success');
  } catch (e) { toast('Error loading image: ' + e.message, 'error'); }
}

// ============================================================================
// ELEVENLABS VOICES
// ============================================================================
async function loadElevenLabsVoices() {
  const sel = document.getElementById('eleven-voice-select');
  if (!sel) return;
  sel.innerHTML = '<option value="">Loading...</option>';
  try {
    const r = await fetch('/api/voices/elevenlabs');
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); sel.innerHTML = '<option value="">Error loading voices</option>'; return; }
    sel.innerHTML = '<option value="">— Select a voice —</option>';
    (d.voices || []).forEach(v => {
      const cat = v.category !== 'premade' ? ` (${v.category})` : '';
      sel.innerHTML += `<option value="${v.id}">${v.name}${cat}</option>`;
    });
    toast(`${d.total} ElevenLabs voices loaded`, 'success');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function loadElevenLabsVoicesFull() {
  const container = document.getElementById('elevenlabs-voices-list');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--dim);font-size:13px">Loading...</div>';
  try {
    const r = await fetch('/api/voices/elevenlabs');
    const d = await r.json();
    if (d.error) { container.innerHTML = `<div style="color:var(--red);font-size:13px">${d.error}</div>`; return; }
    if (!d.voices || d.voices.length === 0) {
      container.innerHTML = '<div style="color:var(--dim);font-size:13px">No voices found. Check your ElevenLabs API key in Settings.</div>';
      return;
    }
    container.innerHTML = `<div style="margin-bottom:8px;font-size:12px;color:var(--dim)">${d.total} voices</div>` +
      d.voices.map(v => {
        const catColor = v.category === 'cloned' ? 'var(--accent2)' : v.category === 'professional' ? 'var(--gold)' : 'var(--dim)';
        return `<div class="voice-item" onclick="selectElevenVoice('${v.id}','${v.name}')">
          <span style="color:${catColor};font-size:11px;text-transform:uppercase;font-weight:700">${v.category}</span>
          <span class="voice-name">${v.name}</span>
          <span class="voice-meta">${v.id.substring(0, 8)}...</span>
          <button class="btn-secondary" style="padding:4px 10px;font-size:11px"
            onclick="event.stopPropagation();testElevenVoice('${v.id}')">▶ Test</button>
        </div>`;
      }).join('');
  } catch (e) { container.innerHTML = `<div style="color:var(--red)">${e.message}</div>`; }
}

function selectElevenVoice(id, name) {
  const idInput = document.getElementById('eleven-voice-id');
  const sel     = document.getElementById('eleven-voice-select');
  if (idInput) idInput.value = id;
  if (sel) {
    let found = false;
    for (let o of sel.options) { if (o.value === id) { found = true; break; } }
    if (!found) sel.innerHTML += `<option value="${id}">${name}</option>`;
    sel.value = id;
  }
  toast(`ElevenLabs voice selected: ${name}`, 'success');
  showPage('create');
  // Switch to ElevenLabs engine
  const tab = document.querySelector('.engine-tab:nth-child(2)');
  if (tab) setEngine('elevenlabs', tab);
}

async function testElevenVoice(voiceId) {
  loading(true, 'Testing ElevenLabs voice...');
  try {
    const r = await fetch('/api/preview_audio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        script: 'Hello! This is a test of this ElevenLabs voice. How does it sound?',
        engine: 'elevenlabs', voice_id: voiceId
      })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    new Audio(d.audio_url).play();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// VOICE CLONING
// ============================================================================
async function cloneVoice() {
  const name     = document.getElementById('clone-name').value.trim();
  const desc     = document.getElementById('clone-description').value.trim();
  const files    = document.getElementById('clone-audio-input').files;
  const resultEl = document.getElementById('clone-result');

  if (!name)          { toast('Enter a voice name', 'warn'); return; }
  if (!files || files.length === 0) { toast('Upload at least one audio sample', 'warn'); return; }

  loading(true, 'Cloning voice with ElevenLabs... (may take 30-60s)');
  try {
    const formData = new FormData();
    formData.append('name', name);
    formData.append('description', desc);
    for (let i = 0; i < Math.min(files.length, 5); i++) {
      formData.append('audio', files[i]);
    }

    const r = await fetch('/api/voices/clone', { method: 'POST', body: formData });
    const d = await r.json();

    if (d.error) { toast(d.error, 'error'); return; }

    resultEl.style.display = 'block';
    resultEl.innerHTML = `
      <div style="color:var(--green);font-weight:700;margin-bottom:8px">✅ Voice cloned successfully!</div>
      <div style="font-size:13px">Name: <strong>${d.name}</strong></div>
      <div style="font-size:13px;margin-top:4px">Voice ID: <code style="background:var(--bg);padding:2px 6px;border-radius:4px">${d.voice_id}</code></div>
      <button class="btn-primary" style="margin-top:10px" onclick="selectElevenVoice('${d.voice_id}','${d.name}')">
        ✅ Use This Voice
      </button>`;
    toast(`Voice "${name}" cloned!`, 'success');
    // Auto-refresh ElevenLabs list
    loadElevenLabsVoicesFull();
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// TEMPLATES
// ============================================================================
async function loadTemplates() {
  try {
    const r = await fetch('/api/templates');
    const d = await r.json();
    allTemplates = d.templates || [];
    const sel = document.getElementById('template-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">Load a template...</option>';
    allTemplates.forEach(t => {
      sel.innerHTML += `<option value="${t.id}">${t.name}</option>`;
    });
  } catch (e) { console.error('Templates load error:', e); }
}

function loadTemplate(id) {
  if (!id) return;
  const tmpl = allTemplates.find(t => t.id === id);
  if (!tmpl) return;
  const s = tmpl.settings || {};
  const set = (elId, val) => { const el = document.getElementById(elId); if (el && val !== undefined) el.value = val; };
  set('voice-select',      s.voice);
  set('preprocess',        s.preprocess);
  set('expression-scale',  s.expression_scale);
  set('enhancer',          s.enhancer);
  set('size',              s.size);
  set('bg-select',         s.background);
  set('avatar-position',   s.avatar_position);
  set('avatar-size',       s.avatar_size);
  set('avatar-opacity',    s.avatar_opacity);
  if (s.script_template) document.getElementById('script').value = s.script_template;
  if (s.still_mode !== undefined) document.getElementById('still-mode').checked = s.still_mode;
  document.getElementById('exp-val').textContent     = s.expression_scale || 1.0;
  document.getElementById('opacity-val').textContent = s.avatar_opacity || 1.0;
  updateScriptStats();
  toast(`Template "${tmpl.name}" loaded!`, 'success');
}

function saveCurrentTemplate() {
  document.getElementById('tmpl-modal').style.display = 'flex';
  document.getElementById('tmpl-name').focus();
}

function closeTmplModal() { document.getElementById('tmpl-modal').style.display = 'none'; }

async function confirmSaveTemplate() {
  const name = document.getElementById('tmpl-name').value.trim();
  if (!name) { toast('Enter a template name', 'warn'); return; }
  try {
    await fetch('/api/templates/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        voice:            document.getElementById('voice-select').value,
        engine:           currentEngine,
        preprocess:       document.getElementById('preprocess').value,
        still_mode:       document.getElementById('still-mode').checked,
        expression_scale: parseFloat(document.getElementById('expression-scale').value),
        enhancer:         document.getElementById('enhancer').value,
        size:             parseInt(document.getElementById('size').value),
        background:       document.getElementById('bg-select').value,
        avatar_position:  document.getElementById('avatar-position').value,
        avatar_size:      document.getElementById('avatar-size').value,
        avatar_opacity:   parseFloat(document.getElementById('avatar-opacity').value),
        script_template:  document.getElementById('script').value,
      })
    });
    closeTmplModal();
    loadTemplates();
    toast(`Template "${name}" saved!`, 'success');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

// ============================================================================
// BATCH PROCESSING
// ============================================================================
let batchRows = [];

function initBatchPage() {
  if (document.getElementById('batch-jobs-list').children.length === 0) {
    addBatchRow();
    addBatchRow();
  }
}

function addBatchRow() {
  const list = document.getElementById('batch-jobs-list');
  const idx  = list.children.length;
  const row  = document.createElement('div');
  row.className = 'batch-row';
  row.dataset.idx = idx;
  row.innerHTML = `
    <div class="batch-row-header">
      <span class="batch-row-num">Job ${idx + 1}</span>
      <button class="btn-secondary" style="padding:4px 10px;font-size:11px;color:var(--red)" onclick="this.closest('.batch-row').remove();renumberBatch()">✕</button>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
      <div>
        <label style="font-size:11px;color:var(--dim)">Script:</label>
        <textarea class="batch-script" rows="3" placeholder="Script for job ${idx + 1}..."></textarea>
      </div>
      <div>
        <label style="font-size:11px;color:var(--dim)">Image (optional — uses shared image if empty):</label>
        <input type="file" class="batch-image" accept="image/*">
        <label style="font-size:11px;color:var(--dim);margin-top:6px">Status:</label>
        <div class="batch-status" style="font-size:12px;color:var(--dim)">Pending</div>
        <div class="batch-progress" style="width:0%;height:4px;background:var(--accent);border-radius:2px;margin-top:4px;transition:width 0.5s"></div>
      </div>
    </div>`;
  list.appendChild(row);
}

function renumberBatch() {
  document.querySelectorAll('.batch-row').forEach((row, i) => {
    const numEl = row.querySelector('.batch-row-num');
    if (numEl) numEl.textContent = `Job ${i + 1}`;
  });
}

async function startBatch() {
  const rows    = document.querySelectorAll('.batch-row');
  const voice   = document.getElementById('batch-voice').value;
  const size    = parseInt(document.getElementById('batch-size').value);
  const imgInput = document.getElementById('img-input');

  if (rows.length === 0) { toast('Add at least one job', 'warn'); return; }

  // Build jobs array — batch API accepts JSON, so we use base64 for images
  const jobs = [];
  for (const row of rows) {
    const script    = row.querySelector('.batch-script')?.value?.trim();
    const imgFile   = row.querySelector('.batch-image')?.files?.[0];
    const sharedImg = imgInput?.files?.[0];
    const imgSrc    = imgFile || sharedImg;

    if (!script) continue;
    if (!imgSrc) { toast('Upload an image (shared or per-job)', 'warn'); return; }

    // Convert image to base64
    const imgB64 = await fileToBase64(imgSrc);
    jobs.push({ script, voice, size, image_base64: imgB64, preprocess: 'crop', enhancer: 'gfpgan' });
  }

  if (jobs.length === 0) { toast('No valid jobs to run', 'warn'); return; }

  loading(true, `Queuing ${jobs.length} batch jobs...`);
  try {
    const r = await fetch('/api/batch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobs })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }

    batchJobIds = d.job_ids || [];
    document.getElementById('batch-progress-area').style.display = 'block';
    toast(`Batch started: ${batchJobIds.length} jobs queued`, 'success');
    pollBatch(batchJobIds, Array.from(rows));
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = e => resolve(e.target.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function pollBatch(jobIds, rows) {
  if (batchPollIv) clearInterval(batchPollIv);
  const progressEl = document.getElementById('batch-jobs-progress');

  batchPollIv = setInterval(async () => {
    try {
      const r  = await fetch('/api/batch/status', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: jobIds })
      });
      const d  = await r.json();
      const jbs = d.jobs || [];

      let allDone = true;
      progressEl.innerHTML = jbs.map((j, i) => {
        const pct    = j.progress || 0;
        const status = j.status   || 'queued';
        const color  = status === 'done' ? 'var(--green)' : status === 'error' ? 'var(--red)' : 'var(--accent2)';
        if (!['done','error'].includes(status)) allDone = false;
        const dlBtn  = status === 'done' ? `<a href="/outputs/${j.output_filename}" download class="btn-secondary" style="padding:4px 10px;font-size:11px">⬇️</a>` : '';
        return `<div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
            <span style="font-size:12px;font-weight:600">Job ${i+1}: <span style="color:${color}">${status}</span></span>
            ${dlBtn}
          </div>
          <div style="background:var(--bg);border-radius:4px;height:6px;overflow:hidden">
            <div style="width:${pct}%;height:100%;background:${color};transition:width 0.5s;border-radius:4px"></div>
          </div>
          <div style="font-size:11px;color:var(--dim);margin-top:2px">${j.message || ''}</div>
        </div>`;
      }).join('');

      if (allDone) {
        clearInterval(batchPollIv);
        toast('All batch jobs complete!', 'success');
      }
    } catch (e) { console.error('Batch poll error:', e); }
  }, 3000);
}

// ============================================================================
// SETTINGS
// ============================================================================
async function saveElevenKey() {
  const key = document.getElementById('eleven-key').value.trim();
  if (!key) { toast('Paste your API key', 'warn'); return; }
  try {
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ elevenlabs_key: key })
    });
    toast('ElevenLabs key saved!', 'success');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function savePlan() {
  const plan = document.getElementById('plan-select').value;
  try {
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan })
    });
    currentPlan = plan;
    document.getElementById('plan-badge').textContent = 'Plan: ' + plan;
    updateScriptStats();
    toast(`Plan set to: ${plan}`, 'success');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function loadSettingsPage() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    currentPlan = d.plan || 'unlimited';
    const planSel = document.getElementById('plan-select');
    if (planSel) planSel.value = currentPlan;
    document.getElementById('plan-badge').textContent = 'Plan: ' + currentPlan;
  } catch (e) {}
}

// ============================================================================
// MODEL CHECK + VALIDATE
// ============================================================================
async function checkModels() {
  try {
    const r = await fetch('/api/check_models');
    const d = await r.json();
    const statusEl = document.getElementById('model-status');
    const infoEl   = document.getElementById('model-info');

    if (d.sadtalker) {
      statusEl.textContent = 'SadTalker Ready';
      statusEl.style.color = 'var(--green)';
      const det = d.detail || {};
      const rows = Object.entries(det).map(([k, v]) =>
        `<div style="display:flex;gap:8px;font-size:12px;margin-bottom:4px">
          <span style="color:${v ? 'var(--green)' : 'var(--red)'}">${v ? '✅' : '❌'}</span>
          <span style="color:var(--dim)">${k}</span>
        </div>`
      ).join('');
      if (infoEl) infoEl.innerHTML = rows;
    } else {
      statusEl.textContent = 'Model needed';
      statusEl.style.color = 'var(--gold)';
      if (infoEl) infoEl.innerHTML = '<div style="color:var(--gold);font-size:13px">SadTalker not installed. Click Install below.</div>';
    }
  } catch (e) { console.error(e); }
}

async function validateSadTalker() {
  loading(true, 'Running SadTalker end-to-end test (may take 2-3 min)...');
  try {
    const r = await fetch('/api/validate_sadtalker', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      toast(`SadTalker OK! Output: ${d.output_duration}s video`, 'success');
    } else {
      toast(`SadTalker test failed: ${d.error}`, 'error');
    }
  } catch (e) { toast('Test error: ' + e.message, 'error'); }
  finally { loading(false); }
}

async function installSadTalker() {
  loading(true, 'Installing SadTalker... This may take a few minutes.');
  try {
    const r = await fetch('/api/install_sadtalker', { method: 'POST' });
    const d = await r.json();
    toast(d.message || 'Installation started', 'info');
  } catch (e) { toast('Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// DASHBOARD
// ============================================================================
async function loadDashboard() {
  const el = document.getElementById('dashboard-content');
  if (!el) return;
  el.innerHTML = '<div style="color:var(--dim);font-size:13px">Loading stats...</div>';
  try {
    const r = await fetch('/api/dashboard');
    const d = await r.json();
    const gpu = d.gpu || {};
    const vramBar = gpu.vram_total
      ? `<div style="margin-top:4px;background:var(--bg);border-radius:4px;height:6px;overflow:hidden">
           <div style="width:${Math.round((gpu.vram_used||0)/gpu.vram_total*100)}%;height:100%;background:var(--accent);border-radius:4px"></div>
         </div>`
      : '';

    el.innerHTML = `
      <div class="dashboard-grid">
        <div class="dash-card"><div class="dash-val">${d.total_generated}</div><div class="dash-label">Videos Generated</div></div>
        <div class="dash-card"><div class="dash-val">${d.total_hours}h</div><div class="dash-label">Total Content</div></div>
        <div class="dash-card"><div class="dash-val">${d.disk_used_mb} MB</div><div class="dash-label">Disk Used</div></div>
        <div class="dash-card"><div class="dash-val">${d.active_jobs}</div><div class="dash-label">Active Jobs</div></div>
      </div>
      <div style="margin-top:14px">
        <div style="font-size:12px;color:var(--dim);margin-bottom:8px">GPU</div>
        <div style="font-size:13px">${gpu.available ? '✅ ' + gpu.name : '❌ No GPU'}</div>
        ${gpu.vram_total ? `<div style="font-size:12px;color:var(--dim);margin-top:4px">VRAM: ${gpu.vram_used||0}GB / ${gpu.vram_total}GB used${vramBar}</div>` : ''}
      </div>
      <div style="margin-top:12px">
        <div style="font-size:12px;color:var(--dim);margin-bottom:6px">Services</div>
        <div style="font-size:12px">SadTalker: ${d.sadtalker?.ready ? '✅ Ready' : '❌ Not installed'}</div>
        <div style="font-size:12px;margin-top:4px">Edge-TTS: ${d.edge_tts ? '✅ Ready' : '❌ Missing'}</div>
        <div style="font-size:12px;margin-top:4px">Plan: <strong>${d.plan}</strong> (limit: ${d.plan_limits?.[d.plan] ? d.plan_limits[d.plan] + ' min' : 'unlimited'})</div>
      </div>
      <button class="btn-secondary" onclick="loadDashboard()" style="margin-top:14px;font-size:12px">🔄 Refresh</button>`;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);font-size:13px">Error: ${e.message}</div><button class="btn-secondary" onclick="loadDashboard()">Retry</button>`;
  }
}

// ============================================================================
// INIT
// ============================================================================
async function init() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    currentPlan = d.plan || 'unlimited';
    document.getElementById('plan-badge').textContent = 'Plan: ' + currentPlan;
    updateScriptStats();
  } catch (e) {}
  checkModels();
  loadBackgrounds();
  loadTemplates();
}

init();

// AvatarPilot Pro — Frontend Logic
let currentEngine = 'edge-tts';
let pollingInterval = null;

function showPage(id) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('page-' + id).classList.add('active');
  document.querySelector(`[data-page="${id}"]`).classList.add('active');
  if (id === 'backgrounds') loadBackgrounds();
  if (id === 'settings') checkModels();
}

function loading(show, text) {
  document.getElementById('loading').style.display = show ? 'flex' : 'none';
  if (text) document.getElementById('loading-text').textContent = text;
}

// IMAGE PREVIEW
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

// SCRIPT COUNTER
const scriptEl = document.getElementById('script');
if (scriptEl) {
  scriptEl.addEventListener('input', () => {
    const text = scriptEl.value;
    const chars = text.length;
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    const seconds = Math.round(words / 2.5); // ~150 words/min
    document.getElementById('char-count').textContent = chars + ' chars';
    document.getElementById('word-count').textContent = words + ' words';
    document.getElementById('est-duration').textContent = '~' + seconds + 's';
  });
}

// EXPRESSION SLIDER
const expSlider = document.getElementById('expression-scale');
if (expSlider) {
  expSlider.addEventListener('input', () => {
    document.getElementById('exp-val').textContent = expSlider.value;
  });
}

// ENGINE SWITCH
function setEngine(engine, btn) {
  currentEngine = engine;
  document.querySelectorAll('.engine-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('edge-options').style.display = engine === 'edge-tts' ? 'block' : 'none';
  document.getElementById('eleven-options').style.display = engine === 'elevenlabs' ? 'block' : 'none';
}

// PREVIEW AUDIO
async function previewAudio() {
  const script = document.getElementById('script').value.trim();
  if (!script) { alert('Write a script first'); return; }

  loading(true, 'Generating voice preview...');
  try {
    const body = {
      script: script.substring(0, 300),
      engine: currentEngine,
      voice: document.getElementById('voice-select').value,
      voice_id: document.getElementById('eleven-voice-id')?.value || '',
    };
    const r = await fetch('/api/preview_audio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (d.error) { alert(d.error); return; }
    const audio = document.getElementById('audio-preview');
    audio.src = d.audio_url;
    audio.style.display = 'block';
    audio.play();
  } catch (e) { alert('Error: ' + e.message); }
  finally { loading(false); }
}

// GENERATE AVATAR
async function generateAvatar() {
  const imgInput = document.getElementById('img-input');
  const script = document.getElementById('script').value.trim();
  const audioInput = document.getElementById('audio-input');

  if (!imgInput.files || !imgInput.files[0]) { alert('Upload an avatar image first'); return; }
  if (!script && (!audioInput.files || !audioInput.files[0])) { alert('Write a script or upload audio'); return; }

  const formData = new FormData();
  formData.append('image', imgInput.files[0]);
  formData.append('script', script);
  formData.append('engine', currentEngine);
  formData.append('voice', document.getElementById('voice-select').value);
  formData.append('voice_id', document.getElementById('eleven-voice-id')?.value || '');
  formData.append('preprocess', document.getElementById('preprocess').value);
  formData.append('still_mode', document.getElementById('still-mode').checked);
  formData.append('expression_scale', document.getElementById('expression-scale').value);
  formData.append('enhancer', document.getElementById('enhancer').value);
  formData.append('background', document.getElementById('bg-select').value);

  if (audioInput.files && audioInput.files[0]) {
    formData.append('audio', audioInput.files[0]);
  }

  // Show progress
  document.getElementById('result').style.display = 'none';
  document.getElementById('progress-area').style.display = 'block';
  document.getElementById('progress-fill').style.width = '5%';
  document.getElementById('progress-status').textContent = '⏳ Starting generation...';
  document.getElementById('progress-text').textContent = 'Uploading files...';

  try {
    const r = await fetch('/api/generate', { method: 'POST', body: formData });
    const d = await r.json();
    if (d.error) { alert(d.error); document.getElementById('progress-area').style.display = 'none'; return; }

    // Start polling
    pollJob(d.job_id);
  } catch (e) {
    alert('Error: ' + e.message);
    document.getElementById('progress-area').style.display = 'none';
  }
}

function pollJob(jobId) {
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(async () => {
    try {
      const r = await fetch('/api/job/' + jobId);
      const d = await r.json();

      const statusMap = {
        'queued': '⏳ Queued...',
        'generating_audio': '🎙️ Generating voice...',
        'generating_video': '🎬 Creating lip sync video...',
        'compositing': '🏞️ Applying background...',
        'done': '✅ Done!',
        'error': '❌ Error',
      };

      document.getElementById('progress-status').textContent = statusMap[d.status] || d.status;
      document.getElementById('progress-fill').style.width = (d.progress || 0) + '%';
      document.getElementById('progress-text').textContent = `${d.progress || 0}% complete`;

      if (d.status === 'done') {
        clearInterval(pollingInterval);
        document.getElementById('progress-area').style.display = 'none';
        document.getElementById('result').style.display = 'block';
        document.getElementById('result-video').src = '/outputs/' + d.output_filename;
        document.getElementById('download-link').href = '/outputs/' + d.output_filename;
      }
      if (d.status === 'error') {
        clearInterval(pollingInterval);
        document.getElementById('progress-text').textContent = 'Error: ' + (d.error || 'Unknown');
        document.getElementById('progress-fill').style.background = 'var(--red)';
      }
    } catch (e) { console.error(e); }
  }, 2000);
}

// BACKGROUNDS
async function loadBackgrounds() {
  try {
    const r = await fetch('/api/backgrounds');
    const d = await r.json();
    const gallery = document.getElementById('bg-gallery');
    const sel = document.getElementById('bg-select');

    // Update gallery
    let html = '';
    (d.backgrounds || []).forEach(bg => {
      html += `<div class="bg-card" onclick="selectBg('${bg.name}')">
        <img src="${bg.url}" alt="${bg.name}">
        <div class="bg-name">${bg.name}</div>
      </div>`;
    });
    gallery.innerHTML = html || '<p style="color:var(--dim)">No backgrounds uploaded yet. Upload some above!</p>';

    // Update selector
    sel.innerHTML = '<option value="">No background (original)</option>';
    (d.backgrounds || []).forEach(bg => {
      sel.innerHTML += `<option value="${bg.name}">${bg.name}</option>`;
    });
  } catch (e) { console.error(e); }
}

function selectBg(name) {
  document.getElementById('bg-select').value = name;
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
  } catch (e) { alert('Error: ' + e.message); }
  finally { loading(false); }
}

// VOICES
async function loadVoiceList() {
  loading(true, 'Loading voices...');
  try {
    const r = await fetch('/api/voices');
    const d = await r.json();
    const filter = document.getElementById('voices-filter-lang').value;
    let html = '';
    let count = 0;

    for (const [lang, voices] of Object.entries(d.voices || {})) {
      if (filter && !lang.startsWith(filter)) continue;
      voices.forEach(v => {
        const gClass = v.gender === 'Male' ? 'voice-gender-m' : 'voice-gender-f';
        const gIcon = v.gender === 'Male' ? '👨' : '👩';
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
  } catch (e) { alert('Error: ' + e.message); }
  finally { loading(false); }
}

function selectVoice(name) {
  const sel = document.getElementById('voice-select');
  // Add option if not exists
  let found = false;
  for (let opt of sel.options) { if (opt.value === name) { found = true; break; } }
  if (!found) sel.innerHTML += `<option value="${name}">${name}</option>`;
  sel.value = name;
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
    if (d.audio_url) {
      const audio = new Audio(d.audio_url);
      audio.play();
    }
  } catch (e) { console.error(e); }
  finally { loading(false); }
}

// SETTINGS
async function saveElevenKey() {
  const key = document.getElementById('eleven-key').value.trim();
  if (!key) { alert('Paste your API key'); return; }
  try {
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ elevenlabs_key: key })
    });
    alert('ElevenLabs key saved!');
  } catch (e) { alert('Error: ' + e.message); }
}

async function checkModels() {
  try {
    const r = await fetch('/api/check_models');
    const d = await r.json();
    const statusEl = document.getElementById('model-status');
    const infoEl = document.getElementById('model-info');

    if (d.sadtalker) {
      statusEl.textContent = '✅ SadTalker Ready';
      statusEl.style.color = 'var(--green)';
      if (infoEl) infoEl.innerHTML = '<div style="color:var(--green);font-size:13px">✅ SadTalker installed and ready</div>';
    } else {
      statusEl.textContent = '⚠️ Model needed';
      statusEl.style.color = 'var(--gold)';
      if (infoEl) infoEl.innerHTML = '<div style="color:var(--gold);font-size:13px">⚠️ SadTalker not installed. Click below to install.</div>';
    }
  } catch (e) { console.error(e); }
}

async function installSadTalker() {
  loading(true, 'Installing SadTalker... This may take a few minutes.');
  try {
    const r = await fetch('/api/install_sadtalker', { method: 'POST' });
    const d = await r.json();
    alert(d.message || 'Installation started');
  } catch (e) { alert('Error: ' + e.message); }
  finally { loading(false); }
}

// INIT
checkModels();
loadBackgrounds();

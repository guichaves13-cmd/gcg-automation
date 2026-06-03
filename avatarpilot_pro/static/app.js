// AvatarPilot Pro v3 — Frontend Logic (MuseTalk 1.5 primary)
'use strict';

function _esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}

function _addOption(sel, value, label) {
  const o = document.createElement('option');
  o.value = value;
  o.textContent = label;
  sel.appendChild(o);
}

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
  if (id === 'settings')    { checkModels(); loadDashboard(); loadSettingsPage(); loadWebhooks(); }
  if (id === 'history')     loadHistory();
  if (id === 'voices')      loadVoiceList();
  if (id === 'batch')       initBatchPage();
  if (id === 'studio')      { loadAvatarLibrary(); loadStockAvatarLibrary(); populateClothingAvatarSelect(); }
  if (id === 'editor')      loadMergeHistory();
  if (id === 'translate')   autoSetTranslVoice();
  if (id === 'admin')       adminCheckSession();
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
function previewImage(input) { previewAvatarFile(input); } // alias

function previewAvatarFile(input) {
  if (!input.files || !input.files[0]) return;
  const file    = input.files[0];
  const isVideo = file.type.startsWith('video/') || /\.(mp4|mov|avi|webm|mkv)$/i.test(file.name);
  const imgEl   = document.getElementById('img-preview');
  const vidEl   = document.getElementById('vid-preview');
  const ph      = document.getElementById('upload-placeholder');
  const badge   = document.getElementById('avatar-mode-badge');

  ph.style.display = 'none';

  if (isVideo) {
    imgEl.style.display = 'none';
    vidEl.src = URL.createObjectURL(file);
    vidEl.style.display = 'block';
    if (badge) {
      badge.style.display = 'block';
      badge.style.background = 'rgba(16,185,129,0.15)';
      badge.style.color = '#10b981';
      badge.style.border = '1px solid #10b981';
      badge.innerHTML = '🎬 Modo Vídeo — corpo e mãos se movem naturalmente (loop automático se necessário)';
    }
  } else {
    vidEl.style.display = 'none';
    const reader = new FileReader();
    reader.onload = e => { imgEl.src = e.target.result; imgEl.style.display = 'block'; };
    reader.readAsDataURL(file);
    if (badge) {
      badge.style.display = 'block';
      badge.style.background = 'rgba(124,58,237,0.1)';
      badge.style.color = '#a78bfa';
      badge.style.border = '1px solid #a78bfa';
      badge.innerHTML = '📷 Modo Foto — só a boca mexe. Para mãos e corpo: suba um vídeo curto.';
    }
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

  if (cc) cc.textContent = chars + ' caracteres';
  if (wc) wc.textContent = words + ' palavras';
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

// EXPRESSION SLIDER — hidden input, listener removido (valor fixo 1.2 via backend automático)
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
          _addOption(sel, v.name, `${v.display || v.name} (${v.gender})`);
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
  if (!script) { toast('Escreva um roteiro primeiro', 'warn'); return; }
  loading(true, 'Gerando prévia de voz...');
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
    if (!r.ok || d.error) { toast(d.error || `Erro ${r.status}`, 'error'); return; }
    const audio = document.getElementById('audio-preview');
    audio.src = d.audio_url;
    audio.style.display = 'block';
    audio.play();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// LIP SYNC ENGINE — agora automático, sem seleção manual
// ============================================================================
function updateLipSyncUI() {
  // No-op — engine is auto-selected by backend based on input file type
}

function applyQualityPreset(preset) {
  const enhance = document.getElementById('enhance-face');
  if (!preset) return;
  // Engine is always auto-detected; presets now only affect GFPGAN toggle
  switch (preset) {
    case 'fast':
      if (enhance) enhance.checked = false;
      break;
    case 'standard':
    case 'motion':
    case 'ultra':
      if (enhance) enhance.checked = true;
      break;
  }
  toast(`Preset aplicado: ${preset}`, 'success');
}

// ============================================================================
// GENERATE AVATAR
// ============================================================================
async function generateAvatar() {
  const imgInput   = document.getElementById('img-input');
  const script     = document.getElementById('script').value.trim();
  const audioInput = document.getElementById('audio-input');

  // Inject library/studio avatar into file input if selected
  if (window._avatarLibraryUrl && (!imgInput.files || !imgInput.files[0])) {
    try {
      const resp = await fetch(window._avatarLibraryUrl);
      const blob = await resp.blob();
      const ext  = window._avatarLibraryUrl.split('.').pop().split('?')[0] || 'jpg';
      const file = new File([blob], `avatar_library.${ext}`, { type: blob.type || 'image/jpeg' });
      const dt   = new DataTransfer();
      dt.items.add(file);
      imgInput.files = dt.files;
    } catch(ex) {
      toast('Erro ao carregar avatar da biblioteca: ' + ex.message, 'error');
      return;
    }
  }
  window._avatarLibraryPath = null;
  window._avatarLibraryUrl  = null;

  if (!imgInput.files || !imgInput.files[0]) { toast('Envie uma imagem de avatar primeiro', 'warn'); return; }
  if (!script && (!audioInput.files || !audioInput.files[0])) { toast('Escreva um roteiro ou envie um áudio', 'warn'); return; }

  const formData = new FormData();
  formData.append('image',            imgInput.files[0]);
  formData.append('script',           script);
  formData.append('engine',           currentEngine);
  formData.append('voice',            document.getElementById('voice-select').value);
  formData.append('voice_id',         document.getElementById('eleven-voice-id')?.value || '');
  formData.append('preprocess',       'full');
  formData.append('still_mode',       'false');
  formData.append('expression_scale', '1.2');
  formData.append('enhancer',         'gfpgan');
  formData.append('lip_sync_engine',  'auto');
  formData.append('background',       document.getElementById('bg-select').value);
  formData.append('size',             document.getElementById('size').value);
  formData.append('avatar_position',  document.getElementById('avatar-position').value);
  formData.append('avatar_size',      document.getElementById('avatar-size').value);
  formData.append('avatar_opacity',   document.getElementById('avatar-opacity').value);
  formData.append('voice_preset',     document.getElementById('voice-preset-select')?.value || '');

  // New: captions + background removal
  const captionsOn = document.getElementById('captions-enable')?.checked || false;
  formData.append('captions',          captionsOn);
  if (captionsOn) {
    formData.append('caption_lang',      document.getElementById('caption-lang')?.value || '');
    formData.append('caption_model',     document.getElementById('caption-model')?.value || 'base');
    formData.append('caption_font_size', document.getElementById('caption-fontsize')?.value || '22');
    formData.append('caption_color',     document.getElementById('caption-color')?.value || 'white');
    formData.append('caption_position',  document.getElementById('caption-position')?.value || 'bottom');
    // NOVO: estilo karaoke (palavra-por-palavra) + cor de destaque
    formData.append('caption_style',     document.getElementById('caption-style')?.value || 'standard');
    formData.append('caption_highlight', document.getElementById('caption-highlight')?.value || 'yellow');
  }
  formData.append('remove_bg',       document.getElementById('remove-bg-enable')?.checked || false);
  formData.append('normalize_audio', document.getElementById('normalize-audio')?.checked  || false);
  formData.append('trim_silence',    document.getElementById('trim-silence')?.checked !== false ? 'true' : 'false');
  formData.append('output_format',   document.getElementById('output-format')?.value      || 'landscape');

  // Phase 4 — music, fade, export, enhance, chroma, translate, template vars
  const musicUrl = document.getElementById('music-select')?.value || '';
  if (musicUrl) {
    formData.append('music_url',    musicUrl);
    formData.append('music_volume', document.getElementById('music-volume')?.value || '0.12');
    formData.append('music_auto_duck', document.getElementById('music-auto-duck')?.checked ? 'true' : 'false');
  }
  if (document.getElementById('fade-enable')?.checked) {
    formData.append('enable_fade', 'true');
    formData.append('fade_in',  document.getElementById('fade-in')?.value  || '0.5');
    formData.append('fade_out', document.getElementById('fade-out')?.value || '0.5');
  }
  const exportFmt = document.getElementById('export-format')?.value || '';
  if (exportFmt) formData.append('export_format', exportFmt);
  if (document.getElementById('enhance-image')?.checked) formData.append('enhance_image', 'true');
  formData.append('enhance_face', document.getElementById('enhance-face')?.checked !== false ? 'true' : 'false');
  const chromaColor = document.getElementById('chroma-key-color')?.value || '';
  if (chromaColor) {
    formData.append('chroma_key',       chromaColor);
    formData.append('chroma_tolerance', document.getElementById('chroma-tolerance')?.value || '40');
  }
  const trLang = document.getElementById('caption-translate-lang')?.value || '';
  if (trLang) formData.append('caption_translate', trLang);
  const tmplVars = parseTemplateVars();
  if (tmplVars) formData.append('template_vars', JSON.stringify(tmplVars));

  // Gesture template mode
  const gestureVideo = document.getElementById('gesture-video-select')?.value || '';
  if (gestureVideo) formData.append('gesture_video', gestureVideo);

  if (audioInput.files && audioInput.files[0]) {
    formData.append('audio', audioInput.files[0]);
  }

  document.getElementById('result').style.display        = 'none';
  document.getElementById('progress-area').style.display = 'block';
  document.getElementById('progress-fill').style.width   = '5%';
  document.getElementById('progress-status').textContent = 'Iniciando geração...';
  document.getElementById('progress-text').textContent   = 'Enviando arquivos...';

  try {
    const r = await fetch('/api/generate', { method: 'POST', body: formData });
    if (!r.ok) { let errMsg = `Erro ${r.status}`; try { const ed = await r.json(); errMsg = ed.error || errMsg; } catch(_) {} toast(errMsg, 'error'); document.getElementById('progress-area').style.display = 'none'; return; }
    const d = await r.json();
    if (d.error) {
      toast(d.error, 'error');
      document.getElementById('progress-area').style.display = 'none';
      return;
    }
    // Mostra ETA inicial + warnings se vieram do servidor (HeyGen-style UX)
    if (d.estimated_minutes_text || d.warning) {
      let msg = `Job enfileirado`;
      if (d.estimated_minutes_text) msg += ` — ETA inicial: ${d.estimated_minutes_text}`;
      if (d.warning) msg += `. ⚠️ ${d.warning}`;
      toast(msg, d.warning ? 'warning' : 'info');
    }
    pollJob(d.job_id);
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
    document.getElementById('progress-area').style.display = 'none';
  }
}

let _activeJobId = null;

async function cancelCurrentJob() {
  if (!_activeJobId) return;
  const btn = document.getElementById('btn-cancel-job');
  if (btn) { btn.disabled = true; btn.textContent = 'Cancelando...'; }
  try {
    const r = await fetch(`/api/job/${_activeJobId}/cancel`, { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      toast('Job cancelado.', 'info');
      clearInterval(pollingInterval);
      document.getElementById('progress-text').textContent = 'Cancelado pelo usuário.';
      document.getElementById('progress-fill').style.background = 'var(--dim)';
      document.getElementById('progress-status').textContent = '⛔ Cancelado';
      if (btn) { btn.style.display = 'none'; }
      _activeJobId = null;
    } else {
      toast('Não foi possível cancelar: ' + (d.error || ''), 'error');
      if (btn) { btn.disabled = false; btn.textContent = '✕ Cancelar'; }
    }
  } catch (e) {
    toast('Erro ao cancelar: ' + e.message, 'error');
    if (btn) { btn.disabled = false; btn.textContent = '✕ Cancelar'; }
  }
}

function pollJob(jobId) {
  if (pollingInterval) clearInterval(pollingInterval);
  if (!jobId) { console.warn('pollJob called with empty jobId — stopping'); return; }
  _activeJobId = jobId;
  const cancelBtn = document.getElementById('btn-cancel-job');
  if (cancelBtn) { cancelBtn.style.display = 'inline-block'; cancelBtn.disabled = false; cancelBtn.textContent = '✕ Cancelar'; }
  const startTime  = Date.now();
  const MAX_MS     = 90 * 60 * 1000; // 90 minutos timeout máximo
  let   errorCount = 0;
  const MAX_ERRORS = 10; // tolerância a falhas de rede temporárias

  pollingInterval = setInterval(async () => {
    // Timeout de segurança — para não ficar em loop infinito
    if (Date.now() - startTime > MAX_MS) {
      clearInterval(pollingInterval);
      document.getElementById('progress-text').textContent = 'Timeout — verifique o servidor';
      document.getElementById('progress-fill').style.background = 'var(--red)';
      toast('Timeout após 90 min. Verifique o status do servidor.', 'error');
      return;
    }

    try {
      const r = await fetch('/api/job/' + jobId);
      // 404 com body JSON = job perdido (server reiniciou) — parar imediatamente
      if (r.status === 404) {
        let errMsg = 'Job não encontrado — servidor pode ter reiniciado.';
        try { const ed = await r.json(); errMsg = ed.error || errMsg; } catch(_){}
        clearInterval(pollingInterval);
        document.getElementById('progress-text').textContent = errMsg;
        document.getElementById('progress-fill').style.background = 'var(--red)';
        document.getElementById('progress-status').textContent = '❌ ' + errMsg;
        toast(errMsg, 'error');
        return;
      }
      if (!r.ok) { errorCount++; if (errorCount >= MAX_ERRORS) { clearInterval(pollingInterval); toast('Servidor inacessível. Recarregue a página.', 'error'); } return; }
      errorCount = 0;
      const d = await r.json();

      const statusMap = {
        'queued':           'Aguardando na fila...',
        'generating_audio': 'Gerando áudio de voz...',
        'generating_video': 'Rodando lip sync...',
        'compositing':      'Aplicando fundo & efeitos...',
        'done':             'Concluído!',
        'error':            'Erro',
        'cancelled':        'Cancelado',
      };

      const statusLabel = d.message || statusMap[d.status] || d.status;
      document.getElementById('progress-status').textContent = statusLabel;
      document.getElementById('progress-fill').style.width   = (d.progress || 0) + '%';
      // HeyGen-like: stage indicator + ETA + mensagem técnica.
      // ex: "✨ Qualidade (4/5) • 70% • ~3min 12s restantes — GFPGAN: restaurando qualidade (47%)..."
      const msg = d.message || '';
      let etaPart = '';
      const eta = Number(d.eta_seconds || 0);
      if (eta > 5) {
        const m = Math.floor(eta / 60), s = eta % 60;
        etaPart = m > 0 ? ` • ~${m}min ${s}s restantes` : ` • ~${s}s restantes`;
      }
      // mapear mensagem técnica -> etapa visual com icone
      let stage = null;
      const mL = msg.toLowerCase();
      if      (/aguardando|vaga|fila/.test(mL))                                                            stage = { i: '⏳', l: 'Na fila',     n: 0 };
      else if (/audio|voice|tts|edge[- ]?tts|eleven/.test(mL))                                             stage = { i: '🎙️', l: 'Áudio',      n: 1 };
      else if (/sadtalker|gesture pack|montando seq|face swap|movimento natural|seq[uû]ênc/.test(mL))     stage = { i: '🎬', l: 'Animação',   n: 2 };
      else if (/musetalk|wav2lip|sincroniz|lip sync/.test(mL))                                             stage = { i: '👄', l: 'Lip Sync',   n: 3 };
      else if (/gfpgan|restaurando|codeformer|enhance/.test(mL))                                           stage = { i: '✨', l: 'Qualidade',  n: 4 };
      else if (/hd final|compositing|encod|body sway|watermark|smooth motion|upscale|fade|export/.test(mL))stage = { i: '📺', l: 'Render HD',  n: 5 };
      else if (/done|concluí/.test(mL))                                                                    stage = { i: '✅', l: 'Pronto',     n: 5 };
      const stageStr = stage ? `${stage.i} ${stage.l} (${stage.n}/5) • ` : '';
      document.getElementById('progress-text').textContent = `${stageStr}${d.progress || 0}%${etaPart} — ${msg}`;

      const elapsed = Math.round((Date.now() - startTime) / 1000);
      const durEl   = document.getElementById('progress-duration');
      if (durEl) durEl.textContent = `Tempo: ${elapsed}s${d.audio_duration ? ' | Áudio: ' + d.audio_duration + 's' : ''}`;

      if (d.status === 'done') {
        clearInterval(pollingInterval);
        _activeJobId = null;
        const cancelBtn = document.getElementById('btn-cancel-job');
        if (cancelBtn) cancelBtn.style.display = 'none';
        document.getElementById('progress-area').style.display = 'none';
        document.getElementById('result').style.display        = 'block';
        // Usar encodeURIComponent para evitar XSS/path injection
        const safeFilename = encodeURIComponent(d.output_filename || '');
        document.getElementById('result-video').src   = '/outputs/' + safeFilename;
        document.getElementById('download-link').href = '/outputs/' + safeFilename;
        document.getElementById('download-link').download = d.output_filename || 'avatar.mp4';
        // SRT download button (if captions were generated)
        const srtRow = document.getElementById('srt-download-row');
        if (srtRow) {
          if (d.srt_path) {
            const srtFilename = encodeURIComponent(d.srt_path.split(/[\\/]/).pop());
            document.getElementById('srt-download-link').href = '/outputs/' + srtFilename;
            srtRow.style.display = 'flex';
          } else {
            srtRow.style.display = 'none';
          }
        }
        loadHistory();
        toast('Vídeo pronto!', 'success');
        // HeyGen-like: notificacao do browser + chime de audio.
        // Permite ao usuario submeter um job longo e sair — sera avisado quando pronto.
        notifyJobDone(d.output_filename || 'Seu vídeo está pronto');
      }
      if (d.status === 'error') {
        clearInterval(pollingInterval);
        _activeJobId = null;
        const cancelBtn = document.getElementById('btn-cancel-job');
        if (cancelBtn) cancelBtn.style.display = 'none';
        document.getElementById('progress-text').textContent    = 'Erro: ' + (d.error || 'Desconhecido');
        document.getElementById('progress-fill').style.background = 'var(--red)';
        toast('Erro: ' + (d.error || 'Desconhecido'), 'error');
      }
      if (d.status === 'cancelled') {
        clearInterval(pollingInterval);
        _activeJobId = null;
        const cancelBtn = document.getElementById('btn-cancel-job');
        if (cancelBtn) cancelBtn.style.display = 'none';
        document.getElementById('progress-status').textContent = '⛔ Cancelado';
        document.getElementById('progress-text').textContent   = 'Job cancelado pelo usuário.';
        document.getElementById('progress-fill').style.background = 'var(--dim)';
      }
    } catch (e) {
      errorCount++;
      console.error('Poll error:', e);
      if (errorCount >= MAX_ERRORS) {
        clearInterval(pollingInterval);
        toast('Conexão com servidor perdida. Recarregue a página.', 'error');
      }
    }
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
    gallery.innerHTML = html || '<p style="color:var(--dim);padding:20px">Nenhum fundo enviado ainda.</p>';

    sel.innerHTML = '<option value="">Sem fundo (original)</option>';
    (d.backgrounds || []).forEach(bg => {
      _addOption(sel, bg.name, bg.name);
    });
  } catch (e) { console.error(e); }
}

function selectBg(name) {
  document.getElementById('bg-select').value = name;
  toast(`Fundo selecionado: ${name}`, 'success');
  showPage('create');
}

async function uploadBackground(input) {
  if (!input.files || !input.files[0]) return;
  const formData = new FormData();
  formData.append('file', input.files[0]);
  loading(true, 'Enviando fundo...');
  try {
    await fetch('/api/backgrounds/upload', { method: 'POST', body: formData });
    loadBackgrounds();
    toast('Fundo enviado!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { loading(false); }
}

// ============================================================================
// GESTURE TEMPLATE
// ============================================================================
async function loadGestureVideos() {
  try {
    const r = await fetch('/api/gesture_videos');
    const d = await r.json();
    const sel = document.getElementById('gesture-video-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Sem gesture (SadTalker padrão) —</option>';
    (d.gesture_videos || []).forEach(gv => {
      const opt = document.createElement('option');
      opt.value = gv.name;
      opt.textContent = `${gv.label} (${gv.duration}s)`;
      sel.appendChild(opt);
    });
  } catch (e) { console.error('loadGestureVideos:', e); }
}

function previewGestureVideo(name) {
  const previewDiv = document.getElementById('gesture-preview');
  const previewVid = document.getElementById('gesture-preview-video');
  if (!name) {
    previewDiv.style.display = 'none';
    return;
  }
  previewVid.src = '/static/gesture_videos/' + encodeURIComponent(name);
  previewDiv.style.display = 'block';
  previewVid.load();
}

async function uploadGestureVideo(input) {
  if (!input.files || !input.files[0]) return;
  const status = document.getElementById('gesture-upload-status');
  if (status) status.textContent = 'Enviando...';
  const formData = new FormData();
  formData.append('file', input.files[0]);
  try {
    const r = await fetch('/api/gesture_videos/upload', { method: 'POST', body: formData });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    await loadGestureVideos();
    // Auto-select the uploaded video
    const sel = document.getElementById('gesture-video-select');
    if (sel && d.name) { sel.value = d.name; previewGestureVideo(d.name); }
    toast('Gesture video enviado!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (status) status.textContent = ''; input.value = ''; }
}

// ============================================================================
// VOICES PAGE
// ============================================================================
async function loadVoiceList() {
  loading(true, 'Carregando vozes...');
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
      `<div style="margin-bottom:8px;font-size:12px;color:var(--dim)">${count} vozes encontradas</div>` + html;
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { loading(false); }
}

function selectVoice(name) {
  const sel = document.getElementById('voice-select');
  let found = false;
  for (let opt of sel.options) { if (opt.value === name) { found = true; break; } }
  if (!found) _addOption(sel, name, name);
  sel.value = name;
  toast(`Voz selecionada: ${name}`, 'success');
  showPage('create');
}

async function testVoice(name) {
  loading(true, 'Testando voz...');
  try {
    const r = await fetch('/api/preview_audio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script: 'Olá! Este é um teste desta voz. Como está o som?', voice: name, engine: 'edge-tts' })
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
  grid.innerHTML = '<div style="color:var(--dim);padding:20px">Carregando...</div>';

  try {
    const r = await fetch('/api/history');
    const d = await r.json();
    stats.textContent = `${d.total} videos · ${d.total_size_mb} MB used`;

    if (!d.videos || d.videos.length === 0) {
      grid.innerHTML = '<div style="color:var(--dim);padding:40px;text-align:center">Nenhum vídeo gerado ainda.<br>Vá para Criar para fazer seu primeiro avatar!</div>';
      return;
    }

    grid.innerHTML = d.videos.map(v => {
      const date    = new Date(v.created).toLocaleString('pt-BR');
      const thumbEl = v.thumbnail
        ? `<img src="${v.thumbnail}?t=${Date.now()}" class="history-thumb" loading="lazy" onerror="this.style.display='none';this.nextElementSibling&&(this.nextElementSibling.style.display='flex')"><div class="history-thumb-placeholder" style="display:none">🎭</div>`
        : `<div class="history-thumb-placeholder">🎭</div>`;
      const dur     = v.duration ? `${v.duration}s` : '—';
      const size    = v.size_mb ? `${v.size_mb} MB` : '—';
      const plan    = v.plan ? `<span style="font-size:10px;background:var(--accent2);color:#fff;border-radius:4px;padding:1px 6px;margin-left:4px">${v.plan}</span>` : '';
      return `
      <div class="history-card" id="hcard-${v.id}">
        <div class="history-thumb-wrap" onclick="previewVideo('${_esc(v.filename)}','${_esc(v.id)}')">${thumbEl}<div class="history-play">▶</div></div>
        <div class="history-info">
          <div class="history-filename">${_esc(v.filename || v.id)}${plan}</div>
          <div class="history-meta">${date} · ${dur} · ${size} · ${_esc(v.voice || '—')}</div>
          ${v.script_preview ? `<div class="history-script">"${_esc(v.script_preview)}"</div>` : ''}
        </div>
        <div class="history-actions">
          <a href="/outputs/${_esc(v.filename)}" download class="btn-primary" style="padding:6px 12px;font-size:12px" title="Download">⬇️</a>
          <button class="btn-secondary" style="padding:6px 12px;font-size:12px" onclick="reuseHistoryScript('${_esc(v.script_preview || '')}')">🔁</button>
          <button class="btn-secondary" style="padding:6px 12px;font-size:12px;color:#10b981;border-color:#10b981" title="Upscale para HD 1280×720" onclick="hdUpscaleVideo('${_esc(v.filename)}','${_esc(v.id)}')">⬆️HD</button>
          <button class="btn-secondary" style="padding:6px 12px;font-size:12px;color:#a78bfa;border-color:#a78bfa" title="Melhorar qualidade do rosto (GFPGAN)" onclick="enhanceHistoryVideo('${_esc(v.filename)}','${_esc(v.id)}')">✨</button>
          <button class="btn-secondary" style="padding:6px 12px;font-size:12px;color:var(--red);border-color:var(--red)" onclick="deleteVideo('${_esc(v.id)}')">🗑️</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    grid.innerHTML = `<div style="color:var(--red);padding:20px">Erro ao carregar histórico: ${e.message}</div>`;
  }
}

function previewVideo(filename, id) {
  const existing = document.getElementById('modal-player');
  if (existing) existing.remove();
  const safeSrc  = '/outputs/' + encodeURIComponent(filename);
  const overlay  = document.createElement('div');
  overlay.id = 'modal-player';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  // Construir via DOM para evitar XSS
  const card = document.createElement('div');
  card.style.cssText = 'background:var(--card);border-radius:16px;padding:20px;max-width:780px;width:95%';
  const video = document.createElement('video');
  video.src = safeSrc; video.controls = true; video.autoplay = true;
  video.style.cssText = 'width:100%;border-radius:10px';
  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex;gap:10px;margin-top:12px;justify-content:center';
  const dlBtn = document.createElement('a');
  dlBtn.href = safeSrc; dlBtn.download = filename; dlBtn.className = 'btn-primary'; dlBtn.textContent = '⬇️ Download';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'btn-secondary'; closeBtn.textContent = '✕ Fechar';
  closeBtn.onclick = () => overlay.remove();
  btnRow.append(dlBtn, closeBtn);
  card.append(video, btnRow);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
}

async function enhanceHistoryVideo(filename, videoId) {
  if (!confirm('Aplicar GFPGAN neste vídeo? Isso pode levar alguns minutos.')) return;
  toast('Aplicando GFPGAN... aguarde', 'success');
  const card = document.getElementById(`hcard-${videoId}`);
  if (card) card.style.opacity = '0.5';
  try {
    const r = await fetch('/api/tools/enhance_video', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    });
    const d = await r.json();
    if (d.error) { toast('Erro: ' + d.error, 'error'); return; }
    toast(`Vídeo melhorado! ${d.size_mb} MB`, 'success');
    // Auto-download enhanced
    const a = document.createElement('a');
    a.href = d.url; a.download = d.filename; a.click();
    loadHistory();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (card) card.style.opacity = '1'; }
}

async function hdUpscaleVideo(filename, videoId) {
  toast('Upscale HD iniciado... (pode levar minutos para vídeos longos)', 'info');
  const card = document.getElementById(`hcard-${videoId}`);
  if (card) card.style.opacity = '0.6';
  try {
    const r = await fetch('/api/tools/hd_upscale', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename })
    });
    const d = await r.json();
    if (d.error) { toast('Erro: ' + d.error, 'error'); return; }
    // Poll for completion
    const jobId = d.job_id;
    toast('Processando HD em background... aguarde', 'info');
    const poll = setInterval(async () => {
      try {
        const sr = await fetch(`/api/tools/hd_upscale/${jobId}`);
        const sd = await sr.json();
        if (sd.status === 'done') {
          clearInterval(poll);
          if (card) card.style.opacity = '1';
          toast(`HD pronto! ${sd.size_mb} MB — 1280×720`, 'success');
          const a = document.createElement('a');
          a.href = sd.url; a.download = sd.filename; a.click();
          loadHistory();
        } else if (sd.status === 'error') {
          clearInterval(poll);
          if (card) card.style.opacity = '1';
          toast('Upscale falhou: ' + (sd.error || 'erro desconhecido'), 'error');
        }
      } catch (_) {}
    }, 5000);
  } catch (e) {
    if (card) card.style.opacity = '1';
    toast('Erro: ' + e.message, 'error');
  }
}

async function deleteVideo(videoId) {
  if (!confirm('Excluir este vídeo?')) return;
  try {
    await fetch(`/api/history/${videoId}`, { method: 'DELETE' });
    document.getElementById(`hcard-${videoId}`)?.remove();
    toast('Vídeo excluído', 'success');
    loadHistory();
  } catch (e) { toast('Erro ao excluir: ' + e.message, 'error'); }
}

async function clearHistory() {
  if (!confirm('Excluir TODO o histórico? Isso não pode ser desfeito.')) return;
  try {
    await fetch('/api/history/clear', { method: 'POST' });
    toast('Histórico limpo', 'success');
    loadHistory();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

function reuseHistoryScript(script) {
  showPage('create');
  const el = document.getElementById('script');
  if (el) {
    el.value = script.replace(/\.\.\.$/,'');
    el.dispatchEvent(new Event('input'));
  }
  toast('Script carregado no Create!', 'success');
}

// ============================================================================
// AI FEATURES
// ============================================================================
function openAIModal()  { document.getElementById('ai-modal').style.display = 'flex'; }
function closeAIModal() { document.getElementById('ai-modal').style.display = 'none'; }

async function generateAIScript() {
  const topic  = document.getElementById('ai-topic').value.trim();
  if (!topic) { toast('Digite um tópico primeiro', 'warn'); return; }

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
    toast('Roteiro gerado!', 'success');
  } catch (e) { toast('AI Error: ' + e.message, 'error'); }
  finally { document.getElementById('ai-loading').style.display = 'none'; }
}

async function enhanceScript() {
  const script = document.getElementById('script').value.trim();
  if (!script) { toast('Escreva um roteiro primeiro', 'warn'); return; }
  loading(true, 'A IA está aprimorando seu roteiro...');
  try {
    const r = await fetch('/api/ai/enhance_script', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script, goal: 'make it more natural, engaging, and clear for video narration' })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    document.getElementById('script').value = d.script;
    updateScriptStats();
    toast('Roteiro aprimorado!', 'success');
  } catch (e) { toast('AI Error: ' + e.message, 'error'); }
  finally { loading(false); }
}

async function suggestVoice() {
  const script = document.getElementById('script').value.trim();
  if (!script) { toast('Escreva um roteiro primeiro', 'warn'); return; }
  loading(true, 'A IA está sugerindo uma voz...');
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
      if (!found) _addOption(sel, d.voice, d.voice);
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
  if (!prompt) { toast('Descreva seu avatar primeiro', 'warn'); return; }

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
    toast('Imagem gerada!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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
    toast('Imagem de IA definida como avatar!', 'success');
  } catch (e) { toast('Erro ao carregar imagem: ' + e.message, 'error'); }
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
      _addOption(sel, v.id, v.name + cat);
    });
    toast(`${d.total} ElevenLabs voices loaded`, 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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
    if (!found) _addOption(sel, id, name);
    sel.value = id;
  }
  toast(`ElevenLabs voice selected: ${name}`, 'success');
  showPage('create');
  // Switch to ElevenLabs engine
  const tab = document.querySelector('.engine-tab:nth-child(2)');
  if (tab) setEngine('elevenlabs', tab);
}

async function testElevenVoice(voiceId) {
  loading(true, 'Testando voz ElevenLabs...');
  try {
    const r = await fetch('/api/preview_audio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        script: 'Olá! Este é um teste desta voz ElevenLabs. Como está o som?',
        engine: 'elevenlabs', voice_id: voiceId
      })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    new Audio(d.audio_url).play();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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

  if (!name)          { toast('Digite um nome para a voz', 'warn'); return; }
  if (!files || files.length === 0) { toast('Envie pelo menos uma amostra de áudio', 'warn'); return; }

  loading(true, 'Clonando voz com ElevenLabs... (pode levar 30-60s)');
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
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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
      _addOption(sel, t.id, t.name);
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
  // still-mode é hidden input (não checkbox) — usar .value
  if (s.still_mode !== undefined) { const el = document.getElementById('still-mode'); if (el) el.value = s.still_mode ? 'true' : 'false'; }
  const _expVal = document.getElementById('exp-val'); if (_expVal) _expVal.textContent = s.expression_scale || 1.0;
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
  if (!name) { toast('Digite um nome para o template', 'warn'); return; }
  try {
    await fetch('/api/templates/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        voice:            document.getElementById('voice-select').value,
        engine:           currentEngine,
        preprocess:       document.getElementById('preprocess').value,
        still_mode:       document.getElementById('still-mode').value === 'true',
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
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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

  if (rows.length === 0) { toast('Adicione pelo menos um trabalho', 'warn'); return; }

  // Build jobs array — batch API accepts JSON, so we use base64 for images
  const jobs = [];
  for (const row of rows) {
    const script    = row.querySelector('.batch-script')?.value?.trim();
    const imgFile   = row.querySelector('.batch-image')?.files?.[0];
    const sharedImg = imgInput?.files?.[0];
    const imgSrc    = imgFile || sharedImg;

    if (!script) continue;
    if (!imgSrc) { toast('Envie uma imagem (compartilhada ou por trabalho)', 'warn'); return; }

    // Convert image to base64
    const imgB64 = await fileToBase64(imgSrc);
    jobs.push({ script, voice, size, image_base64: imgB64, preprocess: 'crop', enhancer: 'gfpgan' });
  }

  if (jobs.length === 0) { toast('Nenhum trabalho válido para executar', 'warn'); return; }

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
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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
  let _batchErrCount = 0;

  batchPollIv = setInterval(async () => {
    try {
      const r  = await fetch('/api/batch/status', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: jobIds })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
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
        toast('Todos os trabalhos em lote concluídos!', 'success');
      }
    } catch (e) {
      _batchErrCount++;
      console.error('Batch poll error:', e);
      if (_batchErrCount >= 5) {
        clearInterval(batchPollIv);
        toast('Polling do lote parou — servidor inacessível. Verifique os logs.', 'error');
      }
    }
  }, 3000);
}

// ============================================================================
// SETTINGS
// ============================================================================
async function saveElevenKey() {
  const key = document.getElementById('eleven-key').value.trim();
  if (!key) { toast('Cole sua chave de API', 'warn'); return; }
  try {
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ elevenlabs_key: key })
    });
    toast('Chave ElevenLabs salva!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

async function saveGroqKey() {
  const key = document.getElementById('groq-key-input')?.value.trim();
  if (!key) { toast('Cole sua chave Groq (gsk_...)', 'warn'); return; }
  try {
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ groq_key: key })
    });
    const badge = document.getElementById('groq-key-badge');
    if (badge) {
      badge.textContent = '✅ Configurada';
      badge.style.background = 'rgba(16,185,129,0.15)';
      badge.style.color = '#34d399';
    }
    document.getElementById('groq-key-input').value = '';
    document.getElementById('groq-key-input').placeholder = '***' + key.slice(-4) + ' (salva)';
    toast('Chave Groq salva! Funções de IA agora disponíveis.', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

async function savePlan() {
  const plan = document.getElementById('plan-select').value;
  try {
    await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan })
    });
    currentPlan = plan;
    document.getElementById('plan-badge').textContent = 'Plano: ' +plan;
    updateScriptStats();
    toast(`Plan set to: ${plan}`, 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

async function loadSettingsPage() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    currentPlan = d.plan || 'unlimited';
    const planSel = document.getElementById('plan-select');
    if (planSel) planSel.value = currentPlan;
    document.getElementById('plan-badge').textContent = 'Plano: ' +currentPlan;
    // Cloud GPU
    const execSel = document.getElementById('executor-select');
    if (execSel) {
      execSel.value = d.executor || 'local';
      _updateCloudSections(d.executor || 'local');
    }
    if (d.replicate_key_set) {
      const kEl = document.getElementById('replicate-key');
      if (kEl) kEl.placeholder = d.replicate_key + ' (saved)';
    }
    const groqBadge = document.getElementById('groq-key-badge');
    if (groqBadge) {
      if (d.groq_key_set) {
        groqBadge.textContent = '✅ Configurada';
        groqBadge.style.background = 'rgba(16,185,129,0.15)';
        groqBadge.style.color = '#34d399';
        const groqInput = document.getElementById('groq-key-input');
        if (groqInput) groqInput.placeholder = d.groq_key + ' (salva)';
      } else {
        groqBadge.textContent = '⚠️ Não configurada';
        groqBadge.style.background = 'rgba(239,68,68,0.15)';
        groqBadge.style.color = '#f87171';
      }
    }
    // Watermark
    const wmText = document.getElementById('wm-text');
    const wmPos  = document.getElementById('wm-pos');
    const wmCol  = document.getElementById('wm-color');
    if (wmText) wmText.value = d.watermark_text || '';
    if (wmPos)  wmPos.value  = d.watermark_pos  || 'bottom_right';
    if (wmCol)  wmCol.value  = d.watermark_color || 'white';
  } catch (e) {}
  loadWebhooks();
}

async function saveWatermark() {
  const text  = document.getElementById('wm-text')?.value || '';
  const pos   = document.getElementById('wm-pos')?.value  || 'bottom_right';
  const color = document.getElementById('wm-color')?.value || 'white';
  try {
    const r = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ watermark_text: text, watermark_pos: pos, watermark_color: color })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro');
    const st = document.getElementById('wm-status');
    if (st) {
      st.style.color = 'var(--green)';
      st.textContent = text ? `Marca d'agua salva: "${text}"` : 'Marca d\'agua desativada.';
    }
    toast(text ? `Marca d'agua configurada!` : 'Marca d\'agua removida.', 'success');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

// ============================================================================
// MODEL CHECK + VALIDATE
// ============================================================================
async function checkModels() {
  try {
    const r  = await fetch('/api/check_models');
    const d  = await r.json();
    const sidebarEl  = document.getElementById('model-status');          // sidebar
    const settingsEl = document.getElementById('model-status-settings'); // settings page
    const infoEl     = document.getElementById('model-info');

    const dash = await fetch('/api/dashboard').then(x => x.json()).catch(() => ({}));
    const mst  = dash.musetalk  || {};
    const w2l  = dash.wav2lip   || {};
    const sad  = dash.sadtalker || {};

    let label, color, info;
    if (mst.ready) {
      label = '✅ MuseTalk 1.5 + SadTalker prontos';
      color = 'var(--green)';
      info  = `<div style="font-size:12px;color:var(--green);margin-bottom:4px">MuseTalk 1.5: engine primário (qualidade HeyGen)</div>
               <div style="font-size:12px;color:var(--dim)">SadTalker: ${sad.ready ? '✅ disponível (animação corpo)' : '❌ não instalado'}</div>
               <div style="font-size:12px;color:var(--dim)">Wav2Lip: ${w2l.ready ? '✅ fallback' : '❌ não instalado'}</div>
               <div style="font-size:12px;color:var(--dim)">GPU: ${dash.gpu?.name || 'CPU'} | VRAM livre: ${dash.gpu?.vram_free || '?'} GB</div>`;
    } else if (w2l.ready) {
      label = '⚡ Wav2Lip + SadTalker prontos';
      color = 'var(--gold)';
      info  = `<div style="font-size:12px;color:var(--gold);margin-bottom:4px">Wav2Lip: engine principal ativo</div>
               <div style="font-size:12px;color:var(--dim)">SadTalker: ${sad.ready ? '✅ disponível (movimento)' : '❌ não instalado'}</div>
               <div style="font-size:12px;color:var(--dim)">GPU: ${dash.gpu?.name || 'CPU'} | VRAM livre: ${dash.gpu?.vram_free || '?'} GB</div>`;
    } else if (sad.ready) {
      label = '⚡ SadTalker pronto (fallback)';
      color = 'var(--gold)';
      info  = '<div style="color:var(--gold);font-size:12px">Wav2Lip não detectado — usando SadTalker como primário</div>';
    } else {
      label = '❌ Nenhum engine detectado';
      color = 'var(--red)';
      info  = '<div style="color:var(--red);font-size:13px">Nem MuseTalk, Wav2Lip nem SadTalker detectados.</div>';
    }

    if (sidebarEl)  { sidebarEl.textContent  = label; sidebarEl.style.color  = color; }
    if (settingsEl) { settingsEl.textContent  = label; settingsEl.style.color = color; }
    if (infoEl)     infoEl.innerHTML = info;
  } catch (e) { console.error(e); }
}

async function validateSadTalker() {
  loading(true, 'Executando teste completo do SadTalker (pode levar 2-3 min)...');
  try {
    const r = await fetch('/api/validate_sadtalker', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      toast(`SadTalker OK! Output: ${d.output_duration}s video`, 'success');
    } else {
      toast(`SadTalker test failed: ${d.error}`, 'error');
    }
  } catch (e) { toast('Erro no teste: ' + e.message, 'error'); }
  finally { loading(false); }
}

async function installSadTalker() {
  loading(true, 'Instalando SadTalker... Isso pode levar alguns minutos.');
  try {
    const r = await fetch('/api/install_sadtalker', { method: 'POST' });
    const d = await r.json();
    toast(d.message || 'Instalação iniciada', 'info');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
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
    const diskFree = d.disk_free_mb || 0;
    const diskWarn = diskFree < 500 ? `<div style="margin-top:8px;padding:8px 12px;background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.4);border-radius:8px;font-size:12px;color:#f87171">⚠️ Disco quase cheio: apenas ${diskFree.toLocaleString()}MB livres. Apague vídeos antigos.</div>` : '';

    el.innerHTML = `
      ${diskWarn}
      <div class="dashboard-grid">
        <div class="dash-card"><div class="dash-val">${d.total_generated}</div><div class="dash-label">Vídeos Gerados</div></div>
        <div class="dash-card"><div class="dash-val">${d.total_hours}h</div><div class="dash-label">Conteúdo Total</div></div>
        <div class="dash-card"><div class="dash-val">${d.disk_used_mb} MB</div><div class="dash-label">Disco Usado</div></div>
        <div class="dash-card"><div class="dash-val" style="${diskFree < 500 ? 'color:var(--red)' : ''}">${diskFree.toLocaleString()} MB</div><div class="dash-label">Disco Livre</div></div>
      </div>
      <div style="margin-top:14px">
        <div style="font-size:12px;color:var(--dim);margin-bottom:8px">GPU</div>
        <div style="font-size:13px">${gpu.available ? '✅ ' + gpu.name : '❌ Sem GPU'}</div>
        ${gpu.vram_total ? `<div style="font-size:12px;color:var(--dim);margin-top:4px">VRAM: ${gpu.vram_used||0}GB / ${gpu.vram_total}GB used${vramBar}</div>` : ''}
      </div>
      <div style="margin-top:12px">
        <div style="font-size:12px;color:var(--dim);margin-bottom:6px">Services</div>
        <div style="font-size:12px">MuseTalk 1.5: ${d.musetalk?.ready ? '✅ Pronto (primário)' : '❌ Não encontrado'}</div>
        <div style="font-size:12px;margin-top:4px">Wav2Lip: ${d.wav2lip?.ready ? '✅ Pronto (fallback)' : '❌ Não encontrado'}</div>
        <div style="font-size:12px;margin-top:4px">SadTalker: ${d.sadtalker?.ready ? '✅ Pronto (animação)' : '❌ Não instalado'}</div>
        <div style="font-size:12px;margin-top:4px">Edge-TTS: ${d.edge_tts ? '✅ Ready' : '❌ Missing'}</div>
        <div style="font-size:12px;margin-top:4px">Plano: <strong>${d.plan}</strong> (limite: ${d.plan_limits?.[d.plan] ? d.plan_limits[d.plan] + ' min' : 'ilimitado'})</div>
      </div>
      <button class="btn-secondary" onclick="loadDashboard()" style="margin-top:14px;font-size:12px">🔄 Refresh</button>`;
  } catch (e) {
    el.innerHTML = `<div style="color:var(--red);font-size:13px">Erro: ${e.message}</div><button class="btn-secondary" onclick="loadDashboard()">Tentar novamente</button>`;
  }
}

// ============================================================================
// VOICE PRESETS (Task 9)
// ============================================================================
async function loadVoicePresets() {
  try {
    const r = await fetch('/api/voice_presets');
    const d = await r.json();
    const sel = document.getElementById('voice-preset-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">— Custom (use voice selection below) —</option>';
    const cats = {};
    (d.presets || []).forEach(p => {
      cats[p.category] = cats[p.category] || [];
      cats[p.category].push(p);
    });
    for (const [cat, presets] of Object.entries(cats)) {
      sel.innerHTML += `<optgroup label="${cat}">`;
      presets.forEach(p => {
        _addOption(sel, p.id, `${p.description} (${p.edge_voice.split('-').pop()})`);
      });
      sel.innerHTML += '</optgroup>';
    }
  } catch (e) { console.error('Presets load error:', e); }
}

function applyVoicePreset(presetId) {
  const descEl = document.getElementById('preset-desc');
  if (!presetId) {
    if (descEl) descEl.textContent = '';
    return;
  }
  fetch('/api/voice_presets/' + presetId).then(r => r.json()).then(p => {
    if (p.error) return;
    // Set voice
    const sel = document.getElementById('voice-select');
    let found = false;
    for (let o of sel.options) { if (o.value === p.edge_voice) { found = true; break; } }
    if (!found) _addOption(sel, p.edge_voice, p.edge_voice);
    sel.value = p.edge_voice;
    // Show description
    if (descEl) {
      descEl.textContent = `${p.description} · Rate: ${p.rate} · Pitch: ${p.pitch} · Best for: ${(p.best_for||[]).join(', ')}`;
    }
    toast(`Preset applied: ${p.description}`, 'success');
  }).catch(() => {});
}

async function detectLanguage() {
  const script = document.getElementById('script').value.trim();
  if (!script) { toast('Escreva um roteiro primeiro', 'warn'); return; }
  try {
    const r = await fetch('/api/ai/detect_voice', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ script })
    });
    const d = await r.json();
    if (d.voice) {
      const sel = document.getElementById('voice-select');
      let found = false;
      for (let o of sel.options) { if (o.value === d.voice) { found = true; break; } }
      if (!found) _addOption(sel, d.voice, d.voice);
      sel.value = d.voice;
      toast(`Voz detectada automaticamente: ${d.voice}`, 'success');
    }
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

// ============================================================================
// CLOUD GPU SETTINGS
// ============================================================================
function _updateCloudSections(executor) {
  const hfSec  = document.getElementById('huggingface-section');
  const repSec = document.getElementById('replicate-section');
  if (hfSec)  hfSec.style.display  = executor === 'huggingface' ? 'block' : 'none';
  if (repSec) repSec.style.display = executor === 'replicate'   ? 'block' : 'none';
}

async function saveExecutor() {
  const executor = document.getElementById('executor-select').value;
  _updateCloudSections(executor);
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ executor })
  });
  const labels = { local: 'Local GPU 🖥️', huggingface: 'HuggingFace ZeroGPU 🤗 (grátis)', replicate: 'Replicate A100 ☁️' };
  toast(`Executor: ${labels[executor] || executor}`, 'success');
}

async function saveReplicateKey() {
  const key = document.getElementById('replicate-key').value.trim();
  if (!key) { toast('Cole sua chave de API do Replicate', 'warn'); return; }
  await fetch('/api/settings', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ replicate_key: key })
  });
  toast('Chave Replicate salva!', 'success');
}

async function testCloudGPU() {
  // Show spinner in whichever result div is visible
  const elHF  = document.getElementById('cloud-test-result');
  const elRep = document.getElementById('cloud-test-result-replicate');
  const el    = elHF || elRep;
  if (elHF)  elHF.innerHTML  = '<span style="color:var(--dim)">Testing...</span>';
  if (elRep) elRep.innerHTML = '<span style="color:var(--dim)">Testing...</span>';
  try {
    const r = await fetch('/api/cloud/test', { method: 'POST' });
    const d = await r.json();
    if (d.ok) {
      let msg = '';
      if (d.space) {
        // HuggingFace response
        msg = `✅ HuggingFace OK — ${d.space} · ${d.gpu}`;
        if (d.endpoints && d.endpoints.length) msg += ` · endpoints: ${d.endpoints.join(', ')}`;
      } else {
        msg = `✅ Cloud GPU ready — ${d.model} · ${d.gpu} · v${d.version}`;
      }
      if (elHF)  elHF.innerHTML  = `<span style="color:var(--green)">${msg}</span>`;
      if (elRep) elRep.innerHTML = `<span style="color:var(--green)">${msg}</span>`;
      toast('GPU na nuvem OK!', 'success');
    } else {
      const msg = `❌ ${d.error}`;
      if (elHF)  elHF.innerHTML  = `<span style="color:var(--red)">${msg}</span>`;
      if (elRep) elRep.innerHTML = `<span style="color:var(--red)">${msg}</span>`;
      toast('Erro na GPU na nuvem: ' + d.error, 'error');
    }
  } catch (e) { toast('Erro no teste: ' + e.message, 'error'); }
}

// ============================================================================
// WEBHOOKS (Task 10)
// ============================================================================
async function loadWebhooks() {
  const el = document.getElementById('webhooks-list');
  if (!el) return;
  try {
    const r = await fetch('/api/webhooks');
    const d = await r.json();
    if (!d.webhooks || d.webhooks.length === 0) {
      el.innerHTML = '<span style="color:var(--dim)">Nenhum webhook cadastrado.</span>';
      return;
    }
    el.innerHTML = d.webhooks.map(w => `
      <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
        <span style="flex:1;font-size:12px;word-break:break-all">${w.url}</span>
        <span style="font-size:10px;color:var(--dim)">${w.global ? 'global' : 'job:'+w.job_id}</span>
        <button onclick="deleteWebhook(${w.id})" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:14px">✕</button>
      </div>`).join('');
  } catch (e) { if (el) el.textContent = 'Erro: ' + e.message; }
}

async function registerWebhook() {
  const url = document.getElementById('webhook-url').value.trim();
  if (!url || !url.startsWith('http')) { toast('URL válida necessária (http...)', 'warn'); return; }
  try {
    await fetch('/api/webhooks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, global: true })
    });
    document.getElementById('webhook-url').value = '';
    loadWebhooks();
    toast('Webhook registrado!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

async function deleteWebhook(id) {
  try {
    await fetch('/api/webhooks/' + id, { method: 'DELETE' });
    loadWebhooks();
    toast('Webhook removido', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

// ============================================================================
// INIT
// ============================================================================
async function init() {
  try {
    const r = await fetch('/api/settings');
    const d = await r.json();
    currentPlan = d.plan || 'unlimited';
    document.getElementById('plan-badge').textContent = 'Plano: ' +currentPlan;
    // Restore executor setting
    const execSel = document.getElementById('executor-select');
    if (execSel) {
      execSel.value = d.executor || 'local';
      _updateCloudSections(d.executor || 'local');
    }
    updateScriptStats();
  } catch (e) {}
  checkModels();
  loadBackgrounds();
  loadGestureVideos();
  loadTemplates();
  loadVoicePresets();
  loadMusicLibrary();
}

init();

// ============================================================================
// GENERATE — captions toggle
// ============================================================================
function toggleKaraokeOptions() {
  const style = document.getElementById('caption-style')?.value || 'standard';
  const row = document.getElementById('caption-highlight-row');
  if (row) row.style.display = (style === 'karaoke') ? '' : 'none';
}

function toggleCaptionOptions() {
  const on = document.getElementById('captions-enable').checked;
  document.getElementById('caption-options').style.display = on ? 'block' : 'none';
  const trCard = document.getElementById('caption-translate-card');
  if (trCard) trCard.style.display = on ? 'block' : 'none';
  // Re-sincroniza karaoke row state quando captions ativa
  if (on) toggleKaraokeOptions();
}

// ============================================================================
// MUSIC LIBRARY
// ============================================================================
async function loadMusicLibrary() {
  try {
    const r = await fetch('/api/tools/music_library');
    const d = await r.json();
    const sel = document.getElementById('music-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">Sem música</option>';
    (d.tracks || []).forEach(t => {
      const o = document.createElement('option');
      o.value = t.url;
      o.textContent = t.name;
      sel.appendChild(o);
    });
    sel.onchange = () => {
      const row  = document.getElementById('music-volume-row');
      const dRow = document.getElementById('music-duck-row');
      const show = sel.value ? 'flex' : 'none';
      if (row)  row.style.display  = show;
      if (dRow) dRow.style.display = show;
    };
  } catch (e) { console.error('Music library error:', e); }
}

// ============================================================================
// FADE + CHROMA TOGGLES
// ============================================================================
function toggleFadeOptions() {
  const on = document.getElementById('fade-enable')?.checked;
  const el = document.getElementById('fade-options');
  if (el) el.style.display = on ? 'block' : 'none';
}

document.addEventListener('change', e => {
  if (e.target.id === 'chroma-key-color') {
    const row = document.getElementById('chroma-tolerance-row');
    if (row) row.style.display = e.target.value ? 'flex' : 'none';
  }
});

// ============================================================================
// TEMPLATE VARS PARSER
// ============================================================================
function parseTemplateVars() {
  const raw = document.getElementById('template-vars')?.value || '';
  const vars = {};
  raw.split('\n').forEach(line => {
    const idx = line.indexOf('=');
    if (idx > 0) {
      const k = line.slice(0, idx).trim();
      const v = line.slice(idx + 1).trim();
      if (k) vars[k] = v;
    }
  });
  return Object.keys(vars).length ? vars : null;
}

// ============================================================================
// STUDIO — CHROMA KEY TOOL
// ============================================================================
let _chromaResultPath = null;

function previewChromaImage(input) {
  const file = input.files[0];
  if (!file) return;
  document.getElementById('studio-chroma-img').src = URL.createObjectURL(file);
  document.getElementById('studio-chroma-preview').style.display = 'block';
  document.getElementById('studio-chroma-result').style.display = 'none';
}

async function applyChromaKeyStudio() {
  const input = document.getElementById('studio-chroma-input');
  if (!input.files[0]) { toast('Selecione uma imagem primeiro.', 'error'); return; }
  const color = document.getElementById('studio-chroma-color').value;
  const tol   = parseInt(document.getElementById('studio-chroma-tol').value) || 40;

  const btn = document.querySelector('[onclick="applyChromaKeyStudio()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Processando...'; btn.disabled = true; }

  try {
    const fd = new FormData();
    fd.append('image', input.files[0]);
    fd.append('color', color);
    fd.append('tolerance', tol);
    const r = await fetch('/api/tools/chroma_key', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro no chroma key');
    _chromaResultPath = d.path;
    const img = document.getElementById('studio-chroma-result-img');
    img.src = d.url + '?t=' + Date.now();
    document.getElementById('studio-chroma-download').href = d.url;
    document.getElementById('studio-chroma-download').download = 'chroma_result.png';
    document.getElementById('studio-chroma-result').style.display = 'block';
    toast('Chroma key aplicado!', 'success');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  } finally {
    if (btn) { btn.textContent = orig; btn.disabled = false; }
  }
}

function useChromaResultAsAvatar() {
  if (!_chromaResultPath) return;
  const url = document.getElementById('studio-chroma-result-img').src;
  useAvatarInCreate(_chromaResultPath, url);
}

// ============================================================================
// TRANSLATE VIDEO PAGE
// ============================================================================
let _translJobId = null;
let _translPollIv = null;
let _translResultFilename = null;

function previewTranslVideo(input) {
  const file = input.files[0];
  if (!file) return;
  const vid = document.getElementById('transl-video-preview');
  vid.src = URL.createObjectURL(file);
  vid.style.display = 'block';
}

const _translVoiceMap = {
  pt: 'pt-BR-AntonioNeural', en: 'en-US-GuyNeural',
  es: 'es-ES-AlvaroNeural',  fr: 'fr-FR-HenriNeural',
  de: 'de-DE-ConradNeural',  it: 'it-IT-DiegoNeural',
  ja: 'ja-JP-KeitaNeural',   'zh-CN': 'zh-CN-YunxiNeural',
  ko: 'ko-KR-InJoonNeural',  ar: 'ar-SA-HamedNeural',
};

function autoSetTranslVoice() {
  const lang  = document.getElementById('transl-target-lang').value;
  const input = document.getElementById('transl-target-voice');
  if (input) input.value = _translVoiceMap[lang] || '';
}

async function startVideoTranslation() {
  const videoInput = document.getElementById('transl-video-input');
  if (!videoInput.files || !videoInput.files[0]) {
    toast('Selecione um vídeo primeiro', 'warn'); return;
  }
  const lang  = document.getElementById('transl-target-lang').value;
  const voice = document.getElementById('transl-target-voice').value;

  const fd = new FormData();
  fd.append('video', videoInput.files[0]);
  fd.append('target_lang', lang);
  if (voice) fd.append('target_voice', voice);

  document.getElementById('transl-progress').style.display = 'block';
  document.getElementById('transl-result').style.display   = 'none';
  document.getElementById('transl-status').textContent     = '⏳ Iniciando tradução...';
  document.getElementById('transl-fill').style.width       = '5%';
  document.getElementById('transl-msg').textContent        = 'Enviando vídeo...';

  try {
    const r = await fetch('/api/video/translate', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); document.getElementById('transl-progress').style.display = 'none'; return; }
    _translJobId = d.job_id;
    _pollTranslation(_translJobId);
    toast('Tradução iniciada!', 'success');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
    document.getElementById('transl-progress').style.display = 'none';
  }
}

function _pollTranslation(jobId) {
  if (_translPollIv) clearInterval(_translPollIv);
  if (!jobId) { console.warn('_pollTranslation called with empty jobId — stopping'); return; }
  const _tStart    = Date.now();
  let   _tErrCount = 0;
  _translPollIv = setInterval(async () => {
    if (Date.now() - _tStart > 60 * 60 * 1000) {
      clearInterval(_translPollIv);
      toast('Timeout na tradução. Tente novamente.', 'error');
      return;
    }
    try {
      const r = await fetch('/api/job/' + jobId);
      if (!r.ok) { _tErrCount++; if (_tErrCount >= 8) { clearInterval(_translPollIv); toast('Servidor inacessível.', 'error'); } return; }
      _tErrCount = 0;
      const s = await r.json();
      document.getElementById('transl-fill').style.width   = (s.progress || 0) + '%';
      document.getElementById('transl-msg').textContent    = s.message || '';
      document.getElementById('transl-status').textContent = s.status === 'done' ? '✅ Tradução completa!' :
        s.status === 'error' ? '❌ Erro' : '⏳ ' + (s.message || s.status || 'Processando...');

      if (s.status === 'done') {
        clearInterval(_translPollIv);
        _translResultFilename = s.output_filename;
        const safeFn = encodeURIComponent(s.output_filename || '');
        document.getElementById('transl-result-video').src = '/outputs/' + safeFn;
        document.getElementById('transl-download').href    = '/outputs/' + safeFn;
        document.getElementById('transl-download').download = s.output_filename || 'translated.mp4';
        const txts = document.getElementById('transl-texts');
        if (txts) txts.innerHTML = s.original_text
          ? `<div><strong>Original:</strong> ${_esc(s.original_text)}</div><div style="margin-top:4px"><strong>Tradução:</strong> ${_esc(s.translated_text || '')}</div>`
          : '';
        document.getElementById('transl-result').style.display = 'block';
        toast('Vídeo traduzido pronto!', 'success');
      }
      if (s.status === 'error') {
        clearInterval(_translPollIv);
        toast('Erro na tradução: ' + (s.error || 'Desconhecido'), 'error');
      }
    } catch (e) { _tErrCount++; console.error('Translation poll error:', e); }
  }, 2500);
}

function useTranslatedAsAvatar() {
  if (!_translResultFilename) return;
  toast('Abra o Create e use o vídeo traduzido como input de áudio.', 'info');
  showPage('create');
}

// ============================================================================
// URL TO VIDEO (Video Editor page)
// ============================================================================
async function urlToScript() {
  const url   = document.getElementById('url-input').value.trim();
  const lang  = document.getElementById('url-lang').value;
  const length = document.getElementById('url-length').value;
  if (!url) { toast('Cole uma URL primeiro', 'warn'); return; }

  const btn = document.querySelector('[onclick="urlToScript()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Buscando...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/ai/url_to_script', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, language: lang, style: 'professional', length })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    document.getElementById('url-script-result').value = d.script;
    document.getElementById('url-result').style.display = 'block';
    toast(`Script gerado! (${d.extracted_chars} chars extraídos da URL)`, 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (btn) { btn.textContent = orig; btn.disabled = false; } }
}

function useUrlScript() {
  const script = document.getElementById('url-script-result').value;
  if (!script) return;
  showPage('create');
  const el = document.getElementById('script');
  if (el) { el.value = script; el.dispatchEvent(new Event('input')); }
  updateScriptStats();
  toast('Script carregado no Create!', 'success');
}

// ============================================================================
// VIDEO MERGE (Video Editor page)
// ============================================================================
let _mergeSelected = new Set();

async function loadMergeHistory() {
  const container = document.getElementById('merge-clip-list');
  const trimSel   = document.getElementById('trim-video-select');
  const karSel    = document.getElementById('karaoke-burn-select');
  container.innerHTML = '<div style="color:var(--dim);font-size:13px">Carregando...</div>';
  try {
    const r = await fetch('/api/history');
    const d = await r.json();
    const vids = d.videos || [];
    if (!vids.length) {
      container.innerHTML = '<div style="color:var(--dim);font-size:13px;text-align:center">Nenhum vídeo gerado ainda</div>';
      return;
    }
    container.innerHTML = '';
    if (trimSel) trimSel.innerHTML = '<option value="">— Selecione —</option>';
    if (karSel)  karSel.innerHTML  = '<option value="">— Selecione vídeo do histórico —</option>';

    vids.forEach(v => {
      // Merge list item
      const item = document.createElement('div');
      item.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px;border-radius:6px;cursor:pointer;border:1px solid transparent;margin-bottom:6px;transition:border-color 0.2s';
      item.dataset.filename = v.filename;
      item.innerHTML = `
        <input type="checkbox" class="merge-check" data-file="${_esc(v.filename)}" style="width:18px;height:18px;cursor:pointer">
        <img src="${_esc(v.thumbnail || '')}" style="width:56px;height:36px;object-fit:cover;border-radius:4px;background:var(--bg)">
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${_esc(v.script_preview || v.filename)}</div>
          <div style="font-size:11px;color:var(--dim)">${v.duration || 0}s · ${v.size_mb || 0} MB</div>
        </div>`;
      item.onclick = (e) => {
        if (e.target.type === 'checkbox') return;
        const cb = item.querySelector('.merge-check');
        cb.checked = !cb.checked;
        item.style.borderColor = cb.checked ? 'var(--accent)' : 'transparent';
      };
      item.querySelector('.merge-check').onchange = function() {
        item.style.borderColor = this.checked ? 'var(--accent)' : 'transparent';
      };
      container.appendChild(item);

      // Trim + Karaoke selects
      const opt1 = document.createElement('option');
      opt1.value = v.filename; opt1.textContent = v.script_preview?.substring(0,50) || v.filename;
      if (trimSel) trimSel.appendChild(opt1.cloneNode(true));
      if (karSel)  karSel.appendChild(opt1);
    });
  } catch (e) { container.innerHTML = `<div style="color:var(--red)">Erro: ${e.message}</div>`; }
}

async function startMerge() {
  const checks   = document.querySelectorAll('.merge-check:checked');
  const filenames = Array.from(checks).map(c => c.dataset.file);
  if (filenames.length < 2) { toast('Selecione ao menos 2 vídeos', 'warn'); return; }

  const btn = document.querySelector('[onclick="startMerge()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Unindo...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/editor/merge', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filenames })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    document.getElementById('merge-result-video').src = d.url;
    document.getElementById('merge-download').href    = d.url;
    document.getElementById('merge-download').download = d.filename;
    document.getElementById('merge-result').style.display = 'block';
    toast(`${d.clips_merged} clips unidos! (${d.size_mb} MB)`, 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (btn) { btn.textContent = orig; btn.disabled = false; } }
}

async function trimVideo() {
  const filename = document.getElementById('trim-video-select').value;
  const start    = parseFloat(document.getElementById('trim-start').value) || 0;
  const endVal   = document.getElementById('trim-end').value;
  const end      = endVal ? parseFloat(endVal) : null;
  if (!filename) { toast('Selecione um vídeo', 'warn'); return; }

  const btn = document.querySelector('[onclick="trimVideo()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Cortando...'; btn.disabled = true; }
  try {
    const r = await fetch('/api/editor/trim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename, start, end })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    const dl = document.getElementById('trim-download');
    dl.href = d.url; dl.download = d.filename;
    document.getElementById('trim-result').style.display = 'block';
    toast('Vídeo cortado!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (btn) { btn.textContent = orig; btn.disabled = false; } }
}

// ============================================================================
// KARAOKE CAPTIONS
// ============================================================================
let _karaokeAssUrl = null;

async function generateKaraoke() {
  const input = document.getElementById('karaoke-input');
  if (!input.files[0]) { toast('Selecione um vídeo ou áudio', 'warn'); return; }

  const primary   = document.getElementById('karaoke-primary').value;
  const highlight = document.getElementById('karaoke-highlight').value;
  const fontSize  = document.getElementById('karaoke-fontsize').value;

  const fd = new FormData();
  fd.append('file', input.files[0]);
  fd.append('primary_color', primary);
  fd.append('highlight_color', highlight);
  fd.append('font_size', fontSize);
  fd.append('model', 'base');

  const btn = document.querySelector('[onclick="generateKaraoke()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Gerando (Whisper AI)...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/tools/karaoke_captions', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    _karaokeAssUrl = d.ass_url;
    const dl = document.getElementById('karaoke-ass-download');
    dl.href = d.ass_url; dl.download = 'karaoke.ass';
    document.getElementById('karaoke-result').style.display = 'block';
    toast('Legendas karaoke geradas!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (btn) { btn.textContent = orig; btn.disabled = false; } }
}

async function burnKaraoke() {
  const videoFilename = document.getElementById('karaoke-burn-select').value;
  if (!videoFilename) { toast('Selecione um vídeo do histórico', 'warn'); return; }
  if (!_karaokeAssUrl) { toast('Gere as legendas primeiro', 'warn'); return; }

  const btn = document.querySelector('[onclick="burnKaraoke()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Gravando...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/tools/burn_karaoke', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_filename: videoFilename, ass_url: _karaokeAssUrl })
    });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    document.getElementById('karaoke-final-video').src      = d.url;
    document.getElementById('karaoke-final-download').href   = d.url;
    document.getElementById('karaoke-final-download').download = d.filename;
    document.getElementById('karaoke-burn-result').style.display = 'block';
    toast('Legendas karaoke gravadas no vídeo!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (btn) { btn.textContent = orig; btn.disabled = false; } }
}

// ============================================================================
// FACE SWAP
// ============================================================================
let _faceSwapResultUrl = null;

function previewFaceSwap(input, previewId) {
  const file = input.files[0];
  if (!file) return;
  const img = document.getElementById(previewId);
  img.src = URL.createObjectURL(file);
  img.style.display = 'block';
}

async function doFaceSwap() {
  const src = document.getElementById('fswap-source').files[0];
  const tgt = document.getElementById('fswap-target').files[0];
  if (!src || !tgt) { toast('Selecione imagem fonte E alvo', 'warn'); return; }

  const fd = new FormData();
  fd.append('source', src);
  fd.append('target', tgt);

  const btn = document.querySelector('[onclick="doFaceSwap()"]');
  const orig = btn?.textContent;
  if (btn) { btn.textContent = '⏳ Processando...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/tools/face_swap', { method: 'POST', body: fd });
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); return; }
    _faceSwapResultUrl = d.url;
    document.getElementById('fswap-result-img').src  = d.url + '?t=' + Date.now();
    document.getElementById('fswap-download').href    = d.url;
    document.getElementById('fswap-download').download = 'face_swap_result.jpg';
    document.getElementById('fswap-method').textContent = `Método: ${d.method}`;
    document.getElementById('fswap-result').style.display = 'block';
    toast('Face swap concluído!', 'success');
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
  finally { if (btn) { btn.textContent = orig; btn.disabled = false; } }
}

function useFaceSwapAsAvatar() {
  if (!_faceSwapResultUrl) return;
  useAvatarInCreate('', _faceSwapResultUrl);
}

// ============================================================================
// AVATAR STUDIO — CREATOR
// ============================================================================
let _studioAvatarLibrary = [];
let _rembgResultPath     = null;
let _clothingResultPath  = null;

function setClothingPreset(val) {
  document.getElementById('studio-clothing').value = val;
}
function setNewClothing(val) {
  document.getElementById('studio-new-clothing').value = val;
}

async function createAvatarFromStudio() {
  const name     = document.getElementById('studio-avatar-name').value || 'Avatar';
  const desc     = document.getElementById('studio-description').value;
  const clothing = document.getElementById('studio-clothing').value;
  const style    = document.getElementById('studio-style').value;
  const gender   = document.getElementById('studio-gender').value;
  const age      = document.getElementById('studio-age').value;
  const bgDesc   = document.getElementById('studio-bg-desc').value;

  if (!desc && !clothing) {
    toast('Descreva a pessoa ou a roupa para gerar o avatar.', 'error');
    return;
  }

  document.getElementById('studio-generating').style.display = 'block';
  document.querySelector('#page-studio .btn-generate').disabled = true;

  try {
    const r = await fetch('/api/avatar/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: desc, clothing, style, gender, age, background: bgDesc })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro ao gerar avatar');

    toast(`Avatar "${d.avatar.name}" criado com sucesso!`, 'success');
    await loadAvatarLibrary();
    await populateClothingAvatarSelect();

    // Auto-preview the new avatar
    document.getElementById('studio-selected-avatar-img').src = d.avatar.url;
    document.getElementById('studio-selected-avatar-preview').style.display = 'block';
    const sel = document.getElementById('studio-clothing-avatar-select');
    sel.value = d.avatar.id;
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  } finally {
    document.getElementById('studio-generating').style.display = 'none';
    document.querySelector('#page-studio .btn-generate').disabled = false;
  }
}

async function loadStockAvatarLibrary() {
  const container = document.getElementById('studio-stock-library');
  if (!container) return;
  try {
    const r = await fetch('/api/avatar/stock_library');
    const d = await r.json();
    const avatars = d.avatars || [];
    if (!avatars.length) {
      container.innerHTML = '<div style="text-align:center;color:var(--dim);padding:20px;grid-column:1/-1">Nenhum avatar stock disponível</div>';
      return;
    }
    container.innerHTML = '';
    avatars.forEach(av => {
      const card = document.createElement('div');
      card.className = 'avatar-card';
      card.title = av.name;
      card.innerHTML = `
        <img src="${_esc(av.url)}" alt="${_esc(av.name)}" loading="lazy" style="background:#1a1a2e">
        <div class="avatar-card-label">${_esc(av.name)}</div>
        <div class="avatar-card-actions">
          <button title="Usar no lip sync" onclick="useAvatarInCreate('${_esc(av.path)}','${_esc(av.url)}');event.stopPropagation()">🎬</button>
        </div>`;
      card.onclick = () => useAvatarInCreate(av.path, av.url);
      container.appendChild(card);
    });
  } catch (e) {
    if (container) container.innerHTML = `<div style="color:var(--red);font-size:13px;grid-column:1/-1">Erro: ${e.message}</div>`;
  }
}

async function loadAvatarLibrary() {
  try {
    const r = await fetch('/api/avatar/library');
    const d = await r.json();
    _studioAvatarLibrary = d.avatars || [];
    renderAvatarLibrary();
  } catch (e) {
    console.error('loadAvatarLibrary:', e);
  }
}

function renderAvatarLibrary() {
  const container = document.getElementById('studio-avatar-library');
  if (!_studioAvatarLibrary.length) {
    container.innerHTML = '<div style="text-align:center;color:var(--dim);padding:20px;grid-column:1/-1">Nenhum avatar criado ainda.<br>Use o criador ao lado!</div>';
    return;
  }
  container.innerHTML = '';
  _studioAvatarLibrary.forEach(av => {
    const card = document.createElement('div');
    card.className = 'avatar-card';
    card.title = av.name;
    card.innerHTML = `
      <img src="${_esc(av.url)}" alt="${_esc(av.name)}" loading="lazy">
      <div class="avatar-card-label">${_esc(av.name)}</div>
      <div class="avatar-card-actions">
        <button title="Usar no lip sync" onclick="useAvatarInCreate('${_esc(av.path)}','${_esc(av.url)}');event.stopPropagation()">🎬</button>
        <button title="Trocar roupa" onclick="selectAvatarForClothing('${_esc(av.id)}');event.stopPropagation()">👔</button>
        <button title="Apagar avatar" style="color:var(--red)" onclick="deleteAvatar('${_esc(av.id)}');event.stopPropagation()">🗑️</button>
      </div>`;
    card.onclick = () => useAvatarInCreate(av.path, av.url);
    container.appendChild(card);
  });
}

async function deleteAvatar(id) {
  if (!confirm('Apagar este avatar da biblioteca?')) return;
  try {
    const r = await fetch(`/api/avatar/delete/${id}`, { method: 'DELETE' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro ao apagar');
    toast('Avatar apagado.', 'info');
    await loadAvatarLibrary();
    await populateClothingAvatarSelect();
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

async function populateClothingAvatarSelect() {
  if (!_studioAvatarLibrary.length) await loadAvatarLibrary();
  const sel = document.getElementById('studio-clothing-avatar-select');
  sel.innerHTML = '<option value="">— Selecione um avatar —</option>';
  _studioAvatarLibrary.forEach(av => _addOption(sel, av.id, av.name));
}

function previewSelectedAvatar() {
  const id  = document.getElementById('studio-clothing-avatar-select').value;
  const av  = _studioAvatarLibrary.find(a => a.id === id);
  const pre = document.getElementById('studio-selected-avatar-preview');
  if (av) {
    document.getElementById('studio-selected-avatar-img').src = av.url;
    pre.style.display = 'block';
  } else {
    pre.style.display = 'none';
  }
}

function selectAvatarForClothing(id) {
  document.getElementById('studio-clothing-avatar-select').value = id;
  previewSelectedAvatar();
  document.getElementById('studio-clothing-avatar-select').scrollIntoView({ behavior: 'smooth' });
}

function useAvatarInCreate(path, url) {
  // Navigate to Create page and set this avatar as the source image
  showPage('create');
  // Preview the image
  const preview = document.getElementById('img-preview');
  const vidEl   = document.getElementById('vid-preview');
  const placeholder = document.getElementById('upload-placeholder');
  const badge   = document.getElementById('avatar-mode-badge');
  // Cache-bust the URL to force browser to reload
  const cacheBust = url.includes('?') ? url + '&_t=' + Date.now() : url + '?_t=' + Date.now();
  preview.src = cacheBust;
  preview.style.display = 'block';
  if (vidEl) vidEl.style.display = 'none';
  placeholder.style.display = 'none';
  if (badge) {
    badge.style.display = 'block';
    badge.style.background = 'rgba(124,58,237,0.1)';
    badge.style.color = '#a78bfa';
    badge.style.border = '1px solid #a78bfa';
    badge.textContent = 'Avatar da biblioteca selecionado';
  }
  // Store path for form submission
  window._avatarLibraryPath = path;
  window._avatarLibraryUrl  = url;
  toast('Avatar selecionado! Escreva o script e clique em Gerar.', 'success');
}

// ── REMOVE BACKGROUND ───────────────────────────────────────────────────────
function previewRembgImage(input) {
  const file = input.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  document.getElementById('studio-rembg-img').src = url;
  document.getElementById('studio-rembg-preview').style.display = 'block';
  document.getElementById('studio-rembg-result').style.display = 'none';
}

async function removeBackgroundStudio() {
  const input = document.getElementById('studio-rembg-input');
  if (!input.files[0]) { toast('Selecione uma imagem primeiro.', 'error'); return; }

  const btn = document.getElementById('studio-rembg-btn');
  const origText = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '⏳ Removendo fundo...'; btn.disabled = true; }

  try {
    const fd = new FormData();
    fd.append('image', input.files[0]);
    const r = await fetch('/api/avatar/remove_bg', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro ao remover fundo');

    _rembgResultPath = d.path;
    document.getElementById('studio-rembg-result-img').src = d.url;
    document.getElementById('studio-rembg-download').href = d.url;
    document.getElementById('studio-rembg-result').style.display = 'block';
    toast('Fundo removido com sucesso!', 'success');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  } finally {
    if (btn) { btn.textContent = origText; btn.disabled = false; }
  }
}

function useRembgAsAvatar() {
  if (!_rembgResultPath) return;
  const url = document.getElementById('studio-rembg-result-img').src;
  useAvatarInCreate(_rembgResultPath, url);
}

// ── CLOTHING CHANGE ─────────────────────────────────────────────────────────
async function changeClothingStudio() {
  const avatarId  = document.getElementById('studio-clothing-avatar-select').value;
  const clothing  = document.getElementById('studio-new-clothing').value.trim();

  if (!avatarId) { toast('Selecione um avatar da biblioteca.', 'error'); return; }
  if (!clothing) { toast('Descreva a nova roupa.', 'error'); return; }

  const av   = _studioAvatarLibrary.find(a => a.id === avatarId);
  const desc = av ? (av.description || av.name) : '';

  const btn = document.getElementById('studio-clothing-btn');
  const origText = btn ? btn.textContent : '';
  if (btn) { btn.textContent = '⏳ Gerando nova roupa...'; btn.disabled = true; }

  try {
    const r = await fetch('/api/avatar/change_clothing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ avatar_id: avatarId, clothing, description: desc })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro ao trocar roupa');

    _clothingResultPath = d.avatar ? d.avatar.path : d.path;
    const resultUrl = d.avatar ? d.avatar.url : d.url;
    document.getElementById('studio-clothing-result-img').src = resultUrl;
    document.getElementById('studio-clothing-result').style.display = 'block';
    toast('Roupa trocada com sucesso!', 'success');
    await loadAvatarLibrary();
    await populateClothingAvatarSelect();
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  } finally {
    if (btn) { btn.textContent = origText; btn.disabled = false; }
  }
}

function useClothingResultAsAvatar() {
  if (!_clothingResultPath) return;
  const url = document.getElementById('studio-clothing-result-img').src;
  useAvatarInCreate(_clothingResultPath, url);
}

// ============================================================================
// UPLOAD REAL PHOTO TO LIBRARY
// ============================================================================
function previewUploadPhoto(input) {
  const file = input.files[0];
  if (!file) return;
  document.getElementById('studio-upload-img').src = URL.createObjectURL(file);
  document.getElementById('studio-upload-preview').style.display = 'block';
}

async function uploadAvatarPhoto() {
  const input = document.getElementById('studio-upload-input');
  if (!input.files[0]) { toast('Selecione uma foto primeiro.', 'error'); return; }

  const name = document.getElementById('studio-upload-name').value.trim()
             || `Foto ${new Date().toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit'})}`;

  const btn = document.getElementById('studio-upload-btn');
  const orig = btn.textContent;
  btn.textContent = 'Salvando...'; btn.disabled = true;

  try {
    const fd = new FormData();
    fd.append('image', input.files[0]);
    fd.append('name',  name);
    const r = await fetch('/api/avatar/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro ao salvar');
    toast(`"${d.avatar.name}" salvo na biblioteca!`, 'success');
    input.value = '';
    document.getElementById('studio-upload-preview').style.display = 'none';
    document.getElementById('studio-upload-name').value = '';
    await loadAvatarLibrary();
    await populateClothingAvatarSelect();
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  } finally {
    btn.textContent = orig; btn.disabled = false;
  }
}

// ============================================================================
// GENERATE 5-SECOND SAMPLE CLIP
// ============================================================================
async function generateSample() {
  const imgInput   = document.getElementById('img-input');
  const script     = document.getElementById('script').value.trim();
  const audioInput = document.getElementById('audio-input');

  // Inject library/studio avatar if selected
  if (window._avatarLibraryUrl && (!imgInput.files || !imgInput.files[0])) {
    try {
      const resp = await fetch(window._avatarLibraryUrl);
      const blob = await resp.blob();
      const ext  = window._avatarLibraryUrl.split('.').pop().split('?')[0] || 'jpg';
      const file = new File([blob], `avatar_library.${ext}`, { type: blob.type || 'image/jpeg' });
      const dt   = new DataTransfer();
      dt.items.add(file);
      imgInput.files = dt.files;
    } catch(ex) {
      toast('Erro ao carregar avatar: ' + ex.message, 'error');
      return;
    }
  }
  window._avatarLibraryPath = null;
  window._avatarLibraryUrl  = null;

  if (!imgInput.files || !imgInput.files[0]) {
    toast('Envie uma imagem de avatar primeiro', 'warn'); return;
  }

  // Truncate script to ~10 words for a quick 5s clip
  let sampleScript = script;
  if (!audioInput.files || !audioInput.files[0]) {
    if (!sampleScript) { toast('Escreva um script primeiro para o teste', 'warn'); return; }
    const words = sampleScript.split(/\s+/);
    sampleScript = words.slice(0, 12).join(' ');
    if (words.length > 12) sampleScript += '...';
    toast(`Gerando clipe de teste: "${sampleScript}"`, 'info');
  } else {
    toast('Gerando clipe de teste com audio completo...', 'info');
  }

  const formData = new FormData();
  formData.append('image',            imgInput.files[0]);
  formData.append('script',           sampleScript);
  formData.append('engine',           currentEngine);
  formData.append('voice',            document.getElementById('voice-select').value);
  formData.append('voice_id',         document.getElementById('eleven-voice-id')?.value || '');
  formData.append('preprocess',       document.getElementById('preprocess')?.value || 'full');
  formData.append('still_mode',       'false');
  formData.append('expression_scale', '1.2');
  formData.append('background',       document.getElementById('bg-select').value);
  formData.append('avatar_position',  document.getElementById('avatar-position').value);
  formData.append('avatar_size',      document.getElementById('avatar-size').value);
  formData.append('avatar_opacity',   document.getElementById('avatar-opacity').value);
  formData.append('lip_sync_engine',  'auto');
  formData.append('enhance_face',     document.getElementById('enhance-face')?.checked ? 'true' : 'false');
  if (audioInput.files && audioInput.files[0]) {
    formData.append('audio', audioInput.files[0]);
  }

  document.getElementById('result').style.display        = 'none';
  document.getElementById('progress-area').style.display = 'block';
  document.getElementById('progress-fill').style.width   = '5%';
  document.getElementById('progress-status').textContent = 'Gerando clipe de teste...';
  document.getElementById('progress-text').textContent   = 'Teste rapido (5-10s)...';

  try {
    const r = await fetch('/api/generate', { method: 'POST', body: formData });
    if (!r.ok) { let em = `Erro ${r.status}`; try { const ed = await r.json(); em = ed.error || em; } catch(_) {} toast(em, 'error'); document.getElementById('progress-area').style.display = 'none'; return; }
    const d = await r.json();
    if (d.error) { toast(d.error, 'error'); document.getElementById('progress-area').style.display = 'none'; return; }
    pollJob(d.job_id);
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
    document.getElementById('progress-area').style.display = 'none';
  }
}

// ============================================================================
// DISK CLEANUP
// ============================================================================
async function runCleanup() {
  const days = parseInt(document.getElementById('cleanup-days').value) || 7;
  const resEl = document.getElementById('cleanup-result');
  resEl.textContent = 'Limpando...';
  try {
    const r = await fetch('/api/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ older_than_days: days })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || 'Erro');
    resEl.style.color = 'var(--green)';
    resEl.textContent = `Apagados: ${d.deleted} arquivos — Liberado: ${d.freed_mb} MB`;
    toast(`Limpeza concluída: ${d.deleted} arquivos removidos`, 'success');
    loadDashboard();
  } catch (e) {
    resEl.style.color = 'var(--red)';
    resEl.textContent = 'Erro: ' + e.message;
  }
}

// ============================================================================
// ADMIN — API KEY MANAGEMENT
// ============================================================================
let _adminToken = sessionStorage.getItem('avp_admin_token') || '';

function adminCheckSession() {
  if (_adminToken) {
    adminShowPanel();   // calls adminLoadAnalytics() internally
    adminLoadStatus();
    adminLoadKeys();
  }
}

async function adminLogin() {
  const inp = document.getElementById('admin-token-input');
  const err = document.getElementById('admin-login-error');
  const token = (inp.value || '').trim();
  if (!token) { err.textContent = 'Digite o token.'; err.style.display = 'block'; return; }
  try {
    const r = await fetch('/api/admin/status', {
      headers: { 'X-Admin-Token': token }
    });
    if (!r.ok) { err.textContent = 'Token inválido.'; err.style.display = 'block'; return; }
    _adminToken = token;
    sessionStorage.setItem('avp_admin_token', token);
    err.style.display = 'none';
    inp.value = '';
    adminShowPanel();
    const d = await r.json();
    adminRenderStatus(d);
    adminLoadKeys();
  } catch (e) {
    err.textContent = 'Erro de rede: ' + e.message;
    err.style.display = 'block';
  }
}

function adminShowPanel() {
  document.getElementById('admin-login-card').style.display = 'none';
  document.getElementById('admin-panel').style.display = 'block';
  stripeLoadStatus();
  stripeLoadConfig();
  adminLoadAnalytics();
}

async function adminLoadStatus() {
  if (!_adminToken) return;
  try {
    const r = await fetch('/api/admin/status', { headers: { 'X-Admin-Token': _adminToken } });
    if (!r.ok) { _adminLogout(); return; }
    adminRenderStatus(await r.json());
  } catch (_) {}
}

function adminRenderStatus(d) {
  const badge = document.getElementById('auth-mode-badge');
  const btn   = document.getElementById('auth-toggle-btn');
  if (d.auth_required) {
    badge.textContent = '🔒 Auth: Ligada';
    badge.style.background = 'rgba(34,197,94,0.15)';
    badge.style.color = '#4ade80';
    if (btn) btn.textContent = 'Desligar Auth';
  } else {
    badge.textContent = '🔓 Auth: Desligada';
    badge.style.background = 'rgba(239,68,68,0.15)';
    badge.style.color = '#f87171';
    if (btn) btn.textContent = 'Ligar Auth';
  }
  const grid = document.getElementById('admin-stats-grid');
  if (!grid) return;
  grid.innerHTML = `
    <div class="dash-card"><div class="dash-label">Chaves Ativas</div><div class="dash-value">${d.active_keys ?? 0}</div></div>
    <div class="dash-card"><div class="dash-label">Chaves Total</div><div class="dash-value">${d.total_keys ?? 0}</div></div>
    <div class="dash-card"><div class="dash-label">Vídeos Gerados</div><div class="dash-value">${d.total_jobs_via_key ?? d.total_jobs ?? 0}</div></div>
    <div class="dash-card"><div class="dash-label">Horas Geradas</div><div class="dash-value">${(d.total_hours_via_key ?? (d.total_seconds ?? 0) / 3600).toFixed(2)}h</div></div>
  `;
}

async function toggleAuthMode() {
  if (!_adminToken) return;
  const badge = document.getElementById('auth-mode-badge');
  const currentlyOn = badge.textContent.includes('Ligada');
  try {
    const r = await fetch('/api/admin/auth_mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': _adminToken },
      body: JSON.stringify({ required: !currentlyOn })
    });
    if (!r.ok) { toast('Erro ao alterar modo auth', 'error'); return; }
    adminLoadStatus();
    toast(`Auth ${!currentlyOn ? 'ativada' : 'desativada'} com sucesso`, 'success');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

async function adminCreateKey() {
  if (!_adminToken) return;
  const name = (document.getElementById('new-key-name').value || '').trim();
  const plan = document.getElementById('new-key-plan').value;
  if (!name) { toast('Digite o nome do cliente', 'error'); return; }
  try {
    const r = await fetch('/api/admin/keys', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': _adminToken },
      body: JSON.stringify({ name, plan })
    });
    const d = await r.json();
    if (!r.ok) { toast(d.error || 'Erro ao criar chave', 'error'); return; }
    document.getElementById('new-key-value').textContent = d.key;
    document.getElementById('new-key-result').style.display = 'block';
    document.getElementById('new-key-name').value = '';
    toast('Chave criada! Copie antes de fechar.', 'success');
    adminLoadKeys();
    adminLoadStatus();
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

function copyNewKey() {
  const val = document.getElementById('new-key-value').textContent;
  navigator.clipboard.writeText(val).then(() => toast('Chave copiada!', 'success'));
}

async function adminLoadKeys() {
  if (!_adminToken) return;
  const container = document.getElementById('admin-keys-list');
  container.innerHTML = '<div style="color:var(--muted)">Carregando...</div>';
  try {
    const r = await fetch('/api/admin/keys', { headers: { 'X-Admin-Token': _adminToken } });
    if (!r.ok) { container.innerHTML = '<div style="color:var(--red)">Erro ao carregar chaves</div>'; return; }
    const data = await r.json();
    const keys = Array.isArray(data) ? data : (data.keys || []);
    if (!keys.length) { container.innerHTML = '<div style="color:var(--muted)">Nenhuma chave criada ainda.</div>'; return; }
    container.innerHTML = keys.map(k => `
      <div style="background:var(--surface2);border-radius:10px;padding:14px 16px;display:flex;flex-wrap:wrap;gap:10px;align-items:center">
        <div style="flex:2;min-width:160px">
          <div style="font-weight:600;color:var(--text)">${_esc(k.name)}</div>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">${_esc(k.key_masked || k.key)}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <span style="font-size:12px;padding:2px 8px;border-radius:8px;background:${k.plan==='pro'?'rgba(139,92,246,0.15)':'rgba(59,130,246,0.15)'};color:${k.plan==='pro'?'#a78bfa':'#60a5fa'}">${k.plan}</span>
          <span style="font-size:12px;color:${k.active?'var(--green)':'var(--red)'}">${k.active?'✅ Ativa':'❌ Revogada'}</span>
          <span style="font-size:12px;color:var(--muted)">${k.jobs_generated} vídeos · ${(k.seconds_generated/60).toFixed(1)} min</span>
          <span style="font-size:11px;color:var(--muted)">${(k.last_used||k.created_at||'').slice(0,10)||'nunca'}</span>
        </div>
        <div style="display:flex;gap:6px;margin-left:auto">
          ${k.active
            ? `<button class="btn-secondary" style="font-size:12px;padding:4px 10px" onclick="adminRevokeKey('${_esc(k.key)}')">Revogar</button>`
            : `<button class="btn-secondary" style="font-size:12px;padding:4px 10px;color:var(--green)" onclick="adminActivateKey('${_esc(k.key)}')">Reativar</button>`
          }
          <button class="btn-secondary" style="font-size:12px;padding:4px 10px;color:var(--red)" onclick="adminDeleteKey('${_esc(k.key)}')">Apagar</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = '<div style="color:var(--red)">Erro: ' + _esc(e.message) + '</div>';
  }
}

async function adminRevokeKey(key) {
  if (!_adminToken || !confirm('Revogar esta chave?')) return;
  try {
    const r = await fetch(`/api/admin/keys/${encodeURIComponent(key)}/revoke`, {
      method: 'POST', headers: { 'X-Admin-Token': _adminToken }
    });
    if (!r.ok) { toast('Erro ao revogar', 'error'); return; }
    toast('Chave revogada', 'success');
    adminLoadKeys();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

async function adminActivateKey(key) {
  if (!_adminToken) return;
  try {
    const r = await fetch(`/api/admin/keys/${encodeURIComponent(key)}/activate`, {
      method: 'POST', headers: { 'X-Admin-Token': _adminToken }
    });
    if (!r.ok) { toast('Erro ao reativar', 'error'); return; }
    toast('Chave reativada', 'success');
    adminLoadKeys();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

async function adminDeleteKey(key) {
  if (!_adminToken || !confirm('APAGAR esta chave permanentemente?')) return;
  try {
    const r = await fetch(`/api/admin/keys/${encodeURIComponent(key)}`, {
      method: 'DELETE', headers: { 'X-Admin-Token': _adminToken }
    });
    if (!r.ok) { toast('Erro ao apagar', 'error'); return; }
    toast('Chave apagada', 'success');
    adminLoadKeys();
    adminLoadStatus();
  } catch (e) { toast('Erro: ' + e.message, 'error'); }
}

function _adminLogout() {
  _adminToken = '';
  sessionStorage.removeItem('avp_admin_token');
  document.getElementById('admin-login-card').style.display = 'block';
  document.getElementById('admin-panel').style.display = 'none';
  document.getElementById('admin-keys-list').innerHTML = '';
}

// ============================================================================
// ADMIN — STRIPE INTEGRATION
// ============================================================================
async function stripeLoadStatus() {
  if (!_adminToken) return;
  try {
    const r = await fetch('/api/admin/stripe/status', { headers: { 'X-Admin-Token': _adminToken } });
    if (!r.ok) return;
    const d = await r.json();
    const badge = document.getElementById('stripe-mode-badge');
    if (!badge) return;
    if (d.mode === 'live') {
      badge.textContent = '🟢 Live';
      badge.style.background = 'rgba(34,197,94,0.15)';
      badge.style.color = '#4ade80';
    } else if (d.mode === 'test') {
      badge.textContent = '🧪 Test Mode';
      badge.style.background = 'rgba(251,191,36,0.15)';
      badge.style.color = '#fbbf24';
    } else {
      badge.textContent = '⚠️ não configurado';
      badge.style.background = 'rgba(239,68,68,0.1)';
      badge.style.color = '#f87171';
    }
  } catch (_) {}
}

async function stripeLoadConfig() {
  if (!_adminToken) return;
  try {
    const r = await fetch('/api/admin/stripe/config', { headers: { 'X-Admin-Token': _adminToken } });
    if (!r.ok) return;
    const d = await r.json();
    const set = (id, val) => { const el = document.getElementById(id); if (el && val) el.placeholder = val; };
    set('scfg-secret',       d.stripe_secret_key || '');
    set('scfg-webhook',      d.stripe_webhook_secret || '');
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };
    setVal('scfg-price-starter', d.stripe_price_starter);
    setVal('scfg-price-pro',     d.stripe_price_pro);
    setVal('scfg-smtp-host',     d.smtp_host);
    setVal('scfg-smtp-port',     d.smtp_port || 587);
    setVal('scfg-smtp-user',     d.smtp_user);
    setVal('scfg-smtp-from',     d.smtp_from);
  } catch (_) {}
}

async function stripeSaveConfig() {
  if (!_adminToken) return;
  const get = id => (document.getElementById(id)?.value || '').trim();
  const payload = {
    stripe_secret_key:     get('scfg-secret'),
    stripe_webhook_secret: get('scfg-webhook'),
    stripe_price_starter:  get('scfg-price-starter'),
    stripe_price_pro:      get('scfg-price-pro'),
    smtp_host:  get('scfg-smtp-host'),
    smtp_port:  parseInt(get('scfg-smtp-port') || '587'),
    smtp_user:  get('scfg-smtp-user'),
    smtp_pass:  get('scfg-smtp-pass'),
    smtp_from:  get('scfg-smtp-from'),
  };
  const msg = document.getElementById('stripe-cfg-msg');
  try {
    const r = await fetch('/api/admin/stripe/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': _adminToken },
      body: JSON.stringify(payload)
    });
    if (!r.ok) { toast('Erro ao salvar config Stripe', 'error'); return; }
    toast('Configurações Stripe salvas!', 'success');
    msg.textContent = '✅ Salvo com sucesso';
    msg.style.color = 'var(--green)';
    msg.style.display = 'block';
    stripeLoadStatus();
  } catch (e) {
    msg.textContent = '❌ ' + e.message;
    msg.style.color = 'var(--red)';
    msg.style.display = 'block';
  }
}

async function stripeGenLink(plan) {
  if (!_adminToken) return;
  const result = document.getElementById('stripe-link-result');
  const urlEl  = document.getElementById('stripe-link-url');
  result.style.display = 'none';
  try {
    const r = await fetch('/api/admin/stripe/create_link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': _adminToken },
      body: JSON.stringify({ plan })
    });
    const d = await r.json();
    if (!r.ok) { toast(d.error || 'Erro ao criar link', 'error'); return; }
    urlEl.href = d.url;
    urlEl.textContent = d.url;
    result.style.display = 'block';
    toast(`Link ${plan} criado! Compartilhe com o cliente.`, 'success');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

function copyStripeLink() {
  const url = document.getElementById('stripe-link-url')?.href;
  if (!url || url === '#') return;
  navigator.clipboard.writeText(url).then(() => toast('Link copiado!', 'success'));
}

async function stripeTestEmail() {
  if (!_adminToken) return;
  const email = (document.getElementById('scfg-test-email')?.value || '').trim();
  if (!email) { toast('Digite um email para testar', 'error'); return; }
  const msg = document.getElementById('stripe-cfg-msg');
  try {
    const r = await fetch('/api/admin/stripe/test_email', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Token': _adminToken },
      body: JSON.stringify({ email })
    });
    const d = await r.json();
    msg.textContent = d.ok ? `✅ ${d.message}` : `❌ ${d.message}`;
    msg.style.color  = d.ok ? 'var(--green)' : 'var(--red)';
    msg.style.display = 'block';
    toast(d.message, d.ok ? 'success' : 'error');
  } catch (e) {
    toast('Erro: ' + e.message, 'error');
  }
}

// ── Analytics ────────────────────────────────────────────────────────────────
let _chartDaily = null;
let _chartPlans = null;

async function adminLoadAnalytics() {
  if (!_adminToken) return;
  try {
    const r = await fetch('/api/admin/analytics', { headers: { 'X-Admin-Token': _adminToken } });
    if (!r.ok) return;
    renderAnalytics(await r.json());
  } catch (_) {}
}

function renderAnalytics(d) {
  // KPI cards
  const kpiEl = document.getElementById('analytics-kpis');
  if (kpiEl) {
    const totalKeys = (d.by_plan || []).reduce((s, p) => s + p.keys, 0);
    kpiEl.innerHTML = [
      { label: 'Jobs totais',    value: d.total_jobs,              icon: '🎬' },
      { label: 'Horas geradas',  value: d.total_hours + 'h',       icon: '⏱️' },
      { label: 'API Keys ativas',value: totalKeys,                  icon: '🔑' },
      { label: 'Jobs (30 dias)', value: (d.daily||[]).reduce((s,x)=>s+x.jobs,0), icon: '📅' },
    ].map(k => `
      <div style="background:rgba(255,255,255,0.04);border-radius:8px;padding:12px;text-align:center">
        <div style="font-size:20px">${k.icon}</div>
        <div style="font-size:20px;font-weight:700;color:var(--accent)">${k.value}</div>
        <div style="font-size:11px;color:var(--dim)">${k.label}</div>
      </div>`).join('');
  }

  // Daily chart
  const dailyCanvas = document.getElementById('chart-daily');
  if (dailyCanvas && typeof Chart !== 'undefined') {
    const labels = (d.daily || []).map(x => x.day.slice(5));  // MM-DD
    const values = (d.daily || []).map(x => x.jobs);
    if (_chartDaily && typeof _chartDaily.destroy === 'function') _chartDaily.destroy();
    _chartDaily = new Chart(dailyCanvas, {
      type: 'bar',
      data: {
        labels,
        datasets: [{ label: 'Jobs', data: values,
          backgroundColor: 'rgba(139,92,246,0.6)',
          borderColor: 'rgba(139,92,246,1)',
          borderWidth: 1, borderRadius: 3 }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#888', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' } },
          y: { ticks: { color: '#888', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.05)' }, beginAtZero: true }
        }
      }
    });
  }

  // Plan breakdown doughnut
  const plansCanvas = document.getElementById('chart-plans');
  if (plansCanvas && typeof Chart !== 'undefined' && (d.by_plan||[]).length) {
    const planColors = { free: '#6b7280', starter: '#3b82f6', pro: '#8b5cf6', unlimited: '#10b981' };
    const labels = (d.by_plan || []).map(p => p.plan);
    const values = (d.by_plan || []).map(p => p.jobs);
    if (_chartPlans && typeof _chartPlans.destroy === 'function') _chartPlans.destroy();
    _chartPlans = new Chart(plansCanvas, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{ data: values,
          backgroundColor: labels.map(l => planColors[l] || '#6b7280'),
          borderWidth: 2, borderColor: '#1a1a2e' }]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'right', labels: { color: '#aaa', font: { size: 11 }, boxWidth: 12 } }
        }
      }
    });
  }

  // Top clients list
  const clientsEl = document.getElementById('analytics-top-clients');
  if (clientsEl) {
    const maxJobs = Math.max(1, ...(d.top_clients || []).map(c => c.jobs));
    clientsEl.innerHTML = (d.top_clients || []).length
      ? (d.top_clients || []).map(c => {
          const pct = Math.round((c.jobs / maxJobs) * 100);
          const planColor = { free: '#6b7280', starter: '#3b82f6', pro: '#8b5cf6', unlimited: '#10b981' }[c.plan] || '#6b7280';
          return `
            <div style="margin-bottom:8px">
              <div style="display:flex;justify-content:space-between;margin-bottom:2px">
                <span style="color:${c.active ? '#e5e7eb' : '#6b7280'}">${c.name}${c.active ? '' : ' (revogada)'}</span>
                <span style="color:var(--dim)">${c.jobs} jobs · ${c.hours}h · <span style="color:${planColor}">${c.plan}</span></span>
              </div>
              <div style="height:4px;background:rgba(255,255,255,0.08);border-radius:2px">
                <div style="height:100%;width:${pct}%;background:${planColor};border-radius:2px"></div>
              </div>
            </div>`;
        }).join('')
      : '<div style="color:var(--dim)">Nenhum cliente ainda.</div>';
  }

  // Recent jobs table
  const recentEl = document.getElementById('analytics-recent');
  if (recentEl) {
    const statusIcon = { done: '✅', error: '❌', processing: '⏳', queued: '⏳' };
    recentEl.innerHTML = (d.recent || []).length
      ? `<table style="width:100%;border-collapse:collapse">
           <tr style="color:var(--dim);border-bottom:1px solid rgba(255,255,255,0.06)">
             <th style="text-align:left;padding:4px 6px;font-weight:500">ID</th>
             <th style="text-align:left;padding:4px 6px;font-weight:500">Status</th>
             <th style="text-align:left;padding:4px 6px;font-weight:500">Data</th>
             <th style="text-align:right;padding:4px 6px;font-weight:500">Duração</th>
           </tr>
           ${(d.recent || []).map(j => `
             <tr style="border-bottom:1px solid rgba(255,255,255,0.04)">
               <td style="padding:4px 6px;color:#a78bfa;font-family:monospace">${j.id}</td>
               <td style="padding:4px 6px">${statusIcon[j.status] || '•'} ${j.status}</td>
               <td style="padding:4px 6px;color:var(--dim)">${j.created_at}</td>
               <td style="padding:4px 6px;text-align:right;color:var(--dim)">${j.duration}s</td>
             </tr>`).join('')}
         </table>`
      : '<div style="color:var(--dim)">Nenhum job no banco ainda.</div>';
  }
}

// ════════════════════════════════════════════════════════════════════════════
// 🔐 LICENÇA DESKTOP — UI (hardware ID + ativação)
// ════════════════════════════════════════════════════════════════════════════
async function loadLicenseUI() {
  const hwidEl = document.getElementById('lic-hwid');
  const stEl   = document.getElementById('lic-status');
  const actRow = document.getElementById('lic-activate-row');
  if (!hwidEl || !stEl) return;  // página de settings não montada ainda
  try {
    const hw = await fetch('/api/license/hardware_id').then(r => r.json());
    hwidEl.textContent = hw.hardware_id || (hw.error || '?');
  } catch (e) {
    hwidEl.textContent = 'erro';
  }
  try {
    const st = await fetch('/api/license/status').then(r => r.json());
    // Uso diário (mostra X/Y vídeos hoje se o plano tem limite)
    const used  = Number(st.usage_today || 0);
    const limit = Number(st.daily_limit || 0);
    let usagePart = '';
    if (limit > 0) {
      const pct = Math.min(100, Math.round((used / limit) * 100));
      const c   = pct >= 90 ? '#ef4444' : (pct >= 70 ? '#eab308' : '#22c55e');
      usagePart = ` · <span style="color:${c}">Hoje: ${used}/${limit}</span>`;
    } else if (st.active && used > 0) {
      usagePart = ` · Hoje: ${used} vídeos`;
    }
    if (st.active) {
      const exp = (st.expires === 'never' || !st.expires)
        ? 'vitalícia'
        : (String(st.expires).slice(0, 10));
      const cust = st.customer ? ` · ${st.customer}` : '';
      stEl.innerHTML = `<span style="color:#22c55e;font-weight:600">● Ativa</span> — plano <strong>${st.plan}</strong> (expira: ${exp})${cust}${usagePart}`;
      if (actRow) actRow.style.display = 'none';
    } else {
      stEl.innerHTML = `<span style="color:#eab308;font-weight:600">● ${st.plan || 'trial'}</span> — ${st.reason || 'sem licença ativa'}${usagePart}`;
      if (actRow) actRow.style.display = '';
    }

    // Banner GLOBAL de expiração: aparece se licença expira em <=7 dias.
    // Sticky até o usuário fechar; reaparece no próximo load se condição persiste.
    try {
      const banner = document.getElementById('lic-expire-banner');
      if (banner && st.active && st.expires && st.expires !== 'never') {
        const expDate = new Date(st.expires);
        if (!isNaN(expDate.getTime())) {
          const daysLeft = Math.ceil((expDate.getTime() - Date.now()) / 86400000);
          const dismissedKey = 'avp_lic_banner_dismissed_' + st.expires.slice(0, 10);
          if (daysLeft <= 7 && !sessionStorage.getItem(dismissedKey)) {
            const title = document.getElementById('lic-expire-title');
            const msg   = document.getElementById('lic-expire-msg');
            if (daysLeft <= 0) {
              if (title) title.textContent = '🚫 Sua licença EXPIROU';
              if (msg) msg.textContent = `O plano '${st.plan}' venceu em ${st.expires.slice(0,10)}. Renove para continuar gerando vídeos.`;
            } else if (daysLeft === 1) {
              if (title) title.textContent = '⏰ Sua licença expira AMANHÃ';
              if (msg) msg.textContent = `Plano '${st.plan}' expira em ${st.expires.slice(0,10)}. Renove hoje para evitar interrupção.`;
            } else {
              if (title) title.textContent = `⚠️ Licença expira em ${daysLeft} dias`;
              if (msg) msg.textContent = `Plano '${st.plan}' expira em ${st.expires.slice(0,10)}. Considere renovar antes do vencimento.`;
            }
            banner.style.display = 'block';
            // Marca como dismissed quando o usuário fecha (mas só pra ESTA expires — se renovar, nova banner)
            banner.querySelectorAll('button').forEach(b => {
              if (!b._avpHooked) { b._avpHooked = true;
                b.addEventListener('click', () => sessionStorage.setItem(dismissedKey, '1'));
              }
            });
          } else {
            banner.style.display = 'none';
          }
        }
      } else if (banner) {
        banner.style.display = 'none';
      }
    } catch (e) { /* banner não-crítico */ }
  } catch (e) {
    stEl.textContent = 'erro ao carregar status';
  }
}

function copyHwid() {
  const txt = (document.getElementById('lic-hwid') || {}).textContent || '';
  if (!txt || txt === 'carregando…') return;
  const msg = document.getElementById('lic-msg');
  const showMsg = (color, text) => {
    if (!msg) return;
    msg.style.display = 'block'; msg.style.color = color; msg.textContent = text;
    setTimeout(() => { msg.style.display = 'none'; }, 2000);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(
      () => showMsg('#22c55e', '✓ Hardware ID copiado!'),
      () => showMsg('#ef4444', 'Falha ao copiar')
    );
  } else {
    // fallback: seleciona o texto
    const r = document.createRange();
    r.selectNode(document.getElementById('lic-hwid'));
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(r);
    showMsg('#22c55e', '✓ Selecionado — Ctrl+C para copiar');
  }
}

async function activateLicense() {
  const inputEl = document.getElementById('lic-input');
  const msg     = document.getElementById('lic-msg');
  const lic     = (inputEl ? inputEl.value : '').trim();
  if (!lic) {
    if (msg) { msg.style.display = 'block'; msg.style.color = '#eab308'; msg.textContent = 'Cole a chave de licença primeiro.'; }
    return;
  }
  try {
    const r = await fetch('/api/license/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ license: lic })
    });
    const d = await r.json();
    if (msg) msg.style.display = 'block';
    if (r.ok) {
      if (msg) { msg.style.color = '#22c55e';
        msg.textContent = `✓ Licença ativada! Plano '${d.plan}' agora ativo.`; }
      if (inputEl) inputEl.value = '';
      setTimeout(loadLicenseUI, 400);
    } else {
      if (msg) { msg.style.color = '#ef4444';
        msg.textContent = `✗ ${d.error || 'Falha na ativação'}`; }
    }
  } catch (e) {
    if (msg) { msg.style.display = 'block'; msg.style.color = '#ef4444';
      msg.textContent = `✗ Erro de rede: ${e}`; }
  }
}

// Carregar UI de licença assim que a página estiver pronta
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', loadLicenseUI);
} else {
  loadLicenseUI();
}

// ════════════════════════════════════════════════════════════════════════════
// 🔔 NOTIFICAÇÕES DE JOB CONCLUÍDO (HeyGen-like: avisa mesmo com aba em BG)
// ════════════════════════════════════════════════════════════════════════════

// Pede permissão de notificação na primeira interação. Idempotente.
function requestNotifyPermission() {
  try {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'default') {
      Notification.requestPermission().catch(() => {});
    }
  } catch (e) { /* navegadores antigos */ }
}

// Toca um chime curto (Web Audio API, sem arquivo). Falha silenciosamente.
function playChime() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    const ctx = new AC();
    const now = ctx.currentTime;
    // dois "ding" curtos: 880Hz e 1320Hz, envelope rápido
    [[880, 0], [1320, 0.13]].forEach(([freq, t]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      osc.connect(gain); gain.connect(ctx.destination);
      gain.gain.setValueAtTime(0.0001, now + t);
      gain.gain.exponentialRampToValueAtTime(0.18, now + t + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.25);
      osc.start(now + t); osc.stop(now + t + 0.3);
    });
    setTimeout(() => { try { ctx.close(); } catch(_){} }, 800);
  } catch (e) { /* sem audio */ }
}

// Notifica o usuário que um job terminou — chime + notificação de browser.
function notifyJobDone(filename) {
  playChime();
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      const n = new Notification('AvatarPilot Pro — Vídeo pronto! 🎬', {
        body: filename || 'Seu vídeo de avatar está pronto para baixar.',
        icon: '/static/favicon.ico',
        tag:  'avp-job-done',  // substitui notificação anterior se ainda visível
      });
      n.onclick = () => { window.focus(); n.close(); };
    }
  } catch (e) { /* sem notificacao */ }
}

// Auto-pede permissão na primeira submissão de job (handler de click no botão Gerar)
document.addEventListener('click', function _avpPermOnce(ev) {
  const btn = ev.target.closest && ev.target.closest('button');
  if (!btn) return;
  const txt = (btn.innerText || '').toLowerCase();
  if (/gerar|criar|generate/.test(txt)) {
    requestNotifyPermission();
    document.removeEventListener('click', _avpPermOnce, true);
  }
}, true);

// ════════════════════════════════════════════════════════════════════════════
// 📊 DASHBOARD — agrega licença + histórico + sistema em uma página polida
// ════════════════════════════════════════════════════════════════════════════
async function loadDashboard() {
  if (!document.getElementById('page-dashboard')) return;
  // Buscar dados em paralelo (mais rápido)
  const [licR, histR, sysR, dashR] = await Promise.allSettled([
    fetch('/api/license/status').then(r => r.json()),
    fetch('/api/history?limit=200').then(r => r.json()),
    fetch('/api/system_health').then(r => r.json()).catch(() => ({})),
    fetch('/api/dashboard').then(r => r.json()).catch(() => ({})),
  ]);
  const lic  = licR.status  === 'fulfilled' ? licR.value  : {};
  const hist = histR.status === 'fulfilled' ? histR.value : { videos: [], total: 0, total_size_mb: 0 };
  const sys  = sysR.status  === 'fulfilled' ? sysR.value  : {};
  const dash = dashR.status === 'fulfilled' ? dashR.value : {};

  // HERO: plano + saudação
  const planEl = document.getElementById('dash-plan-name');
  const planSub = document.getElementById('dash-plan-sub');
  const greet = document.getElementById('dash-greeting');
  if (lic.active) {
    const exp = (lic.expires === 'never' || !lic.expires) ? 'vitalícia' : String(lic.expires).slice(0, 10);
    if (planEl) planEl.textContent = (lic.plan || 'pro').toUpperCase();
    if (planSub) planSub.textContent = `Licença ativa · expira: ${exp}${lic.customer ? ' · ' + lic.customer : ''}`;
    if (greet && lic.customer) greet.textContent = `Olá, ${lic.customer} — acompanhe seu uso e atalhos rápidos`;
  } else {
    if (planEl) planEl.textContent = 'TRIAL';
    if (planSub) planSub.textContent = lic.reason || 'Ative sua licença em Configurações para desbloquear o plano completo';
  }

  // Stats
  const videos = Array.isArray(hist.videos) ? hist.videos : [];
  const todayIso = new Date().toISOString().slice(0, 10);
  const today = videos.filter(v => String(v.created || '').slice(0, 10) === todayIso).length;
  const totalMinutes = videos.reduce((sum, v) => sum + (Number(v.duration || 0) / 60), 0);
  const setText = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
  setText('dash-stat-today', String(today));
  if (lic.daily_limit > 0) {
    setText('dash-stat-today-sub', `de ${lic.daily_limit} no plano`);
  } else {
    setText('dash-stat-today-sub', 'vídeos gerados');
  }
  setText('dash-stat-total',   String(hist.total || videos.length));
  setText('dash-stat-minutes', totalMinutes.toFixed(1));
  const diskMb = Number(hist.total_size_mb || 0);
  setText('dash-stat-disk', diskMb >= 1024 ? (diskMb / 1024).toFixed(1) + ' GB' : Math.round(diskMb) + ' MB');
  const freeMb = Number(dash.disk_free_mb || 0);
  if (freeMb > 0) setText('dash-stat-disk-sub', `${(freeMb / 1024).toFixed(1)} GB livres`);

  // Cota diária (barra visual)
  const quotaCard = document.getElementById('dash-quota-card');
  if (lic.daily_limit > 0) {
    quotaCard.style.display = 'block';
    const used = Number(lic.usage_today || 0);
    const limit = Number(lic.daily_limit);
    const pct = Math.min(100, Math.round((used / limit) * 100));
    setText('dash-quota-label', `${used} / ${limit} vídeos hoje (${pct}%)`);
    const bar = document.getElementById('dash-quota-bar');
    if (bar) {
      bar.style.width = pct + '%';
      bar.style.background = pct >= 90 ? '#ef4444' : pct >= 70 ? '#eab308' : '#22c55e';
    }
  } else {
    quotaCard.style.display = 'none';
  }

  // Vídeos recentes (últimos 6 com thumbs)
  const recentEl = document.getElementById('dash-recent');
  const recent = videos.slice(0, 6);
  if (recent.length === 0) {
    recentEl.innerHTML = '<div style="color:var(--dim);text-align:center;padding:24px;grid-column:1/-1">Nenhum vídeo ainda. <a href="#" onclick="showPage(\'create\');return false" style="color:#a78bfa">Criar o primeiro</a></div>';
  } else {
    recentEl.innerHTML = recent.map(v => {
      const thumb = v.thumbnail ? `<img src="${v.thumbnail}" style="width:100%;height:112px;object-fit:cover;border-radius:6px;background:#222">` :
        '<div style="width:100%;height:112px;background:#222;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:32px">🎬</div>';
      const fname = (v.filename || '').replace(/_final\.mp4$/, '');
      const dur = v.duration ? `${Number(v.duration).toFixed(1)}s` : '';
      const sz = v.size_mb ? `${Number(v.size_mb).toFixed(1)} MB` : '';
      const safe = encodeURIComponent(v.filename || '');
      return `<div style="background:rgba(0,0,0,0.25);border-radius:8px;padding:8px">
        ${thumb}
        <div style="font-size:11px;font-family:monospace;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${fname}">${fname || 'sem nome'}</div>
        <div style="font-size:10px;color:var(--dim);margin-top:2px">${dur} · ${sz}</div>
        <div style="display:flex;gap:4px;margin-top:6px">
          <a href="/outputs/${safe}" target="_blank" class="btn-small" style="flex:1;text-align:center">▶️ Ver</a>
          <a href="/outputs/${safe}" download="${v.filename || 'video.mp4'}" class="btn-small" style="flex:1;text-align:center">⬇️</a>
        </div>
      </div>`;
    }).join('');
  }

  // Sistema
  const gpu = sys.resources && sys.resources.vram_free_gb;
  setText('dash-gpu', gpu != null ? `${gpu} GB` : '—');
  const active = (sys.resources && sys.resources.active_jobs) ?? dash.active_jobs ?? 0;
  setText('dash-jobs', String(active));
  const engs = sys.engines || {};
  const okEngs = Object.entries(engs).filter(([_, v]) => v === 'ready').map(([k, _]) => k).join(', ');
  setText('dash-engines', okEngs || '—');
  // Hardware ID
  try {
    const hw = await fetch('/api/license/hardware_id').then(r => r.json());
    setText('dash-hwid', (hw.hardware_id || '—').slice(0, 16) + '…');
  } catch (e) { setText('dash-hwid', '—'); }
}

// Re-carrega o dashboard sempre que o usuário entra nele
(function () {
  const _orig = window.showPage;
  if (typeof _orig === 'function') {
    window.showPage = function (name) {
      const r = _orig.apply(this, arguments);
      if (name === 'dashboard') {
        try { loadDashboard(); } catch (e) { console.error('dash err', e); }
      }
      return r;
    };
  }
  // Também carrega no primeiro load (se o dashboard estiver visível ou só pra pré-popular)
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { try { loadDashboard(); } catch (e) {} });
  } else {
    try { loadDashboard(); } catch (e) {}
  }
})();

// ════════════════════════════════════════════════════════════════════════════
// 📋 TEMPLATES DE SCRIPT POR NICHO (modal HeyGen-like)
// ════════════════════════════════════════════════════════════════════════════
const SCRIPT_TEMPLATES = [
  { icon: '📢', name: 'Anúncio curto',     niche: 'Marketing/Vendas',
    desc: 'Hook + benefício + CTA. Ideal p/ Reels/TikTok 15-30s.',
    voice: 'sales_pitch',
    text: 'Cansado de [PROBLEMA]? Descobri uma forma simples de [SOLUÇÃO]. Em apenas [TEMPO], você consegue [RESULTADO]. Clique no link agora e veja como funciona. Não perca essa oportunidade!' },
  { icon: '🎓', name: 'Tutorial / Como fazer', niche: 'Educacional',
    desc: 'Passo-a-passo claro. Ideal p/ tutoriais 1-3min.',
    voice: 'corporate_trainer',
    text: 'Hoje vou te mostrar como [TAREFA] em apenas 3 passos simples. Primeiro, [PASSO 1]. Em seguida, [PASSO 2]. E por último, [PASSO 3]. Pronto! Agora você já sabe [RESULTADO]. Curtiu? Compartilhe esse vídeo!' },
  { icon: '🎤', name: 'Apresentação de canal', niche: 'YouTube/Podcast',
    desc: 'Boas-vindas profissional. Ideal p/ intro de canal/podcast.',
    voice: 'podcast_host',
    text: 'Olá e bem-vindo ao [NOME DO CANAL]. Aqui você vai aprender [TÓPICO PRINCIPAL] de forma simples e direta. Toda semana eu trago [TIPO DE CONTEÚDO] novo. Se você curte [TEMA], inscreva-se e ative o sininho. Vamos juntos nessa jornada!' },
  { icon: '📰', name: 'Notícia / Reportagem', niche: 'Jornalismo',
    desc: 'Tom de âncora profissional. Ideal p/ news bulletins.',
    voice: 'news_anchor',
    text: 'Boa noite. A notícia mais comentada de hoje: [MANCHETE]. Segundo informações, [CONTEXTO BREVE]. Especialistas afirmam que [ANÁLISE]. Acompanhe os desdobramentos no nosso portal. Eu sou [SEU NOME], obrigado por assistir.' },
  { icon: '💪', name: 'Mensagem motivacional', niche: 'Coaching/Self-help',
    desc: 'Inspiração emocional. Ideal p/ Reels motivacionais 30s.',
    voice: 'friendly_explainer',
    text: 'Pare e respire fundo. Tudo o que você precisa pra mudar sua vida começa com uma decisão. Hoje. Agora. Não importa onde você está — importa onde você quer chegar. Dê o primeiro passo. O resto vem com o tempo. Você consegue.' },
  { icon: '🎬', name: 'Narração de documentário', niche: 'Documental',
    desc: 'Tom profundo e contemplativo. Ideal p/ vídeo longo (1-5min).',
    voice: 'documentary_narrator',
    text: 'Existe um lugar no mundo onde o tempo parece se esticar. [DESCRIÇÃO DO LUGAR/TEMA]. Há séculos, [CONTEXTO HISTÓRICO]. Hoje, [SITUAÇÃO ATUAL]. E é justamente essa transformação que vamos explorar nos próximos minutos.' },
];

function openScriptTemplates() {
  // Remove modal anterior se existir
  const old = document.getElementById('script-templates-modal');
  if (old) old.remove();
  // Cria modal
  const m = document.createElement('div');
  m.id = 'script-templates-modal';
  m.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px';
  m.innerHTML = `
    <div style="background:#1a1a2e;border:1px solid rgba(124,58,237,0.4);border-radius:12px;max-width:760px;width:100%;max-height:85vh;overflow:auto;padding:24px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2 style="margin:0;font-size:20px">📋 Templates de Script</h2>
        <button onclick="document.getElementById('script-templates-modal').remove()" style="background:transparent;border:none;color:#fff;font-size:24px;cursor:pointer;padding:4px 12px">✕</button>
      </div>
      <p style="color:var(--dim);font-size:13px;margin:0 0 16px">Clique em um template para usar. Substitua os [CAMPOS] pelo seu conteúdo. A voz recomendada é selecionada automaticamente.</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px" id="tmpl-grid"></div>
    </div>
  `;
  document.body.appendChild(m);
  const grid = document.getElementById('tmpl-grid');
  SCRIPT_TEMPLATES.forEach((tpl, idx) => {
    const card = document.createElement('div');
    card.style.cssText = 'background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:14px;cursor:pointer;transition:all 0.15s';
    card.onmouseenter = () => { card.style.borderColor = 'rgba(124,58,237,0.6)'; card.style.background = 'rgba(124,58,237,0.08)'; };
    card.onmouseleave = () => { card.style.borderColor = 'rgba(255,255,255,0.08)'; card.style.background = 'rgba(0,0,0,0.3)'; };
    card.onclick = () => applyScriptTemplate(idx);
    card.innerHTML = `
      <div style="font-size:24px">${tpl.icon}</div>
      <div style="font-weight:600;margin-top:6px">${tpl.name}</div>
      <div style="color:var(--dim);font-size:11px;margin-top:2px">${tpl.niche}</div>
      <div style="color:var(--dim);font-size:12px;margin-top:8px;line-height:1.3">${tpl.desc}</div>
    `;
    grid.appendChild(card);
  });
  // Fecha clicando no backdrop
  m.addEventListener('click', e => { if (e.target === m) m.remove(); });
}

function applyScriptTemplate(idx) {
  const tpl = SCRIPT_TEMPLATES[idx];
  if (!tpl) return;
  const scriptEl = document.getElementById('script');
  if (scriptEl) {
    if (scriptEl.value.trim() && !confirm('Substituir o script atual pelo template?')) return;
    scriptEl.value = tpl.text;
    scriptEl.dispatchEvent(new Event('input', { bubbles: true })); // dispara contador
  }
  // Tenta selecionar a voz recomendada (preset)
  const presetSel = document.getElementById('voice-preset') || document.querySelector('[id*=preset]');
  if (presetSel && tpl.voice) {
    try {
      presetSel.value = tpl.voice;
      presetSel.dispatchEvent(new Event('change', { bubbles: true }));
    } catch (e) { /* preset não existe nesse select */ }
  }
  document.getElementById('script-templates-modal').remove();
  if (typeof toast === 'function') toast(`✓ Template "${tpl.name}" aplicado — substitua os [CAMPOS]`, 'success');
  if (scriptEl) scriptEl.focus();
}

// ════════════════════════════════════════════════════════════════════════════
// 📥 DRAG-AND-DROP de imagem/vídeo na zona de upload (Create Avatar)
// ════════════════════════════════════════════════════════════════════════════
(function setupDragAndDrop() {
  function init() {
    const zone  = document.getElementById('upload-zone');
    const input = document.getElementById('img-input');
    if (!zone || !input) return;
    if (zone._avpDragSetup) return;  // idempotent
    zone._avpDragSetup = true;
    const _origBorder = zone.style.border || '';
    const _origBg     = zone.style.background || '';
    const highlight = () => {
      zone.style.border = '2px dashed #22c55e';
      zone.style.background = 'rgba(34,197,94,0.08)';
    };
    const unhighlight = () => {
      zone.style.border = _origBorder;
      zone.style.background = _origBg;
    };
    ['dragenter', 'dragover'].forEach(e =>
      zone.addEventListener(e, ev => { ev.preventDefault(); ev.stopPropagation(); highlight(); }));
    ['dragleave', 'dragend'].forEach(e =>
      zone.addEventListener(e, ev => { ev.preventDefault(); ev.stopPropagation(); unhighlight(); }));
    zone.addEventListener('drop', ev => {
      ev.preventDefault(); ev.stopPropagation();
      unhighlight();
      const dt = ev.dataTransfer;
      if (!dt || !dt.files || dt.files.length === 0) return;
      const file = dt.files[0];
      const isImg = /\.(jpe?g|png|webp|bmp)$/i.test(file.name);
      const isVid = /\.(mp4|mov|avi|webm|mkv|m4v)$/i.test(file.name);
      if (!isImg && !isVid) {
        if (typeof toast === 'function') toast('Tipo não suportado: ' + file.name + ' (use jpg/png/mp4)', 'error');
        return;
      }
      // Atribui o arquivo ao input via DataTransfer (cross-browser)
      try {
        const dtNew = new DataTransfer();
        dtNew.items.add(file);
        input.files = dtNew.files;
      } catch (e) {
        // Browsers antigos: dispara mensagem e pede clique manual
        if (typeof toast === 'function') toast('Drag-drop não suportado neste navegador — clique para upload', 'error');
        return;
      }
      // Dispara handler existente de preview
      if (typeof previewAvatarFile === 'function') {
        previewAvatarFile(input);
      } else {
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
      if (typeof toast === 'function') toast('📷 ' + file.name + ' carregado', 'success');
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();


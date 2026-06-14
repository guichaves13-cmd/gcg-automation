/**
 * TitlePilot Pro — Sistema Anti-Bug Inteligente v2.0
 * Melhorias: UX mais receptiva, cache inteligente, input sanitization,
 * debounce, telemetria de erros, loading skeleton, modo offline
 */

// ═══════════════════════════════════════════════════════════
// CONFIG GLOBAL
// ═══════════════════════════════════════════════════════════
const TP_CONFIG = {
  MAX_RETRIES: 3,
  RETRY_DELAYS: [8, 20, 45],      // segundos por tentativa
  REQUEST_TIMEOUT: 120000,         // 2 min
  HEALTH_INTERVAL: 4 * 60 * 1000, // 4 min
  CACHE_TTL: 5 * 60 * 1000,       // 5 min cache
  INPUT_MAX_LENGTH: 300,           // truncar inputs gigantes
  VERSION: '2.1',
};

// ═══════════════════════════════════════════════════════════
// CACHE INTELIGENTE (evita chamar IA duas vezes para mesma coisa)
// ═══════════════════════════════════════════════════════════
const SmartCache = (() => {
  const store = new Map();

  function key(url, data) {
    return url + '|' + JSON.stringify(data);
  }

  function get(url, data) {
    const k = key(url, data);
    const entry = store.get(k);
    if (!entry) return null;
    if (Date.now() - entry.ts > TP_CONFIG.CACHE_TTL) {
      store.delete(k);
      return null;
    }
    return entry.value;
  }

  function set(url, data, value) {
    // Não cachear erros nem endpoints de escrita
    if (value && value.error) return;
    if (url.includes('/add') || url.includes('/save') || url.includes('/delete')) return;
    store.set(key(url, data), { value, ts: Date.now() });
  }

  function invalidate(urlPrefix) {
    for (const k of store.keys()) {
      if (k.startsWith(urlPrefix)) store.delete(k);
    }
  }

  return { get, set, invalidate };
})();

// ═══════════════════════════════════════════════════════════
// SANITIZER DE INPUT (previne payloads maliciosos)
// ═══════════════════════════════════════════════════════════
function sanitizeInput(obj) {
  if (typeof obj === 'string') {
    return obj
      .substring(0, TP_CONFIG.INPUT_MAX_LENGTH)
      .replace(/<script[^>]*>.*?<\/script>/gi, '')
      .replace(/<[^>]+>/g, '')       // remove HTML tags
      .trim();
  }
  if (Array.isArray(obj)) return obj.map(sanitizeInput);
  if (obj && typeof obj === 'object') {
    const clean = {};
    for (const [k, v] of Object.entries(obj)) {
      clean[k] = sanitizeInput(v);
    }
    return clean;
  }
  return obj;
}

// ═══════════════════════════════════════════════════════════
// TOAST NOTIFICATION SYSTEM v2 (substitui alert())
// ═══════════════════════════════════════════════════════════
const Toast = (() => {
  let container;
  const MAX_TOASTS = 4;

  function getContainer() {
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.style.cssText = `
        position:fixed;top:20px;right:20px;z-index:99999;
        display:flex;flex-direction:column;gap:8px;
        pointer-events:none;max-width:360px;min-width:280px;
      `;
      document.body.appendChild(container);
    }
    return container;
  }

  function show(message, type = 'info', duration = 5000) {
    const colors = {
      success: { bg: 'linear-gradient(135deg,#0d2e1a,#0a2415)', border: '#4ecca3', icon: '✅', text: '#4ecca3' },
      error:   { bg: 'linear-gradient(135deg,#2e0d0d,#1a0808)', border: '#e94560', icon: '❌', text: '#e94560' },
      warning: { bg: 'linear-gradient(135deg,#2e1e0d,#1a1208)', border: '#f59e0b', icon: '⚠️', text: '#f59e0b' },
      info:    { bg: 'linear-gradient(135deg,#0d1a2e,#081220)', border: '#3b82f6', icon: 'ℹ️', text: '#3b82f6' },
      retry:   { bg: 'linear-gradient(135deg,#1a0d2e,#100820)', border: '#8b5cf6', icon: '🔄', text: '#8b5cf6' },
      copy:    { bg: 'linear-gradient(135deg,#0d2e1a,#0a2415)', border: '#4ecca3', icon: '📋', text: '#4ecca3' },
    };
    const c = colors[type] || colors.info;

    // Limit max toasts
    const ct = getContainer();
    while (ct.children.length >= MAX_TOASTS) {
      ct.firstChild?.remove();
    }

    const el = document.createElement('div');
    el.style.cssText = `
      background:${c.bg};border:1px solid ${c.border}40;border-left:3px solid ${c.border};
      border-radius:10px;padding:10px 14px;color:#fff;font-size:13px;font-family:'Inter',sans-serif;
      pointer-events:all;cursor:pointer;opacity:0;
      transform:translateX(20px) scale(0.97);transition:all 0.25s ease;
      box-shadow:0 4px 24px rgba(0,0,0,0.5),0 0 0 1px rgba(255,255,255,0.03);
      display:flex;align-items:flex-start;gap:10px;
    `;
    el.innerHTML = `
      <span style="font-size:15px;flex-shrink:0;margin-top:1px">${c.icon}</span>
      <div style="flex:1;min-width:0">
        <div style="color:${c.text};font-weight:700;font-size:10px;text-transform:uppercase;
          letter-spacing:0.5px;margin-bottom:2px">${type}</div>
        <div style="color:#d0d0d0;line-height:1.45;word-wrap:break-word">${message}</div>
      </div>
      <span style="color:#444;flex-shrink:0;font-size:16px;line-height:1;margin-top:1px;
        hover:color:#888;cursor:pointer" onclick="this.parentElement.remove()">×</span>
    `;
    el.id = 'toast-' + Date.now();
    ct.appendChild(el);
    requestAnimationFrame(() => {
      el.style.opacity = '1';
      el.style.transform = 'translateX(0) scale(1)';
    });

    const removeTimer = setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(10px) scale(0.97)';
      setTimeout(() => el.remove(), 250);
    }, duration);

    el.onclick = () => {
      clearTimeout(removeTimer);
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 250);
    };
    return el;
  }

  return {
    success: (m, d) => show(m, 'success', d || 3500),
    error:   (m, d) => show(m, 'error',   d || 7000),
    warning: (m, d) => show(m, 'warning', d || 5000),
    info:    (m, d) => show(m, 'info',    d || 4000),
    retry:   (m, d) => show(m, 'retry',   d || 6000),
    copy:    (m, d) => show(m, 'copy',    d || 2500),
  };
})();

// Sobrescrever alert() global
window._originalAlert = window.alert;
window.alert = (msg) => {
  if (typeof msg === 'string') Toast.warning(msg);
  else window._originalAlert(msg);
};

// ═══════════════════════════════════════════════════════════
// HEALTH MONITOR v2 — Badge com histórico de erros
// ═══════════════════════════════════════════════════════════
const HealthMonitor = (() => {
  let badge;
  let consecutiveErrors = 0;
  let totalRequests = 0;
  let totalErrors = 0;
  let lastError = null;

  function getBadge() {
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'health-badge';
      badge.style.cssText = `
        position:fixed;bottom:14px;left:14px;z-index:9999;
        display:flex;align-items:center;gap:6px;
        background:#0a0d12;border:1px solid #1e2530;
        border-radius:20px;padding:5px 12px;font-size:11px;font-family:'Inter',sans-serif;
        cursor:pointer;transition:all 0.2s;user-select:none;
      `;
      badge.title = 'Clique para verificar saúde do sistema';
      badge.onclick = () => TitlePilotAI.runHealthCheck();
      document.body.appendChild(badge);
    }
    return badge;
  }

  function setStatus(status, detail) {
    const b = getBadge();
    const cfgs = {
      ok:       { dot:'#4ecca3', text:'#4ecca3', label:'🤖 IA Online' },
      retrying: { dot:'#f59e0b', text:'#f59e0b', label:'🔄 Recuperando' },
      error:    { dot:'#e94560', text:'#e94560', label:'⚠️ Erro IA' },
      degraded: { dot:'#f59e0b', text:'#f59e0b', label:'⚡ Degradado' },
      offline:  { dot:'#666',    text:'#666',    label:'📴 Offline' },
    };
    const cfg = cfgs[status] || cfgs.ok;
    const animStyle = status === 'retrying'
      ? 'animation:tpPulse 1s ease-in-out infinite;'
      : status === 'ok' ? 'animation:tpGlow 3s ease-in-out infinite;' : '';

    b.innerHTML = `
      <span style="width:7px;height:7px;border-radius:50%;background:${cfg.dot};
        display:inline-block;${animStyle}"></span>
      <span style="color:${cfg.text};font-weight:600">${cfg.label}</span>
      ${detail ? `<span style="color:#333">|</span><span style="color:#555;font-size:10px">${detail}</span>` : ''}
      ${totalRequests > 0 ? `<span style="color:#222;font-size:10px">${totalRequests}req</span>` : ''}
    `;
  }

  function recordSuccess() {
    consecutiveErrors = 0;
    totalRequests++;
    setStatus('ok');
  }

  function recordError(isRateLimit) {
    consecutiveErrors++;
    totalErrors++;
    totalRequests++;
    lastError = new Date().toLocaleTimeString();
    if (isRateLimit) setStatus('retrying', 'rate limit');
    else if (consecutiveErrors >= 3) setStatus('error', `${consecutiveErrors}x`);
    else setStatus('degraded', 'recon.');
  }

  function recordRetrying(attempt, waitSec) {
    setStatus('retrying', `${attempt}/${TP_CONFIG.MAX_RETRIES} • ${waitSec}s`);
  }

  return { setStatus, recordSuccess, recordError, recordRetrying, getBadge };
})();

// ═══════════════════════════════════════════════════════════
// LOADING SKELETON (substitui spinner genérico)
// ═══════════════════════════════════════════════════════════
function showSkeleton(elementId, lines = 4) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const skLines = Array.from({length: lines}, (_, i) => `
    <div style="height:${i === 0 ? 20 : 14}px;border-radius:6px;margin-bottom:8px;
      width:${[90,75,85,60,70][i % 5]}%;
      background:linear-gradient(90deg,#1a1f2e 25%,#252b3d 50%,#1a1f2e 75%);
      background-size:200% 100%;animation:tpShimmer 1.5s infinite;"></div>
  `).join('');
  el.innerHTML = `<div style="padding:16px">${skLines}</div>`;
}

// ═══════════════════════════════════════════════════════════
// DEBOUNCE + THROTTLE (evita spam de botões)
// ═══════════════════════════════════════════════════════════
const debounceMap = new Map();
function debounce(fn, key, ms = 500) {
  if (debounceMap.has(key)) {
    clearTimeout(debounceMap.get(key));
  }
  debounceMap.set(key, setTimeout(() => {
    debounceMap.delete(key);
    fn();
  }, ms));
}

const throttleMap = new Map();
function throttle(fn, key, ms = 2000) {
  if (throttleMap.has(key)) {
    Toast.warning('Aguarde um momento antes de tentar novamente.');
    return false;
  }
  throttleMap.set(key, true);
  setTimeout(() => throttleMap.delete(key), ms);
  fn();
  return true;
}

// ═══════════════════════════════════════════════════════════
// SMART API WRAPPER v2 — Auto-Retry + Cache + Sanitize
// ═══════════════════════════════════════════════════════════
const TitlePilotAI = (() => {

  function classifyError(msg) {
    if (!msg) return 'unknown';
    const m = String(msg).toLowerCase();
    if (m.includes('rate limit') || m.includes('429') || m.includes('quota') || m.includes('tpm'))
      return 'rate_limit';
    if (m.includes('ai service') || m.includes('could not reach') || m.includes('ai error'))
      return 'ai_unavailable';
    if (m.includes('json') || m.includes('parse') || m.includes('unexpected format'))
      return 'parse_error';
    if (m.includes('network') || m.includes('fetch') || m.includes('failed to fetch'))
      return 'network';
    if (m.includes('timeout') || m.includes('abort'))
      return 'timeout';
    return 'unknown';
  }

  function friendlyMsg(type, attempt) {
    const delay = TP_CONFIG.RETRY_DELAYS[attempt] || 30;
    return {
      rate_limit:     `⏱️ IA sobrecarregada. Aguardando ${delay}s... (tentativa ${attempt+1}/${TP_CONFIG.MAX_RETRIES})`,
      ai_unavailable: `🤖 IA indisponível. Tentando novamente em ${delay}s...`,
      network:        '📡 Sem conexão. Verifique sua internet.',
      timeout:        '⌛ A IA demorou muito. Tentando novamente...',
      parse_error:    '🔧 Formato inesperado da IA. Tentando novamente...',
      unknown:        `🔄 Erro temporário. Tentando novamente (${attempt+1}/${TP_CONFIG.MAX_RETRIES})...`,
    }[type] || `Tentando novamente (${attempt+1})...`;
  }

  // Overlay de retry com countdown
  let retryActive = false;
  function showRetryOverlay(waitSec, attempt, type) {
    if (retryActive) {
      // Update existing
      const cd = document.getElementById('tp-retry-countdown');
      if (cd) cd.textContent = waitSec + 's';
      return;
    }
    retryActive = true;
    const existing = document.getElementById('tp-retry-overlay');
    if (existing) existing.remove();

    const ov = document.createElement('div');
    ov.id = 'tp-retry-overlay';
    ov.style.cssText = `
      position:fixed;bottom:55px;left:50%;transform:translateX(-50%);
      background:linear-gradient(135deg,#12082a,#0a1220);
      border:1px solid #8b5cf6;border-radius:14px;padding:14px 22px;
      z-index:99998;display:flex;align-items:center;gap:14px;
      box-shadow:0 8px 40px rgba(139,92,246,0.25);min-width:320px;
      font-family:'Inter',sans-serif;
    `;
    ov.innerHTML = `
      <div style="width:34px;height:34px;border-radius:50%;
        border:3px solid #1a0d2e;border-top-color:#8b5cf6;
        animation:tpSpin 0.8s linear infinite;flex-shrink:0"></div>
      <div style="flex:1">
        <div style="color:#8b5cf6;font-weight:700;font-size:12px;
          letter-spacing:0.3px">🛡️ AUTO-RECOVERY ATIVO</div>
        <div style="color:#999;font-size:11px;margin-top:3px">
          Tentativa <b style="color:#fff">${attempt+1}</b> de
          <b style="color:#fff">${TP_CONFIG.MAX_RETRIES}</b> —
          aguardando <b id="tp-retry-countdown" style="color:#f59e0b">${waitSec}s</b>
        </div>
      </div>
      <button onclick="document.getElementById('tp-retry-overlay')?.remove()"
        style="background:none;border:1px solid #333;color:#666;border-radius:6px;
          padding:4px 8px;cursor:pointer;font-size:11px">Cancelar</button>
    `;
    document.body.appendChild(ov);

    let rem = waitSec;
    const cd = ov.querySelector('#tp-retry-countdown');
    const timer = setInterval(() => {
      rem--;
      if (cd) cd.textContent = rem + 's';
      if (rem <= 0) {
        clearInterval(timer);
        retryActive = false;
        ov.remove();
      }
    }, 1000);
  }

  // Card de erro com botão retry
  function showErrorCard(elId, msg, type, url, data) {
    const el = typeof elId === 'string' ? document.getElementById(elId) : elId;
    if (!el) return;

    const icons = { rate_limit:'⏱️', ai_unavailable:'🤖', network:'📡', timeout:'⌛', parse_error:'🔧', unknown:'⚠️' };
    const tips = {
      rate_limit:     'A IA atingiu o limite de tokens por minuto. Aguarde 30 segundos e tente novamente.',
      ai_unavailable: 'O serviço de IA está temporariamente indisponível. Tente novamente em instantes.',
      network:        'Verifique sua conexão com a internet e tente novamente.',
      timeout:        'A IA demorou muito para responder. Tente novamente ou simplifique o prompt.',
      parse_error:    'A IA retornou um formato inesperado. Isso foi registrado e já tentamos corrigir.',
      unknown:        'Erro inesperado. Tente novamente. Se persistir, recarregue a ferramenta (F5).',
    };

    const id = 'retry-' + Date.now();
    el.innerHTML = `
      <div style="background:linear-gradient(135deg,#1a0d0d,#130808);border:1px solid #e9456040;
        border-left:3px solid #e94560;border-radius:12px;padding:20px;text-align:center;
        font-family:'Inter',sans-serif">
        <div style="font-size:40px;margin-bottom:10px">${icons[type] || '⚠️'}</div>
        <h3 style="color:#e94560;margin:0 0 8px;font-size:16px">Erro Temporário</h3>
        <p style="font-size:13px;color:#aaa;margin:0 0 14px;line-height:1.6">
          ${tips[type] || tips.unknown}
        </p>
        <div style="background:#0d1117;border-radius:6px;padding:6px 10px;margin-bottom:16px;
          font-size:10px;color:#444;text-align:left;word-break:break-all;font-family:monospace">
          ${(msg || '').substring(0, 100)}
        </div>
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
          <button id="${id}" class="btn-primary" style="background:#8b5cf6;font-size:12px;padding:8px 16px"
            onclick="TitlePilotAI._retryBtn('${id}','${url}','${btoa(unescape(encodeURIComponent(JSON.stringify(data))))}','${typeof elId==='string'?elId:''}')">
            🔄 Tentar Novamente
          </button>
          <button class="btn-secondary" style="font-size:12px;padding:8px 16px"
            onclick="this.closest('[style]').closest('.result-area,.section').querySelector('[class*=result]')?.innerHTML?.set?.('') || this.closest('[style]').remove()">
            ✖ Fechar
          </button>
        </div>
      </div>`;
  }

  async function _retryBtn(btnId, url, dataB64, elId) {
    try {
      const data = JSON.parse(decodeURIComponent(escape(atob(dataB64))));
      const btn = document.getElementById(btnId);
      if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Tentando...'; }
      const result = await call(url, data, { resultEl: elId });
      if (result && !result.error) Toast.success('✅ Recuperado com sucesso!');
    } catch(e) {
      Toast.error('Falha na nova tentativa: ' + e.message);
    }
  }

  // Reparar JSON quebrado do lado do cliente
  function tryRepairJSON(text) {
    if (!text || !text.trim()) return null;
    try {
      let c = text.trim()
        .replace(/^```json\s*/i, '').replace(/^```\s*/i, '')
        .replace(/\s*```\s*$/i, '').trim();
      const fb = c.search(/[\[{]/);
      if (fb > 0) c = c.substring(fb);
      const lb = Math.max(c.lastIndexOf('}'), c.lastIndexOf(']'));
      if (lb > 0) c = c.substring(0, lb + 1);
      c = c.replace(/,\s*([}\]])/g, '$1');
      return JSON.parse(c);
    } catch { return null; }
  }

  // MAIN CALL FUNCTION
  async function call(url, data, options = {}) {
    const { silent = false, resultEl = null, useCache = true, loadingText } = options;

    // Sanitize input
    const safeData = sanitizeInput(data || {});

    // Check cache first
    if (useCache) {
      const cached = SmartCache.get(url, safeData);
      if (cached) {
        if (typeof loading === 'function') loading(false);
        if (!silent) Toast.info('💾 Resultado do cache (instantâneo)', 2000);
        return cached;
      }
    }

    let lastError = null;

    for (let attempt = 0; attempt < TP_CONFIG.MAX_RETRIES; attempt++) {
      if (attempt === 0 && typeof loading === 'function') {
        loading(true, loadingText || 'Consultando IA...');
      }

      try {
        const ctrl = new AbortController();
        const tid = setTimeout(() => ctrl.abort(), TP_CONFIG.REQUEST_TIMEOUT);

        const resp = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(safeData),
          signal: ctrl.signal,
        });
        clearTimeout(tid);

        const text = await resp.text();
        let json;
        try {
          json = JSON.parse(text);
        } catch {
          json = tryRepairJSON(text);
          if (!json) {
            lastError = { type: 'parse_error', msg: `Resposta inválida HTTP ${resp.status}` };
            if (attempt < TP_CONFIG.MAX_RETRIES - 1) {
              const delay = TP_CONFIG.RETRY_DELAYS[attempt];
              HealthMonitor.recordRetrying(attempt + 1, delay);
              if (!silent) Toast.retry(friendlyMsg('parse_error', attempt));
              showRetryOverlay(delay, attempt, 'parse_error');
              await sleep(delay * 1000);
              continue;
            }
            break;
          }
        }

        // AI error handling
        if (json && json.error) {
          const errType = classifyError(json.error);
          lastError = { type: errType, msg: json.error };

          if ((errType === 'rate_limit' || errType === 'ai_unavailable') &&
              attempt < TP_CONFIG.MAX_RETRIES - 1) {
            const delay = TP_CONFIG.RETRY_DELAYS[attempt];
            HealthMonitor.recordError(errType === 'rate_limit');
            HealthMonitor.recordRetrying(attempt + 1, delay);
            if (!silent) Toast.retry(friendlyMsg(errType, attempt), (delay + 3) * 1000);
            showRetryOverlay(delay, attempt, errType);
            await sleep(delay * 1000);
            continue;
          }

          if (typeof loading === 'function') loading(false);
          HealthMonitor.recordError(false);
          if (!silent && resultEl) showErrorCard(resultEl, json.error, errType, url, safeData);
          return json;
        }

        // SUCCESS
        if (typeof loading === 'function') loading(false);
        HealthMonitor.recordSuccess();
        if (attempt > 0) Toast.success(`✅ Recuperado! (tentativa ${attempt + 1})`);

        // Cache successful result
        if (useCache) SmartCache.set(url, safeData, json);

        return json;

      } catch (err) {
        const isAbort = err.name === 'AbortError';
        const errType = isAbort ? 'timeout' : 'network';
        lastError = { type: errType, msg: err.message };
        HealthMonitor.recordError(false);

        if (attempt < TP_CONFIG.MAX_RETRIES - 1) {
          const delay = TP_CONFIG.RETRY_DELAYS[attempt];
          HealthMonitor.recordRetrying(attempt + 1, delay);
          if (!silent) Toast.retry(friendlyMsg(errType, attempt));
          showRetryOverlay(delay, attempt, errType);
          await sleep(delay * 1000);
        }
      }
    }

    // All retries exhausted
    if (typeof loading === 'function') loading(false);
    HealthMonitor.recordError(false);
    const finalMsg = lastError?.msg || 'Não foi possível conectar ao servidor.';
    if (!silent) {
      Toast.error(`❌ Falha após ${TP_CONFIG.MAX_RETRIES} tentativas. ${finalMsg.substring(0, 70)}`);
      if (resultEl) showErrorCard(resultEl, finalMsg, lastError?.type || 'unknown', url, safeData);
    }
    return { error: finalMsg };
  }

  // Health check manual
  async function runHealthCheck() {
    HealthMonitor.setStatus('retrying', 'verificando');
    try {
      const r = await fetch('/api/health', { method: 'GET', signal: AbortSignal.timeout(8000) });
      if (r.ok) {
        const d = await r.json();
        const aiOk = d.ai_status === 'ok';
        HealthMonitor.setStatus(aiOk ? 'ok' : 'degraded');
        Toast.info(
          `🤖 IA: ${aiOk ? 'Online ✅' : 'Sem chave ⚠️'} | ` +
          `📺 YT: ${d.youtube_key ? 'Conectado ✅' : 'Sem key ⚠️'} | ` +
          `⏱️ Uptime: ${Math.floor((d.uptime_seconds||0)/60)}min | ` +
          `v${d.version || '?'}`,
          6000
        );
      } else {
        HealthMonitor.setStatus('error', `HTTP ${r.status}`);
        Toast.warning(`Servidor respondeu com HTTP ${r.status}`);
      }
    } catch(e) {
      HealthMonitor.setStatus('offline', 'sem resposta');
      Toast.error('Servidor inacessível: ' + e.message);
    }
  }

  function sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  return { call, runHealthCheck, tryRepairJSON, showSkeleton,
           _retryBtn, SmartCache };
})();

// ═══════════════════════════════════════════════════════════
// COPIAR PARA ÁREA DE TRANSFERÊNCIA (com feedback)
// ═══════════════════════════════════════════════════════════
window.copyToClipboard = async function(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    Toast.copy(`📋 ${label || 'Texto'} copiado!`);
  } catch {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    Toast.copy(`📋 ${label || 'Texto'} copiado!`);
  }
};

// ═══════════════════════════════════════════════════════════
// SUBSTITUIR post() GLOBAL
// ═══════════════════════════════════════════════════════════
window._originalPost = window.post;
window.post = async function(url, data, options = {}) {
  return TitlePilotAI.call(url, data, {
    resultEl:    options.resultEl || null,
    loadingText: options.loadingText || null,
    useCache:    options.useCache !== false,
  });
};

// ═══════════════════════════════════════════════════════════
// CAPTURA DE ERROS GLOBAIS
// ═══════════════════════════════════════════════════════════
window.addEventListener('unhandledrejection', (e) => {
  if (!e.reason) return;
  const msg = e.reason?.message || String(e.reason);
  if (/AbortError|cancelled|ResizeObserver/i.test(msg)) return;
  Toast.warning(`Erro interno recuperado: ${msg.substring(0, 70)}`);
  e.preventDefault();
});

window.addEventListener('error', (e) => {
  const msg = e.error?.message || '';
  if (/Script error|ResizeObserver|extension/i.test(msg)) return;
  console.warn('[TitlePilot Anti-Bug] Erro JS capturado:', msg);
});

// ═══════════════════════════════════════════════════════════
// INJECT CSS — Animações e estilos do sistema anti-bug
// ═══════════════════════════════════════════════════════════
(function injectStyles() {
  const s = document.createElement('style');
  s.id = 'tp-antibug-styles';
  s.textContent = `
    @keyframes tpPulse {
      0%,100% { opacity:1; box-shadow:0 0 5px currentColor; }
      50% { opacity:0.5; box-shadow:0 0 2px currentColor; }
    }
    @keyframes tpGlow {
      0%,100% { box-shadow:0 0 4px #4ecca3; }
      50% { box-shadow:0 0 10px #4ecca3; }
    }
    @keyframes tpSpin {
      from { transform:rotate(0deg); }
      to   { transform:rotate(360deg); }
    }
    @keyframes tpShimmer {
      0%   { background-position:200% 0; }
      100% { background-position:-200% 0; }
    }
    @keyframes tpSlideIn {
      from { opacity:0; transform:translateY(8px); }
      to   { opacity:1; transform:translateY(0); }
    }
    #toast-container * { box-sizing:border-box; }
    #health-badge:hover {
      border-color:#8b5cf680 !important;
      background:#0d1020 !important;
      transform:scale(1.02);
    }
    .tp-skeleton { animation:tpShimmer 1.5s ease-in-out infinite; }
    .tp-fade-in  { animation:tpSlideIn 0.3s ease; }
    /* Botão copiar inline */
    .tp-copy-btn {
      background:none;border:1px solid #333;color:#888;
      border-radius:4px;padding:2px 6px;font-size:10px;
      cursor:pointer;margin-left:6px;vertical-align:middle;
      transition:all 0.2s;
    }
    .tp-copy-btn:hover { border-color:#4ecca3;color:#4ecca3; }
  `;
  document.head.appendChild(s);
})();

// ═══════════════════════════════════════════════════════════
// INICIALIZAÇÃO
// ═══════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  HealthMonitor.setStatus('ok');

  // Welcome toast
  setTimeout(() => {
    Toast.success('TitlePilot Pro v2.1 — Sistema anti-bug ativo 🛡️', 3000);
  }, 800);

  // Auto health check silencioso
  setInterval(async () => {
    try {
      const r = await fetch('/api/health', { method:'GET', signal: AbortSignal.timeout(5000) });
      if (r.ok) HealthMonitor.recordSuccess();
      else HealthMonitor.setStatus('degraded', 'ping anormal');
    } catch {
      HealthMonitor.setStatus('offline', 'sem ping');
    }
  }, TP_CONFIG.HEALTH_INTERVAL);

  // Auto-adicionar botões de copiar em resultados de título
  const observer = new MutationObserver((mutations) => {
    mutations.forEach(m => {
      m.addedNodes.forEach(node => {
        if (node.nodeType !== 1) return;
        node.querySelectorAll?.('.title-text').forEach(el => {
          if (el.querySelector('.tp-copy-btn')) return;
          const btn = document.createElement('button');
          btn.className = 'tp-copy-btn';
          btn.title = 'Copiar título';
          btn.textContent = '📋';
          btn.onclick = (e) => {
            e.stopPropagation();
            copyToClipboard(el.textContent.trim(), 'Título');
          };
          el.appendChild(btn);
        });
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });

  console.log(
    '%c🛡️ TitlePilot Anti-Bug v2.0 ativo\n' +
    '%c✓ Auto-retry  ✓ Cache  ✓ Sanitize  ✓ Toast  ✓ Health Monitor  ✓ Copy buttons',
    'color:#4ecca3;font-weight:bold;font-size:13px',
    'color:#888;font-size:11px'
  );
});

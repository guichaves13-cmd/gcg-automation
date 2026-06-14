/**
 * TitlePilot Pro — Sistema Anti-Bug Inteligente v1.0
 * Camadas: Auto-retry, Toast UI, Health Monitor, Error Recovery
 */

// ═══════════════════════════════════════════════════════════
// TOAST NOTIFICATION SYSTEM (substitui alert())
// ═══════════════════════════════════════════════════════════
const Toast = (() => {
  let container;
  function getContainer() {
    if (!container) {
      container = document.createElement('div');
      container.id = 'toast-container';
      container.style.cssText = `
        position:fixed;top:20px;right:20px;z-index:99999;
        display:flex;flex-direction:column;gap:10px;
        pointer-events:none;max-width:380px;
      `;
      document.body.appendChild(container);
    }
    return container;
  }

  function show(message, type = 'info', duration = 5000) {
    const colors = {
      success: { bg: '#0d2e1a', border: '#4ecca3', icon: '✅', text: '#4ecca3' },
      error:   { bg: '#2e0d0d', border: '#e94560', icon: '❌', text: '#e94560' },
      warning: { bg: '#2e1e0d', border: '#f59e0b', icon: '⚠️', text: '#f59e0b' },
      info:    { bg: '#0d1a2e', border: '#3b82f6', icon: 'ℹ️', text: '#3b82f6' },
      retry:   { bg: '#1a0d2e', border: '#8b5cf6', icon: '🔄', text: '#8b5cf6' },
    };
    const c = colors[type] || colors.info;
    const el = document.createElement('div');
    el.style.cssText = `
      background:${c.bg};border:1px solid ${c.border};border-left:4px solid ${c.border};
      border-radius:8px;padding:12px 16px;color:#fff;font-size:13px;
      pointer-events:all;cursor:pointer;opacity:0;
      transform:translateX(30px);transition:all 0.3s ease;
      box-shadow:0 4px 20px rgba(0,0,0,0.4);
      display:flex;align-items:flex-start;gap:10px;max-width:100%;
    `;
    el.innerHTML = `
      <span style="font-size:16px;flex-shrink:0">${c.icon}</span>
      <div style="flex:1">
        <div style="color:${c.text};font-weight:600;font-size:12px;text-transform:uppercase;margin-bottom:2px">${type.toUpperCase()}</div>
        <div style="color:#ccc;line-height:1.4">${message}</div>
      </div>
      <span style="color:#555;cursor:pointer;flex-shrink:0;font-size:18px;line-height:1" onclick="this.closest('[id^=toast]').remove()">×</span>
    `;
    el.id = 'toast-' + Date.now();
    getContainer().appendChild(el);
    requestAnimationFrame(() => {
      el.style.opacity = '1';
      el.style.transform = 'translateX(0)';
    });
    const removeTimer = setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateX(30px)';
      setTimeout(() => el.remove(), 300);
    }, duration);
    el.onclick = () => {
      clearTimeout(removeTimer);
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 300);
    };
    return el;
  }

  return {
    success: (msg, dur) => show(msg, 'success', dur || 4000),
    error:   (msg, dur) => show(msg, 'error', dur || 7000),
    warning: (msg, dur) => show(msg, 'warning', dur || 5000),
    info:    (msg, dur) => show(msg, 'info', dur || 4000),
    retry:   (msg, dur) => show(msg, 'retry', dur || 6000),
  };
})();

// Sobrescrever alert() global com toast
window._originalAlert = window.alert;
window.alert = function(msg) {
  if (typeof msg === 'string' && msg.length < 200) {
    Toast.warning(msg);
  } else {
    window._originalAlert(msg);
  }
};

// ═══════════════════════════════════════════════════════════
// HEALTH MONITOR (badge no topo)
// ═══════════════════════════════════════════════════════════
const HealthMonitor = (() => {
  let badge;
  let consecutiveErrors = 0;
  let lastSuccess = Date.now();

  function getBadge() {
    if (!badge) {
      badge = document.createElement('div');
      badge.id = 'health-badge';
      badge.style.cssText = `
        position:fixed;bottom:16px;left:16px;z-index:9999;
        display:flex;align-items:center;gap:6px;
        background:#0d1117;border:1px solid #30363d;
        border-radius:20px;padding:6px 12px;font-size:11px;
        cursor:pointer;transition:all 0.3s;
      `;
      badge.onclick = () => TitlePilotAI.runHealthCheck();
      document.body.appendChild(badge);
    }
    return badge;
  }

  function setStatus(status, detail) {
    const b = getBadge();
    const configs = {
      ok:       { dot: '#4ecca3', text: '#4ecca3', label: '🤖 IA: Online' },
      retrying: { dot: '#f59e0b', text: '#f59e0b', label: '🔄 IA: Retrying...' },
      error:    { dot: '#e94560', text: '#e94560', label: '⚠️ IA: Erro' },
      degraded: { dot: '#f59e0b', text: '#f59e0b', label: '⚡ IA: Degradado' },
    };
    const cfg = configs[status] || configs.ok;
    b.innerHTML = `
      <span style="width:8px;height:8px;border-radius:50%;background:${cfg.dot};
        box-shadow:0 0 6px ${cfg.dot};display:inline-block;
        ${status === 'retrying' ? 'animation:pulse 1s infinite;' : ''}"></span>
      <span style="color:${cfg.text};font-weight:600">${cfg.label}</span>
      ${detail ? `<span style="color:#555">•</span><span style="color:#777;font-size:10px">${detail}</span>` : ''}
    `;
  }

  function recordSuccess() {
    consecutiveErrors = 0;
    lastSuccess = Date.now();
    setStatus('ok');
  }

  function recordError(isRateLimit) {
    consecutiveErrors++;
    if (isRateLimit) {
      setStatus('retrying', 'limite IA');
    } else if (consecutiveErrors >= 3) {
      setStatus('error', `${consecutiveErrors} erros`);
    } else {
      setStatus('degraded', 'reconectando');
    }
  }

  function recordRetrying(attempt, waitSec) {
    setStatus('retrying', `tentativa ${attempt} • ${waitSec}s`);
  }

  return { setStatus, recordSuccess, recordError, recordRetrying, getBadge };
})();

// ═══════════════════════════════════════════════════════════
// SMART API WRAPPER com Auto-Retry
// ═══════════════════════════════════════════════════════════
const TitlePilotAI = (() => {
  const MAX_RETRIES = 3;
  const RETRY_DELAYS = [8, 20, 45]; // segundos

  // Detectar tipo de erro a partir da resposta
  function classifyError(errorMsg) {
    if (!errorMsg) return 'unknown';
    const m = errorMsg.toLowerCase();
    if (m.includes('rate limit') || m.includes('429') || m.includes('quota'))
      return 'rate_limit';
    if (m.includes('ai service') || m.includes('could not reach'))
      return 'ai_unavailable';
    if (m.includes('json') || m.includes('parse'))
      return 'parse_error';
    if (m.includes('network') || m.includes('fetch') || m.includes('failed to fetch'))
      return 'network';
    if (m.includes('timeout'))
      return 'timeout';
    return 'unknown';
  }

  // Mensagens amigáveis para o usuário
  function friendlyMessage(type, attempt) {
    const msgs = {
      rate_limit:    `IA sobrecarregada — aguardando ${RETRY_DELAYS[attempt] || 45}s para nova tentativa... (${attempt+1}/${MAX_RETRIES})`,
      ai_unavailable:`IA temporariamente indisponível. Tentando novamente em ${RETRY_DELAYS[attempt] || 20}s...`,
      network:       'Problema de conexão — verifique sua internet.',
      timeout:       'A IA demorou muito. Tentando novamente...',
      parse_error:   'Resposta inesperada da IA. Tentando novamente...',
      unknown:       `Erro temporário. Tentando novamente (${attempt+1}/${MAX_RETRIES})...`,
    };
    return msgs[type] || msgs.unknown;
  }

  // Mostrar overlay de retry com countdown
  let retryOverlayActive = false;
  function showRetryOverlay(waitSec, attempt, total) {
    if (retryOverlayActive) return;
    retryOverlayActive = true;
    const existing = document.getElementById('retry-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'retry-overlay';
    overlay.style.cssText = `
      position:fixed;bottom:60px;left:50%;transform:translateX(-50%);
      background:linear-gradient(135deg,#1a0d2e,#0d1a2e);
      border:1px solid #8b5cf6;border-radius:12px;padding:16px 24px;
      z-index:99998;display:flex;align-items:center;gap:12px;
      box-shadow:0 8px 32px rgba(139,92,246,0.3);min-width:300px;
    `;
    overlay.innerHTML = `
      <div style="font-size:24px;animation:spin 2s linear infinite">🔄</div>
      <div style="flex:1">
        <div style="color:#8b5cf6;font-weight:700;font-size:13px">Auto-Recovery Ativo</div>
        <div style="color:#aaa;font-size:12px;margin-top:2px">
          Tentativa <b style="color:#fff">${attempt+1}</b> de <b style="color:#fff">${total}</b>
          — aguardando <b id="retry-countdown" style="color:#f59e0b">${waitSec}s</b>
        </div>
      </div>
      <div style="width:40px;height:40px;border-radius:50%;border:3px solid #1a0d2e;
        border-top-color:#8b5cf6;animation:spin 1s linear infinite"></div>
    `;
    document.body.appendChild(overlay);

    // Countdown
    let remaining = waitSec;
    const countdownEl = overlay.querySelector('#retry-countdown');
    const interval = setInterval(() => {
      remaining--;
      if (countdownEl) countdownEl.textContent = remaining + 's';
      if (remaining <= 0) {
        clearInterval(interval);
        retryOverlayActive = false;
        overlay.remove();
      }
    }, 1000);
  }

  // Função principal de chamada com retry
  async function call(url, data, options = {}) {
    const { silent = false, resultEl = null } = options;
    let lastError = null;

    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      // Show loading on first attempt
      if (attempt === 0) {
        if (typeof loading === 'function') {
          loading(true, options.loadingText || 'Consultando IA...');
        }
      }

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000); // 2min timeout

        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(data),
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        const text = await response.text();

        // Try to parse JSON
        let json;
        try {
          json = JSON.parse(text);
        } catch (e) {
          // Try to repair JSON
          json = tryRepairJSON(text);
          if (!json) {
            lastError = { type: 'parse_error', msg: `Resposta inválida da IA (${response.status})` };
            if (attempt < MAX_RETRIES - 1) {
              const delay = RETRY_DELAYS[attempt] || 10;
              HealthMonitor.recordRetrying(attempt + 1, delay);
              Toast.retry(friendlyMessage('parse_error', attempt));
              showRetryOverlay(delay, attempt, MAX_RETRIES);
              await sleep(delay * 1000);
              continue;
            }
            break;
          }
        }

        // Check for AI errors
        if (json && json.error) {
          const errType = classifyError(json.error);
          lastError = { type: errType, msg: json.error };

          if (errType === 'rate_limit' || errType === 'ai_unavailable') {
            if (attempt < MAX_RETRIES - 1) {
              const delay = RETRY_DELAYS[attempt] || 15;
              HealthMonitor.recordRetrying(attempt + 1, delay);
              HealthMonitor.recordError(errType === 'rate_limit');
              if (!silent) Toast.retry(friendlyMessage(errType, attempt), (delay + 2) * 1000);
              showRetryOverlay(delay, attempt, MAX_RETRIES);
              await sleep(delay * 1000);
              continue;
            }
          }
          // Non-retriable error — return immediately
          if (typeof loading === 'function') loading(false);
          HealthMonitor.recordError(false);
          if (!silent && resultEl) {
            showErrorCard(resultEl, json.error, errType, url, data, options);
          }
          return json;
        }

        // SUCCESS
        if (typeof loading === 'function') loading(false);
        HealthMonitor.recordSuccess();
        if (attempt > 0) {
          Toast.success(`Recuperado com sucesso! (tentativa ${attempt + 1})`);
        }
        return json;

      } catch (fetchErr) {
        const isAbort = fetchErr.name === 'AbortError';
        const errType = isAbort ? 'timeout' : 'network';
        lastError = { type: errType, msg: fetchErr.message };
        HealthMonitor.recordError(false);

        if (attempt < MAX_RETRIES - 1) {
          const delay = RETRY_DELAYS[attempt] || 10;
          HealthMonitor.recordRetrying(attempt + 1, delay);
          if (!silent) Toast.retry(friendlyMessage(errType, attempt));
          showRetryOverlay(delay, attempt, MAX_RETRIES);
          await sleep(delay * 1000);
        }
      }
    }

    // All retries exhausted
    if (typeof loading === 'function') loading(false);
    HealthMonitor.recordError(false);
    const finalMsg = lastError
      ? `${lastError.msg}`
      : 'Não foi possível conectar ao servidor.';

    if (!silent) {
      Toast.error(`❌ Falha após ${MAX_RETRIES} tentativas. ${finalMsg.substring(0, 80)}`);
      if (resultEl) {
        showErrorCard(resultEl, finalMsg, lastError?.type || 'unknown', url, data, options);
      }
    }
    return { error: finalMsg };
  }

  // Mostrar card de erro com botão "Tentar Novamente"
  function showErrorCard(resultElId, errorMsg, errType, url, data, options) {
    const el = typeof resultElId === 'string'
      ? document.getElementById(resultElId)
      : resultElId;
    if (!el) return;

    const icons = {
      rate_limit: '⏱️', ai_unavailable: '🤖', network: '📡',
      timeout: '⌛', parse_error: '🔧', unknown: '⚠️',
    };
    const tips = {
      rate_limit: 'O limite de tokens da IA foi atingido. Aguarde 30s e tente novamente.',
      ai_unavailable: 'O serviço de IA está temporariamente indisponível.',
      network: 'Verifique sua conexão com a internet.',
      timeout: 'A IA demorou muito para responder. Tente um prompt mais curto.',
      parse_error: 'A resposta da IA teve formato inesperado. Tente novamente.',
      unknown: 'Erro inesperado. Tente novamente ou recarregue a ferramenta.',
    };

    const retryBtnId = 'retry-btn-' + Date.now();
    el.innerHTML = `
      <div style="background:#1a0d0d;border:1px solid #e94560;border-left:4px solid #e94560;
        border-radius:12px;padding:20px;text-align:center">
        <div style="font-size:36px;margin-bottom:8px">${icons[errType] || '⚠️'}</div>
        <h3 style="color:#e94560;margin-bottom:8px">Erro Temporário</h3>
        <div style="font-size:13px;color:#aaa;margin-bottom:12px;line-height:1.5">
          ${tips[errType] || tips.unknown}
        </div>
        <div style="background:#0d1117;border-radius:8px;padding:8px;margin-bottom:16px;
          font-size:11px;color:#555;text-align:left;word-break:break-all">
          ${escHtml ? escHtml(errorMsg.substring(0, 120)) : errorMsg.substring(0, 120)}
        </div>
        <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
          <button id="${retryBtnId}" class="btn-primary" style="background:#8b5cf6"
            onclick="TitlePilotAI._retryFromCard('${retryBtnId}', '${url}', '${btoa(JSON.stringify(data))}', '${typeof resultElId === 'string' ? resultElId : ''}', ${JSON.stringify(Object.keys(options))})">
            🔄 Tentar Novamente
          </button>
          <button class="btn-secondary" onclick="this.closest('.result-area').innerHTML=''">
            ✖ Fechar
          </button>
        </div>
      </div>`;
  }

  // Retry a partir do card de erro
  async function retryFromCard(btnId, url, dataB64, resultElId, optKeys) {
    try {
      const data = JSON.parse(atob(dataB64));
      const btn = document.getElementById(btnId);
      if (btn) { btn.disabled = true; btn.textContent = '⏳ Tentando...'; }
      const result = await call(url, data, { resultEl: resultElId });
      // Trigger the page's own result handler if possible
      if (result && !result.error) {
        Toast.success('Sucesso! Atualize a página se necessário.');
      }
    } catch(e) {
      Toast.error('Falha na nova tentativa: ' + e.message);
    }
  }

  // JSON repair — tenta consertar JSON quebrado da IA
  function tryRepairJSON(text) {
    if (!text || !text.trim()) return null;
    try {
      // Strip markdown code blocks
      let cleaned = text.trim()
        .replace(/^```json\s*/i, '').replace(/^```\s*/i, '')
        .replace(/\s*```$/i, '').trim();
      // Find first { or [
      const firstBrace = cleaned.search(/[\[{]/);
      if (firstBrace > 0) cleaned = cleaned.substring(firstBrace);
      // Find last } or ]
      const lastClose = Math.max(cleaned.lastIndexOf('}'), cleaned.lastIndexOf(']'));
      if (lastClose > 0) cleaned = cleaned.substring(0, lastClose + 1);
      // Fix common issues
      cleaned = cleaned
        .replace(/,\s*([}\]])/g, '$1')  // trailing commas
        .replace(/([{,]\s*)(\w+)\s*:/g, '$1"$2":')  // unquoted keys
        .replace(/:\s*'([^']*)'/g, ':"$1"');  // single-quote values
      return JSON.parse(cleaned);
    } catch(e) {
      return null;
    }
  }

  // Health check
  async function runHealthCheck() {
    Toast.info('Verificando saúde do sistema...');
    try {
      const r = await fetch('/api/health', { method: 'GET' });
      if (r.ok) {
        const d = await r.json();
        const status = d.ai_status === 'ok' ? 'IA Online ✅' : 'IA Degradada ⚠️';
        Toast.info(`Sistema: ${status} | Uptime: ${d.uptime_seconds || '?'}s`);
      } else {
        Toast.warning('Servidor respondendo mas com status anormal');
      }
    } catch(e) {
      Toast.error('Servidor inacessível: ' + e.message);
    }
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  return { call, runHealthCheck, tryRepairJSON, _retryFromCard: retryFromCard };
})();

// ═══════════════════════════════════════════════════════════
// SUBSTITUIR FUNÇÃO post() GLOBAL com versão inteligente
// ═══════════════════════════════════════════════════════════
window._originalPost = window.post;

window.post = async function(url, data, options = {}) {
  // Options passadas pela própria página
  const resultEl = options.resultEl || null;
  const loadingText = options.loadingText || null;
  return TitlePilotAI.call(url, data, { resultEl, loadingText });
};

// ═══════════════════════════════════════════════════════════
// INTERCEPTAR ERROS GLOBAIS não tratados
// ═══════════════════════════════════════════════════════════
window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason;
  if (!reason) return;
  const msg = reason.message || String(reason);
  // Ignorar erros de cancelamento normais
  if (msg.includes('AbortError') || msg.includes('cancelled')) return;
  Toast.warning(`Erro interno recuperado: ${msg.substring(0, 80)}`);
  event.preventDefault(); // Prevenir que o erro apareça no console como fatal
});

window.addEventListener('error', (event) => {
  if (!event.error) return;
  const msg = event.error.message || '';
  if (msg.includes('Script error') || msg.includes('ResizeObserver')) return;
  Toast.warning(`Erro JS recuperado: ${msg.substring(0, 60)}`);
});

// ═══════════════════════════════════════════════════════════
// CSS INJECTOR (animações necessárias)
// ═══════════════════════════════════════════════════════════
(function injectCSS() {
  const style = document.createElement('style');
  style.textContent = `
    @keyframes pulse {
      0%, 100% { opacity: 1; box-shadow: 0 0 6px currentColor; }
      50% { opacity: 0.5; box-shadow: 0 0 2px currentColor; }
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    #toast-container * { box-sizing: border-box; }
    #health-badge { user-select: none; }
    #health-badge:hover { border-color: #8b5cf6 !important; transform: scale(1.02); }
    .retry-pulse { animation: pulse 1s ease-in-out infinite; }
  `;
  document.head.appendChild(style);
})();

// ═══════════════════════════════════════════════════════════
// INICIALIZAÇÃO
// ═══════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
  // Badge de status
  HealthMonitor.setStatus('ok');
  Toast.success('TitlePilot Pro pronto! Sistema anti-bug ativo.', 3000);

  // Auto health check a cada 5 minutos (silencioso)
  setInterval(async () => {
    try {
      const r = await fetch('/api/health', { method: 'GET' });
      if (!r.ok) HealthMonitor.setStatus('degraded', 'ping failed');
      else HealthMonitor.recordSuccess();
    } catch(e) {
      HealthMonitor.setStatus('error', 'offline');
    }
  }, 5 * 60 * 1000);

  console.log('%c[TitlePilot Anti-Bug] Sistema ativo — auto-retry, toast e health monitor iniciados', 
    'color:#4ecca3;font-weight:bold;font-size:12px');
});

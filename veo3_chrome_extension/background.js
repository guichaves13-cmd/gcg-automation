// Background Service Worker for Veo 3 Automation
// Handles video downloads, background timers, and AI prompt generation using Chrome APIs

// Store pending alarms callbacks
const pendingAlarms = new Map();

// ==================== AI GENERATION STATE ====================
let generationInProgress = false;

async function runGeminiGeneration(apiKey, audioBase64, audioMime, audioDurationSeconds, referenceImages, numScenes) {
  generationInProgress = true;
  chrome.storage.local.set({
    generationStatus: {
      running: true,
      statusText: 'Preparando audio...',
      error: null,
      result: null
    }
  });

  try {
    const parts = [];

    if (referenceImages && referenceImages.length > 0) {
      referenceImages.forEach(img => {
        parts.push({
          inlineData: {
            mimeType: img.mimeType,
            data: img.base64
          }
        });
      });
    }

    parts.push({
      inlineData: {
        mimeType: audioMime,
        data: audioBase64
      }
    });

    const imageContext = referenceImages && referenceImages.length > 0
      ? `\n\nIMPORTANT: I have provided ${referenceImages.length} reference image(s). Use these images as visual reference for the style, characters, settings, and mood of ALL scene prompts you generate. Each prompt should describe scenes that match the visual style shown in these reference images.`
      : '';

    parts.push({
      text: `You are a professional video scene prompt generator for Google Veo 3.

I am providing you an audio file. Listen to it carefully and transcribe/understand the content.

Based on the audio content, generate EXACTLY ${numScenes} scene prompts (one scene for every 8 seconds of audio).

Each scene prompt must:
- Be a detailed visual description of what should appear in the video at that moment
- Match the narration/content of the audio at that specific time segment
- Include camera angles, lighting, mood, and visual details
- Be written in English
- Be a single paragraph (no line breaks within a prompt)${imageContext}

Format: Return ONLY the prompts, each separated by exactly ONE blank line. No numbering, no labels, no extra text. Just the raw prompts separated by blank lines.`
    });

    chrome.storage.local.set({
      generationStatus: {
        running: true,
        statusText: `Enviando para Gemini (${numScenes} cenas)...`,
        error: null,
        result: null
      }
    });

    chrome.storage.local.set({
      generationStatus: {
        running: true,
        statusText: 'Processando com IA (pode levar 1-2 min)...',
        error: null,
        result: null
      }
    });

    const response = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${apiKey}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contents: [{ parts }],
          generationConfig: {
            temperature: 0.7,
            maxOutputTokens: 8192
          }
        })
      }
    );

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      const errMsg = errData?.error?.message || `Erro ${response.status}`;
      if (response.status === 400 && errMsg.includes('API key')) {
        throw new Error('API key invalida. Verifique sua chave em aistudio.google.com/apikey');
      }
      throw new Error(errMsg);
    }

    const data = await response.json();
    const generatedText = data?.candidates?.[0]?.content?.parts?.[0]?.text;

    if (!generatedText) {
      throw new Error('Gemini nao retornou prompts. Tente novamente.');
    }

    const cleanedText = generatedText.trim();
    const promptsArray = cleanedText.split(/\n\s*\n/).filter(p => p.trim().length > 0);

    chrome.storage.local.set({
      generationStatus: {
        running: false,
        statusText: `${promptsArray.length} prompts gerados com sucesso!`,
        error: null,
        result: cleanedText,
        promptCount: promptsArray.length
      }
    });

    // Also update the generatorState with the result
    chrome.storage.local.get(['generatorState'], (res) => {
      const state = res.generatorState || {};
      state.generatedPrompts = cleanedText;
      chrome.storage.local.set({ generatorState: state });
    });

  } catch (error) {
    chrome.storage.local.set({
      generationStatus: {
        running: false,
        statusText: `Erro: ${error.message}`,
        error: error.message,
        result: null
      }
    });
  } finally {
    generationInProgress = false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Start AI prompt generation in background
  if (message.type === 'startGeneration') {
    if (generationInProgress) {
      sendResponse({ success: false, error: 'Geracao ja em andamento' });
      return true;
    }
    runGeminiGeneration(
      message.apiKey,
      message.audioBase64,
      message.audioMime,
      message.audioDurationSeconds,
      message.referenceImages,
      message.numScenes
    );
    sendResponse({ success: true });
    return true;
  }

  // Check generation status
  if (message.type === 'getGenerationStatus') {
    chrome.storage.local.get(['generationStatus'], (result) => {
      sendResponse(result.generationStatus || { running: false, statusText: null, error: null, result: null });
    });
    return true;
  }

  // Clear generation status
  if (message.type === 'clearGenerationStatus') {
    chrome.storage.local.remove('generationStatus');
    sendResponse({ success: true });
    return true;
  }
  if (message.type === 'downloadVideo') {
    handleVideoDownload(message.url, message.filename)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true; // Keep message channel open for async response
  }
  
  if (message.type === 'downloadVideoBatch') {
    handleBatchDownload(message.videos)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: false, error: error.message }));
    return true;
  }
  
  // Background timer - works even when tab is in background
  if (message.type === 'setBackgroundTimer') {
    const alarmName = `timer_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    const delayInMinutes = message.delayMs / 60000; // Convert ms to minutes
    
    // Store the sender tab ID to notify when alarm fires
    pendingAlarms.set(alarmName, {
      tabId: sender.tab.id,
      callback: message.callback
    });
    
    // Chrome alarms minimum is 1 minute for non-persistent, but we can use a workaround
    if (delayInMinutes < 1) {
      // For delays less than 1 minute, use setTimeout in background (it still works better here)
      setTimeout(() => {
        notifyTab(sender.tab.id, message.callback);
        pendingAlarms.delete(alarmName);
      }, message.delayMs);
    } else {
      chrome.alarms.create(alarmName, { delayInMinutes });
    }
    
    sendResponse({ success: true, alarmName });
    return true;
  }
  
  // Cancel a background timer
  if (message.type === 'cancelBackgroundTimer') {
    if (message.alarmName) {
      chrome.alarms.clear(message.alarmName);
      pendingAlarms.delete(message.alarmName);
    }
    sendResponse({ success: true });
    return true;
  }
  
  // Keep-alive ping to prevent service worker from sleeping
  if (message.type === 'keepAlive') {
    sendResponse({ success: true, timestamp: Date.now() });
    return true;
  }

  // Insert text using Chrome DevTools Protocol (creates trusted input events)
  if (message.type === 'cdp-insert-text') {
    const tabId = sender.tab.id;
    const text = message.text;
    
    const doInsert = () => {
      chrome.debugger.sendCommand({ tabId }, 'Input.insertText', { text }, () => {
        if (chrome.runtime.lastError) {
          console.log('[Veo3] InsertText error:', chrome.runtime.lastError.message);
          chrome.debugger.detach({ tabId });
          sendResponse({ success: false, error: chrome.runtime.lastError.message });
          return;
        }
        chrome.debugger.detach({ tabId }, () => {
          sendResponse({ success: true });
        });
      });
    };
    
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        const errMsg = chrome.runtime.lastError.message || '';
        if (errMsg.includes('Already attached') || errMsg.includes('already being inspected')) {
          doInsert();
        } else {
          console.log('[Veo3] Debugger attach error:', errMsg);
          sendResponse({ success: false, error: errMsg });
        }
        return;
      }
      doInsert();
    });
    return true;
  }

  // Clear text field using CDP backspace keys
  if (message.type === 'cdp-clear-field') {
    const tabId = sender.tab.id;
    
    const doClear = () => {
      // Select all with Ctrl+A then delete
      chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent', {
        type: 'keyDown', modifiers: 2, windowsVirtualKeyCode: 65, key: 'a', code: 'KeyA'
      }, () => {
        chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent', {
          type: 'keyUp', modifiers: 2, windowsVirtualKeyCode: 65, key: 'a', code: 'KeyA'
        }, () => {
          chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent', {
            type: 'keyDown', windowsVirtualKeyCode: 8, key: 'Backspace', code: 'Backspace'
          }, () => {
            chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent', {
              type: 'keyUp', windowsVirtualKeyCode: 8, key: 'Backspace', code: 'Backspace'
            }, () => {
              chrome.debugger.detach({ tabId }, () => {
                sendResponse({ success: true });
              });
            });
          });
        });
      });
    };
    
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        const errMsg = chrome.runtime.lastError.message || '';
        if (errMsg.includes('Already attached') || errMsg.includes('already being inspected')) {
          doClear();
        } else {
          sendResponse({ success: false, error: errMsg });
        }
        return;
      }
      doClear();
    });
    return true;
  }

  // Click at coordinates using CDP (trusted click)
  // CRITICAL: Must send mouseMoved BEFORE mousePressed for Chrome to register the click target
  if (message.type === 'cdp-click') {
    const tabId = sender.tab.id;
    const { x, y } = message;
    
    const doClick = () => {
      // Step 1: Move mouse to target (required for Chrome to know WHERE to click)
      chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
        type: 'mouseMoved', x, y, button: 'none'
      }, () => {
        // Step 2: Small delay for move to register
        setTimeout(() => {
          // Step 3: mousePressed
          chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
            type: 'mousePressed', x, y, button: 'left', clickCount: 1, buttons: 1, pointerType: 'mouse'
          }, () => {
            // Step 4: Small delay between press and release (natural click behavior)
            setTimeout(() => {
              // Step 5: mouseReleased
              chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
                type: 'mouseReleased', x, y, button: 'left', clickCount: 1, buttons: 0, pointerType: 'mouse'
              }, () => {
                chrome.debugger.detach({ tabId }, () => {
                  sendResponse({ success: true });
                });
              });
            }, 50);
          });
        }, 30);
      });
    };
    
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        // Debugger may already be attached — try detach first then re-attach
        const errMsg = chrome.runtime.lastError.message || '';
        if (errMsg.includes('Already attached') || errMsg.includes('already being inspected')) {
          // Already attached, just proceed with click
          doClick();
        } else {
          console.log('[Veo3] CDP attach error:', errMsg);
          sendResponse({ success: false, error: errMsg });
        }
        return;
      }
      doClick();
    });
    return true;
  }

  // Find and click using CDP (resolves the infobar shift issue)
  if (message.type === 'cdp-find-and-click') {
    const tabId = sender.tab.id;
    const findScript = message.findScript;
    
    const doFindAndClick = () => {
      // Allow a small delay for the layout shift to finish (infobar animation)
      setTimeout(() => {
        chrome.debugger.sendCommand({ tabId }, 'Runtime.evaluate', {
          expression: findScript,
          returnByValue: true
        }, (result) => {
          if (chrome.runtime.lastError || !result || !result.result || !result.result.value) {
            chrome.debugger.detach({ tabId });
            sendResponse({ success: false, error: chrome.runtime.lastError ? chrome.runtime.lastError.message : 'Evaluation failed' });
            return;
          }
          
          try {
            const data = JSON.parse(result.result.value);
            if (!data.found) {
              chrome.debugger.detach({ tabId });
              sendResponse({ success: false, error: 'Element not found by script' });
              return;
            }
            
            const { x, y, text } = data;
            
            // Now click at the calculated coordinates
            chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
              type: 'mouseMoved', x, y, button: 'none'
            }, () => {
              setTimeout(() => {
                chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
                  type: 'mousePressed', x, y, button: 'left', clickCount: 1, buttons: 1, pointerType: 'mouse'
                }, () => {
                  setTimeout(() => {
                    chrome.debugger.sendCommand({ tabId }, 'Input.dispatchMouseEvent', {
                      type: 'mouseReleased', x, y, button: 'left', clickCount: 1, buttons: 0, pointerType: 'mouse'
                    }, () => {
                      chrome.debugger.detach({ tabId }, () => {
                        sendResponse({ success: true, x, y, text });
                      });
                    });
                  }, 50);
                });
              }, 30);
            });
          } catch (e) {
            chrome.debugger.detach({ tabId });
            sendResponse({ success: false, error: 'JSON parse error: ' + e.message });
          }
        });
      }, 300); // 300ms delay to allow infobar layout shift
    };
    
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        const errMsg = chrome.runtime.lastError.message || '';
        if (errMsg.includes('Already attached') || errMsg.includes('already being inspected')) {
          doFindAndClick();
        } else {
          sendResponse({ success: false, error: errMsg });
        }
        return;
      }
      doFindAndClick();
    });
    return true;
  }

  // Press Enter using CDP
  if (message.type === 'cdp-press-enter') {
    const tabId = sender.tab.id;
    
    const doEnter = () => {
      chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent', {
        type: 'keyDown', windowsVirtualKeyCode: 13, key: 'Enter', code: 'Enter'
      }, () => {
        chrome.debugger.sendCommand({ tabId }, 'Input.dispatchKeyEvent', {
          type: 'keyUp', windowsVirtualKeyCode: 13, key: 'Enter', code: 'Enter'
        }, () => {
          chrome.debugger.detach({ tabId }, () => {
            sendResponse({ success: true });
          });
        });
      });
    };
    
    chrome.debugger.attach({ tabId }, '1.3', () => {
      if (chrome.runtime.lastError) {
        const errMsg = chrome.runtime.lastError.message || '';
        if (errMsg.includes('Already attached') || errMsg.includes('already being inspected')) {
          doEnter();
        } else {
          sendResponse({ success: false, error: errMsg });
        }
        return;
      }
      doEnter();
    });
    return true;
  }
});

// Alarm handling moved to cookie management section below


// Notify content script that timer completed
function notifyTab(tabId, callback) {
  chrome.tabs.sendMessage(tabId, {
    type: 'backgroundTimerComplete',
    callback: callback
  }).catch(err => {
    console.log('[Veo3] Could not notify tab:', err.message);
  });
}

// Download a single video - always download directly without dialog
async function handleVideoDownload(url, filename) {
  return new Promise((resolve, reject) => {
    chrome.downloads.download({
      url: url,
      filename: filename,
      saveAs: false // Never open dialog - download directly to Downloads folder
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve({ success: true, downloadId });
      }
    });
  });
}

// Download multiple videos in sequence - all go directly to folder
async function handleBatchDownload(videos) {
  const results = [];
  
  for (let i = 0; i < videos.length; i++) {
    const video = videos[i];
    try {
      const result = await handleVideoDownload(video.url, video.filename);
      results.push({ index: i, success: true, ...result });
      
      // Small delay between downloads
      await new Promise(resolve => setTimeout(resolve, 200));
    } catch (error) {
      results.push({ index: i, success: false, error: error.message });
    }
  }
  
  return { 
    success: true, 
    count: results.filter(r => r.success).length,
    results 
  };
}

console.log('[Veo3 Automation] Background service worker loaded.');

// ==================== BULLETPROOF COOKIE MANAGEMENT ====================
// Handles: __Host- prefix, hostOnly, sameSite, auto-restore, periodic check

const SESSION_COOKIE = '__Secure-next-auth.session-token';
const SESSION_URL = 'https://labs.google';
const CHECK_INTERVAL_MIN = 30; // Check every 30 minutes
const ALARM_NAME = 'veo3_session_check';

/**
 * Set a single cookie with full error handling.
 * Handles __Host- prefix (MUST NOT have domain), hostOnly, etc.
 */
async function setSingleCookie(cookie) {
  const cleanDomain = (cookie.domain || '').replace(/^\./, '');
  const url = `https://${cleanDomain}${cookie.path || '/'}`;
  
  const details = {
    url: url,
    name: cookie.name,
    value: cookie.value,
    path: cookie.path || '/',
    httpOnly: cookie.httpOnly || false,
  };
  
  // sameSite mapping (Chrome API uses different values than export format)
  const sm = (cookie.sameSite || '').toLowerCase();
  if (sm === 'lax') details.sameSite = 'lax';
  else if (sm === 'strict') details.sameSite = 'strict';
  else details.sameSite = 'no_restriction';
  
  // If sameSite is no_restriction, secure MUST be true
  if (details.sameSite === 'no_restriction') {
    details.secure = true;
  } else {
    details.secure = cookie.secure || false;
  }
  
  // __Host- prefix cookies: MUST NOT have domain, MUST be secure, path must be /
  const isHostPrefix = cookie.name.startsWith('__Host-');
  
  // __Secure- prefix cookies: MUST be secure
  const isSecurePrefix = cookie.name.startsWith('__Secure-');
  
  if (isHostPrefix) {
    // __Host- cookies cannot have domain at all
    details.secure = true;
    details.path = '/';
    // Do NOT set details.domain
  } else if (isSecurePrefix) {
    details.secure = true;
    // hostOnly = true means no domain attribute
    if (!cookie.hostOnly && cookie.domain) {
      details.domain = cookie.domain;
    }
  } else {
    // Regular cookie
    // hostOnly = true means the cookie is only for exact domain (no domain attribute)
    // hostOnly = false means subdomain matching (set domain attribute)
    if (!cookie.hostOnly && cookie.domain) {
      details.domain = cookie.domain;
    }
  }
  
  // Set expiration for persistent cookies
  if (cookie.expirationDate && !cookie.session) {
    details.expirationDate = cookie.expirationDate;
  }
  
  // Try to set the cookie
  try {
    await chrome.cookies.set(details);
    return { ok: true, name: cookie.name };
  } catch (e) {
    // Retry without domain if it failed (common with hostOnly confusion)
    if (details.domain) {
      try {
        delete details.domain;
        await chrome.cookies.set(details);
        return { ok: true, name: cookie.name, retry: true };
      } catch (e2) {
        return { ok: false, name: cookie.name, error: e2.message };
      }
    }
    return { ok: false, name: cookie.name, error: e.message };
  }
}

/**
 * Import ALL cookies from a JSON array.
 * Saves to storage for auto-restore.
 */
async function importCookies(cookiesArray) {
  const results = { success: 0, failed: 0, errors: [], details: [] };
  
  for (const cookie of cookiesArray) {
    const r = await setSingleCookie(cookie);
    if (r.ok) {
      results.success++;
    } else {
      results.failed++;
      results.errors.push(`${r.name}: ${r.error}`);
    }
    results.details.push(r);
  }
  
  // Save cookies to storage for auto-restore
  await chrome.storage.local.set({ 
    savedCookies: cookiesArray,
    cookiesSavedAt: Date.now(),
    cookiesAccount: (() => {
      const emailCookie = cookiesArray.find(c => c.name === 'EMAIL');
      if (emailCookie) {
        try { return decodeURIComponent(emailCookie.value).replace(/"/g, ''); }
        catch { return emailCookie.value; }
      }
      return 'unknown';
    })()
  });
  
  console.log(`[Veo3] Import: ${results.success} OK, ${results.failed} failed`);
  if (results.errors.length > 0) {
    console.log(`[Veo3] Errors: ${results.errors.join('; ')}`);
  }
  
  // Setup periodic check alarm
  setupSessionAlarm();
  
  return results;
}

/**
 * Check if labs.google session is active.
 */
async function checkSession() {
  try {
    const sessionCookie = await chrome.cookies.get({
      url: SESSION_URL,
      name: SESSION_COOKIE
    });
    
    if (!sessionCookie || !sessionCookie.value) {
      return { active: false, reason: 'No session token' };
    }
    
    // Check expiration
    if (sessionCookie.expirationDate && sessionCookie.expirationDate < Date.now() / 1000) {
      return { active: false, reason: 'Token expired' };
    }
    
    // Get email
    let email = 'unknown';
    try {
      const emailCookie = await chrome.cookies.get({ url: SESSION_URL, name: 'EMAIL' });
      if (emailCookie && emailCookie.value) {
        email = decodeURIComponent(emailCookie.value).replace(/"/g, '');
      }
    } catch {}
    
    // Calculate time remaining
    let expiresAt = 'session';
    let hoursLeft = null;
    if (sessionCookie.expirationDate) {
      const expDate = new Date(sessionCookie.expirationDate * 1000);
      expiresAt = expDate.toLocaleString();
      hoursLeft = Math.round((sessionCookie.expirationDate - Date.now() / 1000) / 3600);
    }
    
    return { active: true, email, expiresAt, hoursLeft };
  } catch (e) {
    return { active: false, reason: e.message };
  }
}

/**
 * Auto-restore cookies from storage.
 */
async function autoRestoreCookies() {
  try {
    const data = await chrome.storage.local.get(['savedCookies', 'cookiesSavedAt']);
    if (!data.savedCookies || !data.savedCookies.length) {
      console.log('[Veo3] No saved cookies to restore');
      return;
    }
    
    // Check if session is already active
    const session = await checkSession();
    if (session.active) {
      console.log(`[Veo3] Session OK: ${session.email} (${session.hoursLeft}h left)`);
      return;
    }
    
    // Session expired/missing — restore
    console.log('[Veo3] Session lost, restoring...');
    const result = await importCookies(data.savedCookies);
    
    // Verify
    const newSession = await checkSession();
    if (newSession.active) {
      console.log(`[Veo3] Session RESTORED: ${newSession.email}`);
    } else {
      console.log(`[Veo3] Restore FAILED — cookies may be too old. Re-import needed.`);
    }
  } catch (e) {
    console.log('[Veo3] Auto-restore error:', e.message);
  }
}

/**
 * Setup periodic session check alarm.
 */
function setupSessionAlarm() {
  chrome.alarms.create(ALARM_NAME, {
    delayInMinutes: CHECK_INTERVAL_MIN,
    periodInMinutes: CHECK_INTERVAL_MIN
  });
  console.log(`[Veo3] Session check alarm: every ${CHECK_INTERVAL_MIN}min`);
}

// ─── LISTENERS ─────────────────────────────────────────────

// Alarm fires → auto-restore if needed
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    console.log('[Veo3] Periodic session check...');
    autoRestoreCookies();
  }
  // Handle other alarms (timers for automation)
  const pending = pendingAlarms.get(alarm.name);
  if (pending) {
    notifyTab(pending.tabId, pending.callback);
    pendingAlarms.delete(alarm.name);
  }
});

// Browser startup → restore
chrome.runtime.onStartup.addListener(() => {
  console.log('[Veo3] Browser startup — checking session...');
  autoRestoreCookies();
  setupSessionAlarm();
});

// Extension install/update → restore
chrome.runtime.onInstalled.addListener(() => {
  console.log('[Veo3] Extension installed/updated — checking session...');
  autoRestoreCookies();
  setupSessionAlarm();
});

// Cookie removed for labs.google → instant re-restore
chrome.cookies.onChanged.addListener((changeInfo) => {
  if (changeInfo.removed && 
      changeInfo.cookie.name === SESSION_COOKIE &&
      changeInfo.cookie.domain.includes('labs.google')) {
    console.log('[Veo3] Session cookie REMOVED — restoring immediately...');
    // Small delay to avoid racing with browser's own cookie management
    setTimeout(() => autoRestoreCookies(), 2000);
  }
});

// Run on service worker startup
autoRestoreCookies();
setupSessionAlarm();

// ─── MESSAGE HANDLERS ──────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'importCookies') {
    importCookies(message.cookies)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ success: 0, failed: 1, errors: [error.message] }));
    return true;
  }
  
  if (message.type === 'checkSession') {
    checkSession()
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ active: false, reason: error.message }));
    return true;
  }
  
  if (message.type === 'restoreCookies') {
    autoRestoreCookies()
      .then(() => checkSession())
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ active: false, reason: error.message }));
    return true;
  }
  
  if (message.type === 'clearSavedCookies') {
    chrome.storage.local.remove(['savedCookies', 'cookiesSavedAt', 'cookiesAccount']);
    chrome.alarms.clear(ALARM_NAME);
    sendResponse({ success: true });
    return true;
  }
});

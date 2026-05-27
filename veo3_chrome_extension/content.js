// Content Script for Veo 3 Automation
// This script runs on the Veo 3 page and automates the video generation process

(function() {
  'use strict';

  // Check if we're on the Veo 3/Flow page - ONLY run on the video generation page
  // URL format: labs.google/fx/{lang}/tools/flow/project/{project-id}
  const VEO3_URL_PATTERNS = [
    /labs\.google\/fx\/[a-z]{2}\/tools\/flow\/project\//i,  // labs.google/fx/pt/tools/flow/project/...
    /labs\.google\/fx\/tools\/flow/i,                        // labs.google/fx/tools/flow/...
    /labs\.google\.com\/fx\/[a-z]{2}\/tools\/flow/i,         // with .com
    /labs\.google\.com\/fx\/tools\/video/i,                  // video-fx variant
    /labs\.google\/fx\/.*\/tools\//i                         // any language, tools path
  ];
  
  function isVeo3Page() {
    const currentUrl = window.location.href;
    const isMatch = VEO3_URL_PATTERNS.some(pattern => pattern.test(currentUrl));
    console.log('[Veo3 Automation] URL check:', currentUrl, 'Match:', isMatch);
    return isMatch;
  }
  
  // Exit early if NOT on Veo 3 page - do not run on other Google pages
  if (!isVeo3Page()) {
    console.log('[Veo3 Automation] Not on Veo 3/Flow page, skipping.');
    return;
  }

  // Guard against multiple script injections
  if (window.__veo3AutomationLoaded) {
    console.log('[Veo3 Automation] Script already loaded, skipping initialization.');
    return;
  }
  window.__veo3AutomationLoaded = true;
  
  console.log('[Veo3 Automation] Initialized on Veo 3 page:', window.location.href);

  let isRunning = false;
  let shouldStop = false;
  let isPaused = false;
  let currentSettings = null;
  let generatedVideos = []; // Track videos in order of prompts
  let currentPromptIndex = 0;
  let totalPrompts = 0;
  
  // Save automation state to storage (persists across popup close/reopen)
  function saveAutomationState() {
    const state = {
      isRunning,
      isPaused,
      currentPromptIndex,
      totalPrompts,
      currentSettings,
      timestamp: Date.now()
    };
    chrome.storage.local.set({ automationState: state });
  }
  
  // Clear automation state when complete
  function clearAutomationState() {
    chrome.storage.local.remove('automationState');
  }
  
  // Broadcast current state to popup
  function broadcastState() {
    chrome.runtime.sendMessage({
      type: 'automationState',
      isRunning,
      isPaused,
      currentPromptIndex,
      totalPrompts,
      currentSettings
    }).catch(() => {}); // Ignore if popup is closed
  }
  
  // Background timer support
  const pendingBackgroundCallbacks = new Map();
  let keepAliveInterval = null;
  
  // Listen for background timer completions
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'backgroundTimerComplete') {
      const callback = pendingBackgroundCallbacks.get(message.callback);
      if (callback) {
        callback();
        pendingBackgroundCallbacks.delete(message.callback);
      }
      sendResponse({ received: true });
    }
    return true;
  });
  
  // Start keep-alive to prevent service worker from sleeping during automation
  function startKeepAlive() {
    if (keepAliveInterval) return;
    keepAliveInterval = setInterval(() => {
      if (isRunning && !shouldStop) {
        chrome.runtime.sendMessage({ type: 'keepAlive' }).catch(() => {});
      }
    }, 20000); // Ping every 20 seconds
  }
  
  function stopKeepAlive() {
    if (keepAliveInterval) {
      clearInterval(keepAliveInterval);
      keepAliveInterval = null;
    }
  }
  
  // Background-safe delay for long waits (> 5 seconds)
  function backgroundDelay(ms) {
    return new Promise((resolve) => {
      if (ms < 5000) {
        // For short delays, use regular setTimeout
        setTimeout(resolve, ms);
      } else {
        // For longer delays, use background timer
        const callbackId = `cb_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        pendingBackgroundCallbacks.set(callbackId, resolve);
        
        chrome.runtime.sendMessage({
          type: 'setBackgroundTimer',
          delayMs: ms,
          callback: callbackId
        }).catch(() => {
          // Fallback to regular timeout if background fails
          pendingBackgroundCallbacks.delete(callbackId);
          setTimeout(resolve, ms);
        });
      }
    });
  }
  
  // Collected videos for manual detection
  let collectedVideosOrder = [];
  let isScanning = false;
  
  function getSortedCollectedVideos() {
    return collectedVideosOrder;
  }
  
  // Collect videos visible in current viewport
  function collectVisibleVideos(seenUrls, videoDataList, scrollTop) {
    document.querySelectorAll('video').forEach(video => {
      const url = video.src || video.querySelector('source')?.src;
      if (url && !seenUrls.has(url)) {
        const rect = video.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          seenUrls.add(url);
          videoDataList.push({
            url: url,
            y: rect.top + scrollTop,
            x: rect.left,
            isBlob: url.startsWith('blob:')
          });
        }
      }
    });
    
    document.querySelectorAll('video source').forEach(source => {
      if (source.src && !seenUrls.has(source.src)) {
        const video = source.closest('video');
        const rect = video ? video.getBoundingClientRect() : source.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          seenUrls.add(source.src);
          videoDataList.push({
            url: source.src,
            y: rect.top + scrollTop,
            x: rect.left,
            isBlob: source.src.startsWith('blob:')
          });
        }
      }
    });
  }
  
  // Sort videos: bottom-right first (oldest = first prompt = 001) → top-left last
  function sortByGridPosition(list) {
    list.sort((a, b) => {
      if (Math.abs(a.y - b.y) < 80) {
        return b.x - a.x;
      }
      return b.y - a.y;
    });
  }
  
  // Find the actual scrollable container on the page
  function findScrollContainer() {
    // Try common scrollable containers
    const candidates = [
      document.querySelector('[role="main"]'),
      document.querySelector('main'),
      ...document.querySelectorAll('div')
    ];
    
    for (const el of candidates) {
      if (!el) continue;
      if (el.scrollHeight > el.clientHeight + 50 && 
          el.clientHeight > 200 &&
          (getComputedStyle(el).overflowY === 'auto' || getComputedStyle(el).overflowY === 'scroll')) {
        return el;
      }
    }
    
    // Fallback to document
    if (document.documentElement.scrollHeight > window.innerHeight + 50) {
      return document.documentElement;
    }
    return document.documentElement;
  }

  // Extract text around a video to match with prompts
  function extractPromptTextFromVideo(videoElement) {
    let bestText = '';
    let current = videoElement.parentElement;
    for (let k = 0; k < 8; k++) {
      if (!current) break;
      // Stop traversing if we hit a container with multiple videos (e.g. the main grid)
      if (current.querySelectorAll('video').length > 1) {
        break;
      }
      const text = (current.textContent || '').trim();
      if (text.length > bestText.length) {
        bestText = text;
      }
      current = current.parentElement;
    }
    return bestText;
  }

  // Collect video URLs currently in the DOM into a Set
  function collectCurrentVideos(seenUrls, videoDataList, currentScrollTop = 0) {
    let newCount = 0;
    document.querySelectorAll('video').forEach(video => {
      const url = video.src || video.querySelector('source')?.src;
      if (url && !seenUrls.has(url)) {
        seenUrls.add(url);
        const rect = video.getBoundingClientRect();
        videoDataList.push({
          url: url,
          order: videoDataList.length,
          isBlob: url.startsWith('blob:'),
          top: rect.top + currentScrollTop,
          left: rect.left + window.scrollX,
          promptText: extractPromptTextFromVideo(video)
        });
        newCount++;
      }
    });
    document.querySelectorAll('video source').forEach(source => {
      if (source.src && !seenUrls.has(source.src)) {
        seenUrls.add(source.src);
        const videoParent = source.closest('video');
        const rect = videoParent ? videoParent.getBoundingClientRect() : { top: 0, left: 0 };
        videoDataList.push({
          url: source.src,
          order: videoDataList.length,
          isBlob: source.src.startsWith('blob:'),
          top: rect.top + currentScrollTop,
          left: rect.left + window.scrollX,
          promptText: videoParent ? extractPromptTextFromVideo(videoParent) : ''
        });
        newCount++;
      }
    });
    return newCount;
  }

  // Detect all videos by scrolling through the entire page
  // Veo 3 uses virtualized rendering — only visible videos are in the DOM
  // We must scroll to force all videos to load
  async function detectAllVideosOnPage() {
    const seenUrls = new Set();
    const videoDataList = [];
    
    const scrollContainer = findScrollContainer();
    const originalScroll = scrollContainer.scrollTop;
    
    // First collect what's already visible
    collectCurrentVideos(seenUrls, videoDataList, scrollContainer.scrollTop);
    log(`Fase 1: ${videoDataList.length} videos visiveis no DOM`, 'info');
    
    // Scroll to top first
    scrollContainer.scrollTop = 0;
    await delay(400);
    collectCurrentVideos(seenUrls, videoDataList, scrollContainer.scrollTop);
    
    // Scroll down through the entire page in steps
    const viewportHeight = scrollContainer.clientHeight || window.innerHeight;
    const scrollStep = Math.floor(viewportHeight * 0.6);
    let currentScroll = 0;
    let noNewVideoRounds = 0;
    let dynamicScrollHeight = scrollContainer.scrollHeight;
    
    log(`Scrollando pagina para carregar todos os videos...`, 'info');
    
    while (currentScroll < dynamicScrollHeight + viewportHeight) {
      currentScroll += scrollStep;
      scrollContainer.scrollTop = currentScroll;
      await delay(350);
      
      const newFound = collectCurrentVideos(seenUrls, videoDataList, scrollContainer.scrollTop);
      
      if (newFound > 0) {
        noNewVideoRounds = 0;
        log(`${videoDataList.length} videos encontrados...`, 'info');
        // Send live update to popup
        chrome.runtime.sendMessage({
          type: 'videoScanUpdate',
          count: videoDataList.length
        }).catch(() => {});
      } else {
        noNewVideoRounds++;
      }
      
      // Check if scrollHeight changed (dynamic loading)
      const newScrollHeight = scrollContainer.scrollHeight;
      if (newScrollHeight > dynamicScrollHeight + 50) {
        // Page grew, keep scrolling
        noNewVideoRounds = 0;
        dynamicScrollHeight = newScrollHeight;
      }
      
      // If we scrolled past the end and found nothing new for 3 rounds, stop
      if (currentScroll >= dynamicScrollHeight && noNewVideoRounds >= 3) {
        break;
      }
    }
    
    // Final scroll to very bottom to catch any remaining
    scrollContainer.scrollTop = scrollContainer.scrollHeight;
    await delay(500);
    collectCurrentVideos(seenUrls, videoDataList, scrollContainer.scrollTop);
    
    // Restore original scroll position
    scrollContainer.scrollTop = originalScroll;
    
    // We scroll top-to-bottom, and DOM naturally reads left-to-right.
    // So the collected list is Top-Down, Left-to-Right (Prompt 3, Prompt 2, Prompt 1).
    // The user wants Prompt 1 (Bottom Left) to be 001.
    // Reversing the array perfectly maps them to Prompt 1, Prompt 2, Prompt 3.
    videoDataList.reverse();
    videoDataList.forEach((v, i) => { v.order = i; });
    
    log(`${videoDataList.length} videos unicos encontrados no total`, 'success');
    return videoDataList;
  }
  
  // Start detection
  async function startContinuousVideoScan() {
    if (isScanning) return;
    isScanning = true;
    collectedVideosOrder = [];
    
    log('Detectando todos os videos na pagina...', 'info');
    
    const videos = await detectAllVideosOnPage();
    collectedVideosOrder = videos;
    
    videos.forEach((v, i) => {
      const orderNum = String(i + 1).padStart(3, '0');
      log(`Video ${orderNum} detectado!`, 'success');
    });
    
    log(`Total: ${videos.length} videos detectados.`, 'success');
    log('Pronto para baixar na ordem dos prompts (001 = primeiro prompt).', 'info');
    
    chrome.runtime.sendMessage({
      type: 'videoScanUpdate',
      count: videos.length
    });
    
    isScanning = false;
  }
  
  function stopContinuousVideoScan() {
    isScanning = false;
    const totalVideos = collectedVideosOrder.length;
    log(`${totalVideos} videos prontos para download.`, 'success');
  }

  // ===== IMAGE SCANNING AND DOWNLOAD =====
  let collectedImagesOrder = [];
  let isImageScanning = false;

  // Collect images visible in current viewport
  function collectVisibleImages(seenUrls, imageDataList, scrollTop) {
    document.querySelectorAll('img').forEach(img => {
      const url = img.src || img.currentSrc;
      if (!url || seenUrls.has(url)) return;
      if (url.startsWith('data:') && url.length < 200) return;
      if (url.includes('icon') || url.includes('logo') || url.includes('avatar') || url.includes('favicon')) return;

      const rect = img.getBoundingClientRect();
      if (rect.width < 80 || rect.height < 80) return;

      seenUrls.add(url);
      imageDataList.push({
        url: url,
        y: rect.top + scrollTop,
        x: rect.left,
        width: img.naturalWidth || rect.width,
        height: img.naturalHeight || rect.height,
        isCanvas: false
      });
    });

    document.querySelectorAll('canvas').forEach(canvas => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width < 80 || rect.height < 80) return;
      const key = `canvas_${rect.top + scrollTop}_${rect.left}`;
      if (seenUrls.has(key)) return;

      try {
        const dataUrl = canvas.toDataURL('image/png');
        if (dataUrl && dataUrl.length > 200) {
          seenUrls.add(key);
          imageDataList.push({
            url: dataUrl,
            y: rect.top + scrollTop,
            x: rect.left,
            width: canvas.width,
            height: canvas.height,
            isCanvas: true
          });
        }
      } catch (e) {}
    });
  }

  // Collect current images from DOM into list
  function collectCurrentImages(seenUrls, imageDataList) {
    let newCount = 0;
    document.querySelectorAll('img').forEach(img => {
      const url = img.src || img.currentSrc;
      if (!url || seenUrls.has(url)) return;
      if (url.startsWith('data:') && url.length < 200) return;
      if (url.includes('icon') || url.includes('logo') || url.includes('avatar') || url.includes('favicon')) return;

      const rect = img.getBoundingClientRect();
      if (img.naturalWidth < 80 && rect.width < 80) return;
      if (img.naturalHeight < 80 && rect.height < 80) return;

      seenUrls.add(url);
      imageDataList.push({
        url: url,
        order: imageDataList.length,
        width: img.naturalWidth || rect.width,
        height: img.naturalHeight || rect.height,
        isCanvas: false
      });
      newCount++;
    });
    return newCount;
  }

  // Detect all images by scrolling through the entire page
  async function detectAllImagesOnPage() {
    const seenUrls = new Set();
    const imageDataList = [];

    const scrollContainer = findScrollContainer();
    const originalScroll = scrollContainer.scrollTop;

    collectCurrentImages(seenUrls, imageDataList);
    log(`Fase 1: ${imageDataList.length} imagens visiveis no DOM`, 'info');

    scrollContainer.scrollTop = 0;
    await delay(400);
    collectCurrentImages(seenUrls, imageDataList);

    const scrollHeight = scrollContainer.scrollHeight;
    const viewportHeight = scrollContainer.clientHeight || window.innerHeight;
    const scrollStep = Math.floor(viewportHeight * 0.6);
    let currentScroll = 0;
    let noNewRounds = 0;

    log(`Scrollando pagina para carregar todas as imagens...`, 'info');

    while (currentScroll < scrollHeight + viewportHeight) {
      currentScroll += scrollStep;
      scrollContainer.scrollTop = currentScroll;
      await delay(350);

      const newFound = collectCurrentImages(seenUrls, imageDataList);

      if (newFound > 0) {
        noNewRounds = 0;
        log(`${imageDataList.length} imagens encontradas...`, 'info');
        chrome.runtime.sendMessage({
          type: 'imageScanUpdate',
          count: imageDataList.length
        }).catch(() => {});
      } else {
        noNewRounds++;
      }

      if (currentScroll >= scrollContainer.scrollHeight && noNewRounds >= 3) {
        break;
      }
    }

    scrollContainer.scrollTop = scrollContainer.scrollHeight;
    await delay(500);
    collectCurrentImages(seenUrls, imageDataList);

    scrollContainer.scrollTop = originalScroll;

    // Reverse: top = newest, bottom = oldest; we want oldest = 001
    imageDataList.reverse();
    imageDataList.forEach((v, i) => { v.order = i; });

    log(`${imageDataList.length} imagens unicas encontradas no total`, 'success');
    return imageDataList;
  }

  async function startContinuousImageScan() {
    if (isImageScanning) return;
    isImageScanning = true;
    collectedImagesOrder = [];

    log('Detectando todas as imagens na pagina...', 'info');

    const images = await detectAllImagesOnPage();
    collectedImagesOrder = images;

    images.forEach((v, i) => {
      const orderNum = String(i + 1).padStart(3, '0');
      log(`Imagem ${orderNum} detectada! (${Math.round(v.width)}x${Math.round(v.height)})`, 'success');
    });

    log(`Total: ${images.length} imagens detectadas.`, 'success');
    log('Pronto para baixar na ordem (001 = primeira imagem).', 'info');

    chrome.runtime.sendMessage({
      type: 'imageScanUpdate',
      count: images.length
    }).catch(() => {});

    isImageScanning = false;
  }

  function stopContinuousImageScan() {
    isImageScanning = false;
    const totalImages = collectedImagesOrder.length;
    log(`${totalImages} imagens prontas para download.`, 'success');
  }

  async function downloadAllImages(folderName = 'veo3_imagens') {
    const safeFolderName = folderName.replace(/[<>:"/\\|?*]/g, '_').trim() || 'veo3_imagens';

    let imagesToDownload = [];

    if (collectedImagesOrder.length > 0) {
      imagesToDownload = collectedImagesOrder.map((img, i) => ({
        url: img.url,
        index: i,
        isCanvas: img.isCanvas || false
      }));
      log(`Preparando ${imagesToDownload.length} imagens`, 'success');
    } else {
      const detected = await detectAllImagesOnPage();
      if (detected.length > 0) {
        imagesToDownload = detected.map((img, i) => ({
          url: img.url,
          index: i,
          isCanvas: img.isCanvas || false
        }));
        log(`Detectadas ${imagesToDownload.length} imagens na pagina`, 'info');
      } else {
        return { success: false, error: 'Nenhuma imagem encontrada na pagina.' };
      }
    }

    if (imagesToDownload.length === 0) {
      return { success: false, error: 'Nenhuma imagem encontrada' };
    }

    const totalImages = imagesToDownload.length;
    log(`Baixando exatamente ${totalImages} imagens para pasta: ${safeFolderName}`, 'info');

    let downloadedCount = 0;
    let failedCount = 0;

    // Convert image URL to data URL for background download
    async function imageToDataUrl(url) {
      const response = await fetch(url);
      const blob = await response.blob();
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve({ dataUrl: reader.result, type: blob.type });
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    function downloadViaBackground(url, filename) {
      return new Promise((resolve) => {
        chrome.runtime.sendMessage({
          type: 'downloadVideo',
          url: url,
          filename: filename
        }, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ success: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(response || { success: false, error: 'no response' });
          }
        });
      });
    }

    for (let i = 0; i < imagesToDownload.length; i++) {
      const img = imagesToDownload[i];
      const paddedIndex = String(img.index + 1).padStart(3, '0');

      try {
        let downloadUrl = img.url;
        let ext = 'jpg';

        if (img.url.startsWith('data:') || img.isCanvas) {
          downloadUrl = img.url;
          ext = 'png';
        } else {
          try {
            const result = await imageToDataUrl(img.url);
            downloadUrl = result.dataUrl;
            ext = result.type.includes('png') ? 'png' : result.type.includes('webp') ? 'webp' : 'jpg';
          } catch (fetchErr) {
            downloadUrl = img.url;
          }
        }

        const filename = `${safeFolderName}/${paddedIndex}.${ext}`;
        const dlResult = await downloadViaBackground(downloadUrl, filename);

        if (dlResult && dlResult.success) {
          downloadedCount++;
          log(`Imagem ${paddedIndex} baixada!`, 'success');
        } else {
          failedCount++;
          log(`Falha ao baixar imagem ${paddedIndex}: ${dlResult?.error || 'erro'}`, 'error');
        }

        if (i < imagesToDownload.length - 1) {
          await delay(800);
        }
      } catch (err) {
        failedCount++;
        log(`Erro ao baixar imagem ${paddedIndex}: ${err.message}`, 'error');
      }
    }

    log(`Download concluido: ${downloadedCount}/${totalImages} imagens` + (failedCount > 0 ? `, ${failedCount} falharam` : ''), downloadedCount === totalImages ? 'success' : 'warning');
    return { success: true, count: downloadedCount, total: totalImages };
  }

  // Helper: Wait for element to appear
  function waitForElement(selector, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const startTime = Date.now();
      
      const check = () => {
        const element = document.querySelector(selector);
        if (element) {
          resolve(element);
          return;
        }
        
        if (Date.now() - startTime > timeout) {
          reject(new Error(`Element not found: ${selector}`));
          return;
        }
        
        requestAnimationFrame(check);
      };
      
      check();
    });
  }

  // Helper: Wait for multiple elements
  function waitForElements(selector, timeout = 10000) {
    return new Promise((resolve, reject) => {
      const startTime = Date.now();
      
      const check = () => {
        const elements = document.querySelectorAll(selector);
        if (elements.length > 0) {
          resolve(elements);
          return;
        }
        
        if (Date.now() - startTime > timeout) {
          reject(new Error(`Elements not found: ${selector}`));
          return;
        }
        
        requestAnimationFrame(check);
      };
      
      check();
    });
  }

  // Helper: Delay
  function delay(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  // Helper: Click element with retry
  async function clickElement(selector, retries = 3) {
    for (let i = 0; i < retries; i++) {
      try {
        const element = await waitForElement(selector, 5000);
        element.click();
        return true;
      } catch (error) {
        if (i === retries - 1) throw error;
        await delay(1000);
      }
    }
    return false;
  }

  // Helper: Find element by text content
  function findElementByText(selector, text) {
    const elements = document.querySelectorAll(selector);
    for (const el of elements) {
      if (el.textContent.toLowerCase().includes(text.toLowerCase())) {
        return el;
      }
    }
    return null;
  }

  // Helper: Find clickable element by text
  function findClickableByText(text) {
    // Look for buttons, links, and divs that might be clickable
    const selectors = ['button', 'a', '[role="button"]', '[role="tab"]', 'div[tabindex]', 'span'];
    
    for (const selector of selectors) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        if (el.textContent.trim().toLowerCase().includes(text.toLowerCase())) {
          return el;
        }
      }
    }
    return null;
  }

  // Send log to popup
  function log(text, level = 'info') {
    chrome.runtime.sendMessage({ type: 'log', text, level });
    console.log(`[Veo3 Automation] ${text}`);
  }

  // Send progress to popup
  function updateProgress(current, total, status, timeRemaining = '') {
    chrome.runtime.sendMessage({ 
      type: 'progress', 
      current, 
      total, 
      status,
      timeRemaining 
    });
  }

  // Click on Images tab
  // Google Flow 2026: Sidebar has "Todas as mídias", "Personagens", "Cenas", "Ferramentas"
  // Images/Videos are NOT in sidebar — they are TABS in the popover near the prompt bar
  function findSidebarButtons() {
    const sidebar = [];
    const allButtons = document.querySelectorAll('button, [role="button"], a[role="tab"], [role="tab"]');
    
    for (const btn of allButtons) {
      const rect = btn.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      // Sidebar buttons are on the left side (x < 80px) or within sidebar area (x < 230px for expanded)
      if (rect.left < 230 && rect.width < 230) {
        sidebar.push(btn);
      }
    }
    
    return sidebar;
  }

  // Click on Images tab — in Google Flow 2026, this is the "Imagem" tab in the popover
  // The popover appears near the prompt bar and has tabs: Imagem | Vídeo
  async function clickImagesTab() {
    log('Clicando na aba Imagens (popover)...');
    
    // Strategy 1: Find "Imagem" or "Image" tab button in the popover near the bottom
    const allClickables = document.querySelectorAll('button, [role="button"], [role="tab"], a, div[tabindex], span[tabindex]');
    
    for (const el of allClickables) {
      const text = (el.textContent || '').trim();
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      
      // Look for "Imagem" or "Image" tab in popover area (lower half of screen)
      if ((text === 'Imagem' || text === 'Image' || text === 'Imagens' || text === 'Images') && 
          rect.top > window.innerHeight * 0.4) {
        log(`Aba imagens encontrada: "${text}"`, 'info');
        el.click();
        await delay(1500);
        return true;
      }
    }

    // Strategy 2: Find by icon name (photo, image)
    for (const el of allClickables) {
      const elText = (el.textContent || '').trim().toLowerCase();
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      if (rect.top < window.innerHeight * 0.4) continue;
      
      if (elText.includes('photo_library') || elText.includes('imagem') || elText === 'image') {
        el.click();
        await delay(1500);
        return true;
      }
    }

    // Strategy 3: Look in sidebar for "Todas as mídias" (shows all media)
    for (const el of allClickables) {
      const text = (el.textContent || '').trim().toLowerCase();
      if (text.includes('todas as m') || text.includes('all media')) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          el.click();
          await delay(1500);
          return true;
        }
      }
    }

    throw new Error('Não foi possível encontrar a aba Imagens');
  }

  // Click on Videos tab — in Google Flow 2026, this is the "Vídeo"/"Video" tab in the popover
  async function clickVideosTab() {
    log('Clicando na aba Vídeos (popover)...');
    
    const allClickables = document.querySelectorAll('button, [role="button"], [role="tab"], a, div[tabindex], span[tabindex]');
    
    // Strategy 1: Find "Vídeo" or "Video" tab in the popover near the bottom
    for (const el of allClickables) {
      const text = (el.textContent || '').trim();
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      
      if ((text === 'Video' || text === 'Vídeo' || text === 'Videos' || text === 'Vídeos') && 
          rect.top > window.innerHeight * 0.4) {
        log(`Aba videos encontrada: "${text}"`, 'info');
        el.click();
        await delay(1500);
        return true;
      }
    }

    // Strategy 2: Look for elements with video icon text
    for (const el of allClickables) {
      const elText = (el.textContent || '').trim().toLowerCase();
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      if (rect.top < window.innerHeight * 0.4) continue;
      
      if (elText.includes('videocam') || elText.includes('play_circle') || 
          (elText.includes('video') && !elText.includes('video_') && elText.length < 20)) {
        el.click();
        await delay(1500);
        return true;
      }
    }

    // Strategy 3: Look for sidebar "Todas as mídias" as fallback
    for (const el of allClickables) {
      const text = (el.textContent || '').trim().toLowerCase();
      if (text.includes('todas as m') || text.includes('all media')) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          el.click();
          await delay(1500);
          return true;
        }
      }
    }

    throw new Error('Não foi possível encontrar a aba Vídeos');
  }

  // Include images via the "+" button in the prompt bar
  // Simple flow: click "+" → click image item(s) → image appears in prompt bar
  async function includeImagesInCommand(maxImages = 1) {
    log(`Incluindo ${maxImages} imagem(ns)...`);
    
    function cdpClick(x, y) {
      return new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: 'cdp-click', x: Math.round(x), y: Math.round(y) }, (response) => {
          resolve(response && response.success);
        });
      });
    }

    await delay(200);

    // Step 1: Find and click the "+" button
    const allButtons = document.querySelectorAll('button');
    let plusBtn = null;
    
    for (const btn of allButtons) {
      const rect = btn.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      if (rect.top < window.innerHeight * 0.6) continue;
      if (btn.getAttribute('aria-haspopup') === 'dialog') {
        plusBtn = btn;
        break;
      }
    }
    
    if (!plusBtn) {
      for (const btn of allButtons) {
        const rect = btn.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        if (rect.width > 60 || rect.height > 60) continue;
        if (rect.top < window.innerHeight * 0.6) continue;
        if (rect.left > window.innerWidth * 0.4) continue;
        plusBtn = btn;
        break;
      }
    }

    if (!plusBtn) {
      log('Botao "+" nao encontrado.', 'error');
      return 0;
    }

    const limit = Math.min(maxImages, 3);
    let totalIncluded = 0;

    // For each image: click "+" → wait for panel → click first item
    // The panel may close after each selection, so we reopen it each time
    for (let imgIndex = 0; imgIndex < limit; imgIndex++) {
      
      // Click the "+" button (re-find it each time in case DOM changed)
      let currentPlusBtn = null;
      for (const btn of document.querySelectorAll('button')) {
        const rect = btn.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        if (rect.top < window.innerHeight * 0.6) continue;
        if (btn.getAttribute('aria-haspopup') === 'dialog') { currentPlusBtn = btn; break; }
      }
      if (!currentPlusBtn) {
        for (const btn of document.querySelectorAll('button')) {
          const rect = btn.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) continue;
          if (rect.width > 60 || rect.height > 60) continue;
          if (rect.top < window.innerHeight * 0.6) continue;
          if (rect.left > window.innerWidth * 0.4) continue;
          currentPlusBtn = btn; break;
        }
      }
      
      if (!currentPlusBtn) {
        log('Botao "+" nao encontrado.', 'error');
        break;
      }

      const pr = currentPlusBtn.getBoundingClientRect();
      log(`Imagem ${imgIndex + 1}/${limit}: clicando no "+"...`);
      await cdpClick(pr.left + pr.width / 2, pr.top + pr.height / 2);
      
      // Wait for panel to fully open and render
      await delay(1500);

      // Find the first image item in the panel and click it
      let found = false;
      for (let attempt = 0; attempt < 5; attempt++) {
        // Look for the picker panel
        let panelEl = null;
        const dialogs = document.querySelectorAll('[role="dialog"], [aria-modal="true"]');
        if (dialogs.length > 0) panelEl = dialogs[dialogs.length - 1];
        
        if (!panelEl) {
          const inputs = document.querySelectorAll('input[placeholder*="Pesquisar"], input[placeholder*="Search"]');
          if (inputs.length > 0) {
            panelEl = inputs[0];
            for (let p = 0; p < 6; p++) { if (panelEl.parentElement) panelEl = panelEl.parentElement; }
          }
        }

        const searchRoot = panelEl || document.body;
        const imgs = searchRoot.querySelectorAll('img');
        const items = [];
        const seen = new Set();

        for (const img of imgs) {
          const ir = img.getBoundingClientRect();
          if (ir.width > 55 || ir.height > 55) continue;
          if (ir.width < 10 || ir.height < 10) continue;
          if (ir.top < 0 || ir.bottom > window.innerHeight) continue;

          let row = img;
          for (let i = 0; i < 5; i++) {
            if (!row.parentElement) break;
            row = row.parentElement;
            const rr = row.getBoundingClientRect();
            if (rr.width > 120 && rr.height >= 30 && rr.height <= 70) break;
          }
          
          if (!seen.has(row)) {
            seen.add(row);
            items.push({ el: row, rect: row.getBoundingClientRect(), text: (row.textContent || '').trim().substring(0, 40) });
          }
        }

        log(`Tentativa ${attempt + 1}: ${items.length} itens encontrados`, 'info');

        if (items.length > 0) {
          const item = items[0];
          const cx = item.rect.left + item.rect.width / 2;
          const cy = item.rect.top + item.rect.height / 2;
          log(`Clicando em "${item.text}" (${Math.round(cx)}, ${Math.round(cy)})...`);
          await cdpClick(cx, cy);
          await delay(500);
          totalIncluded++;
          log(`Imagem ${totalIncluded}/${limit} selecionada!`, 'success');
          found = true;
          break;
        }
        
        await delay(600);
      }

      if (!found) {
        log(`Nenhum item encontrado no painel para imagem ${imgIndex + 1}.`, 'warning');
      }

      // Wait for panel to close after selection before reopening
      await delay(800);
    }

    // Close the picker if still open
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await delay(300);

    log(`${totalIncluded} imagem(ns) adicionada(s) ao prompt.`, totalIncluded > 0 ? 'success' : 'warning');
    return totalIncluded;
  }

  // Find the prompt input field on the Veo 3 / Google Flow page
  // Google Flow 2026: div[contenteditable="true"][role="textbox"] with placeholder "O que você quer criar?"
  function findPromptInput() {
    const selectors = [
      '[role="textbox"][contenteditable="true"]',
      '[contenteditable="true"]',
      '[contenteditable="plaintext-only"]',
      'textarea',
      '[role="textbox"]',
      'input[type="text"]',
      'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])'
    ];

    // Priority 1: Google Flow 2026 specific — div[role="textbox"] with placeholder containing "criar" or "create"
    const keywords = ['criar', 'create', 'vídeo', 'video', 'comando', 'prompt', 'crie', 'o que', 'what', 'quer criar'];
    
    // Check role="textbox" elements first (Flow 2026 uses this)
    const textboxes = document.querySelectorAll('[role="textbox"]');
    for (const el of textboxes) {
      const rect = el.getBoundingClientRect();
      if (rect.width < 100 || rect.height <= 0) continue;
      
      // Check internal placeholder text (Flow puts placeholder in <p> inside the div)
      const innerText = (el.textContent || '').trim().toLowerCase();
      const placeholder = (el.placeholder || el.getAttribute('aria-label') || el.getAttribute('data-placeholder') || '').toLowerCase();
      const parentText = (el.parentElement?.textContent || '').toLowerCase();
      
      if (keywords.some(kw => innerText.includes(kw) || placeholder.includes(kw) || parentText.includes(kw))) {
        return el;
      }
      // If it's the only textbox near the bottom, use it
      if (rect.bottom > window.innerHeight * 0.7) {
        return el;
      }
    }

    // Priority 2: find by placeholder/aria-label keywords on all input types
    for (const selector of selectors) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        const placeholder = (el.placeholder || el.getAttribute('aria-label') || el.getAttribute('data-placeholder') || '').toLowerCase();
        if (keywords.some(kw => placeholder.includes(kw))) {
          const rect = el.getBoundingClientRect();
          if (rect.width > 0 && rect.height > 0) {
            return el;
          }
        }
      }
    }

    // Priority 3: find by parent container with those keywords (for contenteditable inside a wrapper)
    const allEditables = document.querySelectorAll('[contenteditable="true"], [contenteditable="plaintext-only"]');
    for (const el of allEditables) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 100 && rect.height > 0) {
        const parentText = (el.parentElement?.textContent || '').toLowerCase();
        const parentPlaceholder = (el.closest('[data-placeholder]')?.getAttribute('data-placeholder') || '').toLowerCase();
        if (keywords.some(kw => parentText.includes(kw) || parentPlaceholder.includes(kw))) {
          return el;
        }
      }
    }

    // Priority 4: get visible contenteditable/textarea near the bottom of the page (likely the prompt bar)
    for (const selector of selectors) {
      const elements = document.querySelectorAll(selector);
      const candidates = [];
      for (const el of elements) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 100 && rect.height > 0 && rect.bottom > window.innerHeight * 0.5) {
          candidates.push({ el, bottom: rect.bottom });
        }
      }
      candidates.sort((a, b) => b.bottom - a.bottom);
      if (candidates.length > 0) return candidates[0].el;
    }

    // Last resort: any visible input
    for (const selector of selectors) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          return el;
        }
      }
    }

    return null;
  }

  // Enter AND submit prompt in one atomic CDP operation
  // background.js handles: focus textbox → clear → insert text → find submit button → click
  async function enterAndSubmitPrompt(promptText) {
    log('Enviando prompt atomicamente via CDP...');

    const result = await chrome.runtime.sendMessage({ type: 'cdp-full-prompt', text: promptText });
    
    if (!result || !result.success) {
      throw new Error(`CDP full-prompt falhou: ${result?.error || 'erro desconhecido'}`);
    }

    log(`Prompt enviado! Botao clicado em (${result.x}, ${result.y})`, 'success');
    return true;
  }

  // Keep enterPrompt and submitPrompt as wrappers for backward compatibility
  async function enterPrompt(promptText) {
    // This is now handled by enterAndSubmitPrompt, but we keep it for callers
    // that still use the two-step flow
    log('enterPrompt chamado (delegando para atomico)...');
    return true; // No-op, enterAndSubmitPrompt does everything
  }

  async function submitPrompt() {
    // This is now handled by enterAndSubmitPrompt
    log('submitPrompt chamado (delegando para atomico)...');
    return true; // No-op, enterAndSubmitPrompt does everything
  }

  // Format time remaining
  function formatTimeRemaining(seconds) {
    if (seconds < 60) {
      return `${seconds}s restantes`;
    }
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}m ${secs}s restantes`;
  }

  // Wait with countdown - works in background tabs
  async function waitWithCountdown(seconds, message) {
    log(`${message} (${seconds} segundos)...`);
    
    // For background tab support, use longer intervals with background timer
    const updateInterval = 5; // Update every 5 seconds for background compatibility
    let remaining = seconds;
    
    while (remaining > 0) {
      if (shouldStop) return;
      
      // Check if paused
      while (isPaused && !shouldStop) {
        updateProgress(0, 0, 'Pausado', formatTimeRemaining(remaining));
        await delay(500);
      }
      
      if (shouldStop) return;
      updateProgress(0, 0, message, formatTimeRemaining(remaining));
      
      // Wait for the interval or remaining time, whichever is smaller
      const waitTime = Math.min(updateInterval, remaining);
      await backgroundDelay(waitTime * 1000);
      remaining -= waitTime;
    }
  }

  // Wait for pause to end
  async function waitIfPaused() {
    while (isPaused && !shouldStop) {
      await delay(500);
    }
  }

  // Keep track of known video URLs to detect new ones
  let knownVideoUrls = new Set();

  // Detect newly generated video on the page
  function detectNewVideo() {
    const currentVideos = [];
    
    // Look for video elements
    const videoElements = document.querySelectorAll('video');
    videoElements.forEach(video => {
      if (video.src && !knownVideoUrls.has(video.src)) {
        currentVideos.push(video.src);
      }
      const sources = video.querySelectorAll('source');
      sources.forEach(source => {
        if (source.src && !knownVideoUrls.has(source.src)) {
          currentVideos.push(source.src);
        }
      });
    });

    // Look for download links
    const links = document.querySelectorAll('a[href*=".mp4"], a[href*="video"], a[download]');
    links.forEach(link => {
      if (link.href && !knownVideoUrls.has(link.href) && 
          (link.href.includes('.mp4') || link.href.includes('video'))) {
        currentVideos.push(link.href);
      }
    });

    // Return first new video found and add to known set
    if (currentVideos.length > 0) {
      const newVideo = currentVideos[0];
      knownVideoUrls.add(newVideo);
      return newVideo;
    }

    return null;
  }

  // Initialize known videos on page load
  function initializeKnownVideos() {
    knownVideoUrls.clear();
    
    const videoElements = document.querySelectorAll('video');
    videoElements.forEach(video => {
      if (video.src) knownVideoUrls.add(video.src);
      const sources = video.querySelectorAll('source');
      sources.forEach(source => {
        if (source.src) knownVideoUrls.add(source.src);
      });
    });

    const links = document.querySelectorAll('a[href*=".mp4"], a[href*="video"], a[download]');
    links.forEach(link => {
      if (link.href && (link.href.includes('.mp4') || link.href.includes('video'))) {
        knownVideoUrls.add(link.href);
      }
    });
  }

  // Detect Error 253 (quota limit) on the page
  // Google Flow shows error messages when rate limited
  function detectQuotaError() {
    const errorKeywords = [
      'error code 253',
      'quota limit',
      'exceeds the quota',
      'rate limit',
      'too many requests',
      'limite de cota',
      'muitas solicitações',
      'tente novamente mais tarde',
      'try again later',
      'number of requests sent exceeds'
    ];
    
    // Scan visible text on the page for error messages
    const allText = document.body.innerText.toLowerCase();
    for (const keyword of errorKeywords) {
      if (allText.includes(keyword)) {
        return keyword;
      }
    }
    
    // Also check for error dialog/toast/snackbar elements
    const errorSelectors = [
      '[role="alert"]',
      '[role="alertdialog"]',
      '.error-message',
      '.snackbar',
      '[class*="error"]',
      '[class*="toast"]',
      '[class*="snack"]'
    ];
    
    for (const selector of errorSelectors) {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        const text = (el.textContent || '').toLowerCase();
        for (const keyword of errorKeywords) {
          if (text.includes(keyword)) {
            return keyword;
          }
        }
      }
    }
    
    return null;
  }

  // Click "Reutilizar comando" (Reuse command) on the most recent video
  // The button is a small circular arrow icon on the video card
  // Clicking it reloads the prompt area with the same images attached
  async function clickReuseCommand() {
    log('Procurando "Reutilizar comando"...');

    function cdpClick(x, y) {
      return new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: 'cdp-click', x: Math.round(x), y: Math.round(y) }, (response) => {
          resolve(response && response.success);
        });
      });
    }

    // Find ALL buttons/elements and check for reutilizar
    const allElements = document.querySelectorAll('button, [role="button"], [role="menuitem"], div[tabindex], span, a');
    
    for (const el of allElements) {
      const text = (el.textContent || '').trim().toLowerCase();
      const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
      const title = (el.getAttribute('title') || '').toLowerCase();
      
      if (text.includes('reutilizar') || ariaLabel.includes('reutilizar') || ariaLabel.includes('reuse') ||
          title.includes('reutilizar') || title.includes('reuse')) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          log(`"Reutilizar comando" encontrado, clicando via CDP...`, 'info');
          await cdpClick(rect.left + rect.width / 2, rect.top + rect.height / 2);
          await delay(2000);
          return true;
        }
      }
    }

    // Second try: find the circular arrow icon button on the most recent video
    // It's typically a small button near the video description text
    const allButtons = document.querySelectorAll('button, [role="button"]');
    const reuseButtons = [];
    
    for (const btn of allButtons) {
      const rect = btn.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      if (rect.width > 50 || rect.height > 50) continue;
      
      // Small icon button with SVG
      if (btn.querySelector('svg') || btn.querySelector('mat-icon') || btn.querySelector('[class*="icon"]')) {
        const ariaLabel = (btn.getAttribute('aria-label') || '').toLowerCase();
        const title = (btn.getAttribute('title') || '').toLowerCase();
        if (ariaLabel.includes('reutilizar') || ariaLabel.includes('reuse') ||
            title.includes('reutilizar') || title.includes('reuse')) {
          reuseButtons.push({ btn, rect });
        }
      }
    }

    if (reuseButtons.length > 0) {
      // Click the first one (most recent video)
      const { btn, rect } = reuseButtons[0];
      log(`Botao reutilizar encontrado, clicando via CDP...`, 'info');
      await cdpClick(rect.left + rect.width / 2, rect.top + rect.height / 2);
      await delay(2000);
      return true;
    }

    throw new Error('Não foi possível encontrar "Reutilizar comando"');
  }

  // Main automation function
  async function runAutomation(settings) {
    if (isRunning) {
      log('Automação já está em execução.', 'warning');
      return;
    }

    isRunning = true;
    shouldStop = false;
    isPaused = false;
    currentSettings = settings;
    generatedVideos = []; // Reset tracked videos

    // Start keep-alive for background tab support
    startKeepAlive();

    // Initialize known videos before starting
    initializeKnownVideos();

    const { prompts, batchSize, waitTime, includeImages, imageCount } = settings;
    totalPrompts = prompts.length;
    let processedCount = 0;
    let batchCount = 0;

    log(`Iniciando automação com ${totalPrompts} prompts...`, 'info');
    log(`Lote: ${batchSize} prompts | Espera: ${waitTime / 1000}s`, 'info');
    
    // Save initial state
    currentPromptIndex = 0;
    saveAutomationState();
    broadcastState();

    try {
      for (let i = 0; i < totalPrompts; i++) {
        if (shouldStop) {
          log('Automação interrompida pelo usuário.', 'warning');
          break;
        }
        
        // Update current index and save state
        currentPromptIndex = i;
        saveAutomationState();
        broadcastState();

        // Wait if paused
        await waitIfPaused();
        if (shouldStop) break;

        const prompt = prompts[i];
        const isFirstPrompt = (i === 0);
        updateProgress(i, totalPrompts, `Processando prompt ${i + 1}/${totalPrompts}...`);
        log(`Processando prompt ${i + 1}/${totalPrompts}...`);

        if (includeImages) {
          // ALL PROMPTS WITH IMAGES (same flow for every prompt):
          // Click "+" → select images → enter prompt → submit
          await waitIfPaused();
          if (shouldStop) break;
          
          try {
            log(`Prompt ${i + 1}: incluindo imagens via botao "+"...`, 'info');
            await includeImagesInCommand(imageCount || 1);
            await delay(200);
          } catch (e) {
            log(`Aviso ao processar imagens: ${e.message}`, 'warning');
          }

          await waitIfPaused();
          if (shouldStop) break;

          try {
            await enterAndSubmitPrompt(prompt);
          } catch (e) {
            log(`Erro ao enviar prompt: ${e.message}`, 'error');
            continue;
          }

        } else {
          // NO IMAGES: just enter prompt and submit
          try {
            await enterAndSubmitPrompt(prompt);
          } catch (e) {
            log(`Erro ao enviar prompt: ${e.message}`, 'error');
            continue;
          }
        }

        // Wait if paused before continuing
        await waitIfPaused();
        if (shouldStop) break;

        // Check for Error 253 (quota limit) after submission
        await delay(2000); // Wait for error to appear
        const quotaError = detectQuotaError();
        if (quotaError) {
          log('⚠️ ERRO 253 DETECTADO: Limite de cota excedido!', 'error');
          log('Entrando em modo cooldown automatico (5 minutos)...', 'warning');
          
          // Retry logic with cooldown
          let retrySuccess = false;
          for (let retry = 0; retry < 3; retry++) {
            if (shouldStop) break;
            
            log(`Cooldown: tentativa ${retry + 1}/3 em 5 minutos...`, 'warning');
            await waitWithCountdown(300, `Cooldown Error 253 (tentativa ${retry + 1}/3)`);
            
            if (shouldStop) break;
            
            // Reload the page to clear error state
            log('Recarregando pagina...', 'info');
            window.location.reload();
            await delay(5000); // Wait for page to reload
            
            // Re-initialize
            initializeKnownVideos();
            
            // Try submitting the same prompt again
            try {
              await enterAndSubmitPrompt(prompt);
              await delay(2000);
              
              // Check if error persists
              if (!detectQuotaError()) {
                log(`Cooldown concluido! Prompt ${i + 1} reenviado com sucesso.`, 'success');
                retrySuccess = true;
                break;
              }
            } catch (retryErr) {
              log(`Erro no retry: ${retryErr.message}`, 'error');
            }
          }
          
          if (!retrySuccess && !shouldStop) {
            log('Limite de cota persistente. Pausando automacao.', 'error');
            log('Aguarde 1-2 horas e tente novamente.', 'warning');
            isPaused = true;
            saveAutomationState();
            broadcastState();
            await waitIfPaused();
            if (shouldStop) break;
          }
        }

        // Track newly generated video after a short delay
        await delay(300);
        const newVideo = detectNewVideo();
        if (newVideo) {
          generatedVideos.push({
            url: newVideo,
            promptIndex: i,
            prompt: prompt.substring(0, 50) + (prompt.length > 50 ? '...' : '')
          });
          log(`Video detectado para prompt ${i + 1}`, 'success');
        }

        processedCount++;
        batchCount++;

        // Check if we need to wait after batch
        if (batchCount >= batchSize && i < totalPrompts - 1) {
          log(`Lote de ${batchSize} concluido. Aguardando ${waitTime / 1000} segundos...`, 'info');
          await waitWithCountdown(waitTime / 1000, 'Aguardando proximo lote');
          batchCount = 0;
        } else {
          // 2 second delay between prompts
          await delay(2000);
        }
      }
      
      // Fase 2: Detectar e baixar de forma inteligente
      log('Todos os prompts enviados. Iniciando detecção e download...', 'info');
      
      let downloadedCountSeq = 0;
      const downloadedUrls = new Set();
      const downloadedIndices = new Set(); // To keep track of which prompt indices we already saved
      let waitAttempts = 0;
      let loopsWithoutNewVideos = 0;
      let lastDownloadedCount = 0;
      
      // Increased timeout to 400 attempts (~4.5 hours) for massive batches like 200+ videos
      while (downloadedCountSeq < totalPrompts && waitAttempts < 400) {
        if (shouldStop) break;
        
        updateProgress(downloadedCountSeq, totalPrompts, `Aguardando vídeos (${downloadedCountSeq}/${totalPrompts})...`);
        
        const allVideos = await detectAllVideosOnPage();
        const newChronologicalVideos = allVideos.filter(v => !knownVideoUrls.has(v.url));
        
        const pendingVideos = [];
        const seenSorted = new Set();
        for (const v of newChronologicalVideos) {
            if (!seenSorted.has(v.url) && !downloadedUrls.has(v.url)) {
                seenSorted.add(v.url);
                pendingVideos.push(v);
            }
        }
        
        if (pendingVideos.length > 0) {
            for (const v of pendingVideos) {
                if (shouldStop) break;
                
                downloadedUrls.add(v.url);
                
                // Tenta descobrir qual prompt é esse buscando o texto pre-capturado
                let matchedIndex = -1;
                
                if (v.promptText && prompts && prompts.length > 0) {
                  const found = findPromptIndex(v.promptText, prompts);
                  if (found !== -1) {
                    matchedIndex = found;
                  }
                }
                
                // Se não achou pelo texto, tenta achar pela posição na lista detectada (que foi revertida para cronológica)
                if (matchedIndex === -1) {
                  // find the original index of this video in newChronologicalVideos
                  const arrIndex = newChronologicalVideos.findIndex(nv => nv.url === v.url);
                  if (arrIndex !== -1 && !downloadedIndices.has(arrIndex)) {
                     matchedIndex = arrIndex;
                  } else {
                     for (let p = 0; p < totalPrompts; p++) {
                         if (!downloadedIndices.has(p)) {
                             matchedIndex = p;
                             break;
                         }
                     }
                  }
                }
                
                if (matchedIndex !== -1) {
                    downloadedIndices.add(matchedIndex);
                }
                
                const finalIndex = matchedIndex !== -1 ? matchedIndex : downloadedCountSeq;
                const paddedIndex = String(finalIndex + 1).padStart(3, '0');
                const filename = `prompt_${paddedIndex}.mp4`; // direto pra pasta de downloads (sem barra)
                
                log(`Vídeo detectado! (Prompt ${finalIndex + 1}) Baixando...`, 'info');
                
                let downloadUrl = v.url;
                
                if (v.isBlob || downloadUrl.startsWith('blob:')) {
                    try {
                      const response = await fetch(downloadUrl);
                      const blobData = await response.blob();
                      const safeBlobUrl = URL.createObjectURL(blobData);
                      
                      const a = document.createElement('a');
                      a.href = safeBlobUrl;
                      a.download = filename;
                      document.body.appendChild(a);
                      a.click();
                      document.body.removeChild(a);
                      setTimeout(() => URL.revokeObjectURL(safeBlobUrl), 15000);
                    } catch(e) {
                      log(`Falha ao baixar blob: ${e.message}`, 'error');
                    }
                    downloadedCountSeq++;
                    log(`Salvo como ${filename}`, 'success');
                } else {
                    // Fallback para background script se não for blob
                    const result = await downloadViaBackgroundGlobal(downloadUrl, filename);
                    if (result && result.success) {
                        log(`Salvo como ${filename}`, 'success');
                        downloadedCountSeq++;
                    } else {
                        log(`Falha no download: ${result?.error || 'erro desconhecido'}`, 'error');
                    }
                }
                
                await delay(2000);
            }
        } else {
            await waitWithCountdown(30, `Aguardando renderização (${downloadedCountSeq}/${totalPrompts} prontos)`);
            waitAttempts++;
            
            // Update idle tracker
            if (downloadedCountSeq === lastDownloadedCount) {
                loopsWithoutNewVideos++;
            } else {
                loopsWithoutNewVideos = 0;
                lastDownloadedCount = downloadedCountSeq;
            }
            
            // Se ficar 15 turnos (~7.5 minutos) sem achar NENHUM vídeo novo, assume que o Flow terminou e os restantes falharam
            if (loopsWithoutNewVideos >= 15) {
                log(`Nenhum vídeo novo gerado nos últimos 7 minutos. O Google Flow provavelmente falhou os ${totalPrompts - downloadedCountSeq} vídeos restantes. Encerrando espera.`, 'warning');
                break;
            }
        }
      }
      
      if (downloadedCountSeq < totalPrompts && !shouldStop) {
          log(`Tempo esgotado! Apenas ${downloadedCountSeq}/${totalPrompts} vídeos foram concluídos.`, 'warning');
      }

      updateProgress(totalPrompts, totalPrompts, 'Concluído!');
      log(`Automação concluída! ${downloadedCountSeq}/${totalPrompts} vídeos baixados com sucesso.`, 'success');
      
      chrome.runtime.sendMessage({ type: 'complete' });

    } catch (error) {
      log(`Erro na automação: ${error.message}`, 'error');
      
      // Check if error is quota related
      if (error.message.includes('253') || error.message.includes('quota') || error.message.includes('limit')) {
        log('⚠️ Erro de cota detectado. Aguarde 1-2 horas.', 'error');
      }
      
      chrome.runtime.sendMessage({ type: 'error', text: error.message });
    } finally {
      isRunning = false;
      shouldStop = false;
      stopKeepAlive(); // Stop background keep-alive
      clearAutomationState(); // Clear saved state when complete
      broadcastState();
    }
  }

  // Stop automation
  function stopAutomation() {
    shouldStop = true;
    isRunning = false;
    isPaused = false;
    stopKeepAlive(); // Stop background keep-alive
    clearAutomationState(); // Clear saved state when stopped
    broadcastState();
    log('Parando automação...', 'warning');
  }

  // Pause automation
  function pauseAutomation() {
    isPaused = true;
    saveAutomationState();
    broadcastState();
    log('Automação pausada.', 'warning');
  }

  // Resume automation
  function resumeAutomation() {
    isPaused = false;
    saveAutomationState();
    broadcastState();
    log('Automação retomada.', 'success');
  }

  // Helper functions for download
  async function blobToDataUrlGlobal(blobUrl) {
    const response = await fetch(blobUrl);
    const blob = await response.blob();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  }

  function downloadViaBackgroundGlobal(url, filename) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage({
        type: 'downloadVideo',
        url: url,
        filename: filename
      }, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ success: false, error: chrome.runtime.lastError.message });
        } else {
          resolve(response || { success: false, error: 'no response' });
        }
      });
    });
  }

  // scanForVideos function removed (merged into detectAllVideosOnPage)

  // Download a single video
  async function downloadVideo(url, filename) {
    return new Promise((resolve, reject) => {
      try {
        // Use Chrome downloads API via message to background
        chrome.runtime.sendMessage({
          type: 'downloadVideo',
          url: url,
          filename: filename
        }, response => {
          if (chrome.runtime.lastError) {
            // Fallback: create download link
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            resolve(true);
          } else {
            resolve(response?.success);
          }
        });
      } catch (e) {
        reject(e);
      }
    });
  }

  // Find all videos on page, scrolling if needed to load everything
  // Veo 3 grid: newest = top-left, oldest = bottom-right
  // We reverse: bottom-right = 001 (first prompt), top-left = last prompt
  // Alias for backward compatibility
  async function findVideosWithPrompts() {
    const videos = await detectAllVideosOnPage();
    return videos.map((v, i) => ({
      url: v.url,
      promptIndex: i,
      isBlob: v.isBlob
    }));
  }

  // Match video's prompt text with original prompts list
  function findPromptIndex(videoPromptText, originalPrompts) {
    if (!videoPromptText || !originalPrompts || originalPrompts.length === 0) {
      return -1;
    }
    
    // Normalize text for comparison (lowercase, remove extra whitespace)
    const normalizeText = (text) => text.toLowerCase().replace(/\s+/g, ' ').trim();
    const videoTextNorm = normalizeText(videoPromptText);
    
    // Try to find exact or partial match
    for (let i = 0; i < originalPrompts.length; i++) {
      const promptNorm = normalizeText(originalPrompts[i]);
      // Check if video text starts with prompt beginning (first 100 chars)
      const promptStart = promptNorm.substring(0, 100);
      const videoStart = videoTextNorm.substring(0, 100);
      const promptSnippet = promptNorm.substring(0, 40); // Shorter snippet for robust includes search
      
      if (promptStart === videoStart || 
          promptNorm.startsWith(videoStart) || 
          videoTextNorm.startsWith(promptStart) ||
          (promptSnippet.length > 10 && videoTextNorm.includes(promptSnippet))) {
        return i;
      }
    }
    
    // Fuzzy match: count matching words
    const videoWords = new Set(videoTextNorm.split(' ').filter(w => w.length > 4));
    let bestMatch = -1;
    let bestScore = 0;
    
    for (let i = 0; i < originalPrompts.length; i++) {
      const promptWords = normalizeText(originalPrompts[i]).split(' ').filter(w => w.length > 4);
      let matches = 0;
      for (const word of promptWords) {
        if (videoWords.has(word)) matches++;
      }
      const score = matches / Math.max(promptWords.length, 1);
      if (score > bestScore && score > 0.3) { // At least 30% match
        bestScore = score;
        bestMatch = i;
      }
    }
    
    return bestMatch;
  }
  
  // Download all videos in order (from collected videos or page scan)
  async function downloadAllVideos(folderName = 'veo3_videos', originalPrompts = []) {
    // Sanitize folder name (remove invalid characters)
    const safeFolderName = folderName.replace(/[<>:"/\\|?*]/g, '_').trim() || 'veo3_videos';
    
    let videosToDownload = [];
    
    // Priority 1: Use generatedVideos from automation (already in prompt order)
    if (generatedVideos.length > 0) {
      videosToDownload = generatedVideos.map((video, i) => ({
        url: video.url,
        promptIndex: i,
        isBlob: video.url.startsWith('blob:')
      }));
      log(`Usando ${videosToDownload.length} videos da automacao (em ordem)`, 'success');
    }
    // Priority 2: Use videos from manual detection button
    else if (collectedVideosOrder.length > 0) {
      videosToDownload = collectedVideosOrder.map((video, i) => ({
        url: video.url,
        promptIndex: i,
        isBlob: video.isBlob
      }));
      log(`Usando ${videosToDownload.length} videos detectados`, 'success');
    }
    // Priority 3: Detect videos on page right now
    else {
      const detected = await detectAllVideosOnPage();
      videosToDownload = detected.map((v, i) => {
        let matchedIndex = i; // Fallback to chronological order (1=Bottom, 2=TopRight)
        
        // Advanced Text Matching logic overrides grid position if successful!
        if (v.promptText && originalPrompts && originalPrompts.length > 0) {
          const found = findPromptIndex(v.promptText, originalPrompts);
          if (found !== -1) {
            matchedIndex = found;
          }
        }
        
        return {
          url: v.url,
          promptIndex: matchedIndex,
          isBlob: v.isBlob
        };
      });
      log(`Detectados ${videosToDownload.length} videos na pagina (Mapeados por texto/posicao)`, 'info');
    }
    
    if (videosToDownload.length === 0) {
      return { success: false, error: 'Nenhum video encontrado' };
    }

    const totalToDownload = videosToDownload.length;
    log(`Baixando exatamente ${totalToDownload} videos para a raiz de Downloads`, 'info');

    const downloadBatch = videosToDownload.map((video, i) => {
      const paddedIndex = String(video.promptIndex + 1).padStart(3, '0');
      return {
        url: video.url,
        filename: `prompt_${paddedIndex}.mp4`, // SEM PASTA
        isBlob: video.isBlob
      };
    });

    let downloadedCount = 0;
    let failedCount = 0;

    // Convert blob URL to data URL so it can be sent to background script
    async function blobToDataUrl(blobUrl) {
      const response = await fetch(blobUrl);
      const blob = await response.blob();
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    // Download a single video via background script (chrome.downloads API)
    function downloadViaBackground(url, filename) {
      return new Promise((resolve) => {
        chrome.runtime.sendMessage({
          type: 'downloadVideo',
          url: url,
          filename: filename
        }, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ success: false, error: chrome.runtime.lastError.message });
          } else {
            resolve(response || { success: false, error: 'no response' });
          }
        });
      });
    }
    
    for (let i = 0; i < downloadBatch.length; i++) {
      const video = downloadBatch[i];

      try {
        let downloadUrl = video.url;

        // Direct download for blobs (Object URL to prevent .crdownload stuck/revoked bugs)
        if (video.isBlob || downloadUrl.startsWith('blob:')) {
          try {
            log(`Buscando blob para o video ${i + 1}/${totalToDownload}...`, 'info');
            const response = await fetch(downloadUrl);
            const blobData = await response.blob();
            const safeBlobUrl = URL.createObjectURL(blobData);
            
            const a = document.createElement('a');
            a.href = safeBlobUrl;
            a.download = video.filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            
            downloadedCount++;
            log(`Video ${i + 1}/${totalToDownload} baixado (ObjectURL): ${video.filename}`, 'success');
            
            // Clean up memory after browser starts download
            setTimeout(() => URL.revokeObjectURL(safeBlobUrl), 15000);
          } catch(err) {
            failedCount++;
            log(`Falha critica no blob ${i + 1}: ${err.message}`, 'error');
          }
        } else {
          // Use chrome.downloads API via background script for normal URLs
          const result = await downloadViaBackground(downloadUrl, video.filename);

          if (result && result.success) {
            downloadedCount++;
            log(`Video ${i + 1}/${totalToDownload} baixado: ${video.filename}`, 'success');
          } else {
            failedCount++;
            log(`Falha ao baixar video ${i + 1}/${totalToDownload}: ${result?.error || 'erro desconhecido'}`, 'error');
          }
        }
        
        // Wait between downloads to avoid overwhelming the browser
        if (i < downloadBatch.length - 1) {
          await delay(2000); // 2 seconds between downloads as requested
        }
      } catch (e) {
        failedCount++;
        log(`Erro ao baixar video ${i + 1}/${totalToDownload}: ${e.message}`, 'error');
      }
    }

    log(`Download concluido: ${downloadedCount}/${totalToDownload} videos baixados` + (failedCount > 0 ? `, ${failedCount} falharam` : ''), downloadedCount === totalToDownload ? 'success' : 'warning');
    return { success: true, count: downloadedCount, total: totalToDownload };
  }

  // ============ MESSAGE LISTENER ============
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'startAutomation') {
      runAutomation(message.settings);
      sendResponse({ success: true });
    } else if (message.action === 'stopAutomation') {
      stopAutomation();
      sendResponse({ success: true });
    } else if (message.action === 'pauseAutomation') {
      pauseAutomation();
      sendResponse({ success: true });
    } else if (message.action === 'resumeAutomation') {
      resumeAutomation();
      sendResponse({ success: true });
    } else if (message.action === 'scanVideos') {
      sendResponse({ count: collectedVideosOrder.length });
    } else if (message.action === 'startVideoScan') {
      startContinuousVideoScan();
      sendResponse({ success: true });
    } else if (message.action === 'stopVideoScan') {
      stopContinuousVideoScan();
      sendResponse({ success: true, count: collectedVideosOrder.length });
    } else if (message.action === 'getCollectedVideos') {
      const sortedVideos = getSortedCollectedVideos();
      sendResponse({ videos: sortedVideos.map(v => v.url) });
    } else if (message.action === 'downloadAllVideos') {
      // Pass prompts for text matching
      downloadAllVideos(message.folderName, message.prompts || []).then(result => {
        sendResponse(result);
      });
      return true; // Keep message channel open for async response
    } else if (message.action === 'startImageScan') {
      startContinuousImageScan();
      sendResponse({ success: true });
    } else if (message.action === 'stopImageScan') {
      stopContinuousImageScan();
      sendResponse({ success: true, count: collectedImagesOrder.length });
    } else if (message.action === 'downloadAllImages') {
      downloadAllImages(message.folderName).then(result => {
        sendResponse(result);
      });
      return true;
    } else if (message.action === 'getAutomationState') {
      sendResponse({
        isRunning,
        isPaused,
        currentPromptIndex,
        totalPrompts,
        currentSettings
      });
    }
    return true;
  });

  // Check for saved state on load
  chrome.storage.local.get(['automationState', 'automationSettings'], (result) => {
    if (result.automationState?.running && result.automationSettings) {
      // Resume automation if it was running
      log('Retomando automação...', 'info');
      runAutomation(result.automationSettings);
    }
  });

  console.log('[Veo3 Automation] Content script loaded.');
})();

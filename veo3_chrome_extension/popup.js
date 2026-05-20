// Popup Script for Veo 3 Automation

// License verification URL (GitHub Gist API - better for avoiding cache issues)
const GIST_ID = '5e7e455637d58922f43273df0f33ad01';
const GIST_FILENAME = 'clientes.json';

document.addEventListener('DOMContentLoaded', () => {
  // License elements
  const licenseScreen = document.getElementById('licenseScreen');
  const mainContent = document.getElementById('mainContent');
  const licenseKeyInput = document.getElementById('licenseKeyInput');
  const activateLicenseBtn = document.getElementById('activateLicenseBtn');
  const licenseError = document.getElementById('licenseError');
  const invalidLicenseInfo = document.getElementById('invalidLicenseInfo');
  const logoutBtn = document.getElementById('logoutBtn');

  // Main content elements
  const promptsTextarea = document.getElementById('prompts');
  const promptCount = document.getElementById('promptCount');
  const batchSizeInput = document.getElementById('batchSize');
  const waitTimeInput = document.getElementById('waitTime');
  const includeImagesCheckbox = document.getElementById('includeImages');
  const imageCountInput = document.getElementById('imageCount');
  const imageCountRow = document.getElementById('imageCountRow');
  const startBtn = document.getElementById('startBtn');
  const pauseBtn = document.getElementById('pauseBtn');
  const resumeBtn = document.getElementById('resumeBtn');
  const stopBtn = document.getElementById('stopBtn');
  const statusContainer = document.getElementById('statusContainer');
  const statusText = document.getElementById('statusText');
  const progressFill = document.getElementById('progressFill');
  const progressText = document.getElementById('progressText');
  const timeRemaining = document.getElementById('timeRemaining');
  const logContainer = document.getElementById('logContainer');
  const downloadInfo = document.getElementById('downloadInfo');
  const downloadAllBtn = document.getElementById('downloadAllBtn');
  const startScanBtn = document.getElementById('startScanBtn');

  // Image download elements
  const imageDownloadInfo = document.getElementById('imageDownloadInfo');
  const startImageScanBtn = document.getElementById('startImageScanBtn');
  const imageFolderNameInput = document.getElementById('imageFolderName');
  const downloadAllImagesBtn = document.getElementById('downloadAllImagesBtn');

  // Tab elements
  const tabBtns = document.querySelectorAll('.tab-btn');
  const automationTab = document.getElementById('automationTab');
  const generatorTab = document.getElementById('generatorTab');
  const libraryTab = document.getElementById('libraryTab');

  // Library elements
  const insertFromLibraryBtn = document.getElementById('insertFromLibraryBtn');
  const promptsList = document.getElementById('promptsList');
  const categoryFilter = document.getElementById('categoryFilter');
  const addCategoryBtn = document.getElementById('addCategoryBtn');
  const newPromptTitle = document.getElementById('newPromptTitle');
  const newPromptText = document.getElementById('newPromptText');
  const newPromptCategory = document.getElementById('newPromptCategory');
  const savePromptBtn = document.getElementById('savePromptBtn');
  const exportPromptsBtn = document.getElementById('exportPromptsBtn');
  const importPromptsBtn = document.getElementById('importPromptsBtn');
  const importFileInput = document.getElementById('importFileInput');

  let isRunning = false;
  let isPaused = false;
  let isScanning = false;

  // Prompt Library Data
  let savedPrompts = [];
  let categories = ['Geral'];

  // Check if user has a saved license key and if it's still valid
  chrome.storage.local.get(['licenseKey', 'lastValidation'], async (result) => {
    if (result.licenseKey) {
      // Check if validation is still within 24 hours
      const now = Date.now();
      const lastValidation = result.lastValidation || 0;
      const hoursElapsed = (now - lastValidation) / (1000 * 60 * 60);
      
      if (hoursElapsed < 24) {
        // Still valid, show main content
        showMainContent();
      } else {
        // Need to re-validate (but keep email saved)
        showValidationScreen(result.licenseKey);
      }
    } else {
      showLicenseScreen();
    }
  });

  // Verify license against remote Gist using GitHub API (avoids CDN cache issues)
  async function verifyLicense(key) {
    try {
      // Use GitHub API to get fresh Gist content (no CDN caching)
      const apiUrl = `https://api.github.com/gists/${GIST_ID}`;
      const response = await fetch(apiUrl, {
        cache: 'no-store',
        headers: {
          'Accept': 'application/vnd.github+json',
          'Cache-Control': 'no-cache'
        }
      });
      
      if (!response.ok) {
        console.error('Failed to fetch license data from API, trying fallback...');
        // Fallback to raw URL with cache busting
        return await verifyLicenseFallback(key);
      }
      
      const gistData = await response.json();
      const fileContent = gistData.files[GIST_FILENAME]?.content;
      
      if (!fileContent) {
        console.error('License file not found in Gist');
        return await verifyLicenseFallback(key);
      }
      
      const data = JSON.parse(fileContent);
      const normalizedKey = key.toLowerCase().trim();
      
      // Check if key exists in valid_keys array
      if (data.valid_keys && Array.isArray(data.valid_keys)) {
        const found = data.valid_keys.some(validKey => 
          validKey.toLowerCase().trim() === normalizedKey
        );
        console.log(`License check for ${normalizedKey}: ${found ? 'FOUND' : 'NOT FOUND'}`);
        return found;
      }
      
      return false;
    } catch (error) {
      console.error('License verification error:', error);
      return await verifyLicenseFallback(key);
    }
  }
  
  // Fallback verification using raw URL
  async function verifyLicenseFallback(key) {
    try {
      const rawUrl = `https://gist.githubusercontent.com/nichosviraisyt-ctrl/${GIST_ID}/raw/${GIST_FILENAME}?t=${Date.now()}&r=${Math.random()}`;
      const response = await fetch(rawUrl, {
        cache: 'no-store',
        headers: {
          'Cache-Control': 'no-cache, no-store, must-revalidate',
          'Pragma': 'no-cache'
        }
      });
      
      if (!response.ok) {
        console.error('Fallback fetch failed');
        return false;
      }
      
      const data = await response.json();
      const normalizedKey = key.toLowerCase().trim();
      
      if (data.valid_keys && Array.isArray(data.valid_keys)) {
        return data.valid_keys.some(validKey => 
          validKey.toLowerCase().trim() === normalizedKey
        );
      }
      
      return false;
    } catch (error) {
      console.error('Fallback verification error:', error);
      return false;
    }
  }

  // Show license screen (first time - needs email input)
  function showLicenseScreen() {
    licenseScreen.style.display = 'flex';
    mainContent.style.display = 'none';
    logoutBtn.style.display = 'none';
    licenseKeyInput.value = '';
    licenseKeyInput.disabled = false;
    activateLicenseBtn.querySelector('span') ? null : null;
    updateActivateButtonText('Ativar');
  }

  // Show validation screen (email already saved, just need to validate)
  function showValidationScreen(savedEmail) {
    licenseScreen.style.display = 'flex';
    mainContent.style.display = 'none';
    logoutBtn.style.display = 'none';
    licenseKeyInput.value = savedEmail;
    licenseKeyInput.disabled = true;
    updateActivateButtonText('Validar Licenca');
    
    // Update description text
    const description = licenseScreen.querySelector('.license-description');
    if (description) {
      description.textContent = 'Clique em Validar para verificar sua licenca (a cada 24h)';
    }
  }

  // Update activate button text
  function updateActivateButtonText(text) {
    activateLicenseBtn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <polyline points="20 6 9 17 4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      ${text}
    `;
  }

  // Show main content
  function showMainContent() {
    licenseScreen.style.display = 'none';
    mainContent.style.display = 'flex';
    logoutBtn.style.display = 'block';
    loadSavedSettings();
  }

  // Show license error with support links
  function showLicenseError(message, showSupportLinks = true) {
    licenseError.textContent = message || 'Licenca Invalida';
    licenseError.style.display = 'block';
    
    // Show support links when license is invalid
    if (showSupportLinks && invalidLicenseInfo) {
      invalidLicenseInfo.style.display = 'flex';
    }
  }

  // Hide license error
  function hideLicenseError() {
    licenseError.style.display = 'none';
    if (invalidLicenseInfo) {
      invalidLicenseInfo.style.display = 'none';
    }
  }

  // Activate license button click
  activateLicenseBtn.addEventListener('click', async () => {
    const key = licenseKeyInput.value.trim();
    
    if (!key) {
      showLicenseError('Por favor, digite seu email');
      return;
    }

    activateLicenseBtn.disabled = true;
    activateLicenseBtn.innerHTML = `
      <svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="60" stroke-dashoffset="20"/>
      </svg>
      Verificando...
    `;
    hideLicenseError();

    const isValid = await verifyLicense(key);

    if (isValid) {
      // Save license key and validation timestamp
      chrome.storage.local.set({ 
        licenseKey: key,
        lastValidation: Date.now()
      });
      showMainContent();
    } else {
      // Show error with support links
      showLicenseError('Licenca Invalida - email nao encontrado', true);
    }

    activateLicenseBtn.disabled = false;
    // Restore button text based on whether email was already saved
    const isRevalidation = licenseKeyInput.disabled;
    updateActivateButtonText(isRevalidation ? 'Validar Licenca' : 'Ativar');
  });

  // Enter key to activate
  licenseKeyInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      activateLicenseBtn.click();
    }
  });

  // Logout button click
  logoutBtn.addEventListener('click', () => {
    chrome.storage.local.remove(['licenseKey']);
    showLicenseScreen();
    licenseKeyInput.value = '';
    hideLicenseError();
  });

  // Load saved settings
  function loadSavedSettings() {
    chrome.storage.local.get(['prompts', 'batchSize', 'waitTime', 'includeImages', 'imageCount'], (result) => {
      if (result.prompts) {
        promptsTextarea.value = result.prompts;
      }
      if (result.batchSize) batchSizeInput.value = result.batchSize;
      if (result.waitTime) waitTimeInput.value = result.waitTime;
      if (result.includeImages !== undefined) includeImagesCheckbox.checked = result.includeImages;
      if (result.imageCount) imageCountInput.value = result.imageCount;
      
      imageCountRow.style.display = includeImagesCheckbox.checked ? 'flex' : 'none';
      updatePromptCount();
    });
    
    // Also check for saved automation state
    restoreAutomationState();
  }
  
  // Restore automation state from storage
  function restoreAutomationState() {
    chrome.storage.local.get(['automationState'], (result) => {
      if (result.automationState) {
        const state = result.automationState;
        
        // Check if state is not too old (max 2 hours)
        const ageMs = Date.now() - (state.timestamp || 0);
        const maxAgeMs = 2 * 60 * 60 * 1000; // 2 hours
        
        if (ageMs < maxAgeMs && state.isRunning) {
          // Restore running state
          isRunning = true;
          isPaused = state.isPaused || false;
          
          // Show status container
          statusContainer.style.display = 'block';
          updateButtonStates();
          
          // Update progress display
          const current = state.currentPromptIndex || 0;
          const total = state.totalPrompts || 0;
          updateProgress(current, total);
          
          if (isPaused) {
            statusText.textContent = 'Pausado';
          } else {
            statusText.textContent = `Processando prompt ${current + 1}/${total}...`;
          }
          
          addLog(`Automação em andamento: prompt ${current + 1} de ${total}`, 'info');
          
          // Also try to get live state from content script
          requestLiveState();
        }
      }
    });
  }
  
  // Request live state from content script
  function requestLiveState() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'getAutomationState' }, (response) => {
          if (chrome.runtime.lastError) {
            // Content script not available
            return;
          }
          if (response && response.isRunning) {
            isRunning = true;
            isPaused = response.isPaused || false;
            
            statusContainer.style.display = 'block';
            updateButtonStates();
            updateProgress(response.currentPromptIndex || 0, response.totalPrompts || 0);
            
            if (isPaused) {
              statusText.textContent = 'Pausado';
            } else {
              statusText.textContent = `Processando prompt ${(response.currentPromptIndex || 0) + 1}/${response.totalPrompts || 0}...`;
            }
          }
        });
      }
    });
  }

  // Parse prompts from textarea
  function parsePrompts(text) {
    return text
      .split(/\n\s*\n/)
      .map(p => p.trim())
      .filter(p => p.length > 0);
  }

  // Update prompt count
  function updatePromptCount() {
    const prompts = parsePrompts(promptsTextarea.value);
    const count = prompts.length;
    promptCount.textContent = `${count} prompt${count !== 1 ? 's' : ''} detectado${count !== 1 ? 's' : ''}`;
  }

  promptsTextarea.addEventListener('input', () => {
    updatePromptCount();
    chrome.storage.local.set({ prompts: promptsTextarea.value });
  });

  batchSizeInput.addEventListener('change', () => {
    chrome.storage.local.set({ batchSize: parseInt(batchSizeInput.value) });
  });

  waitTimeInput.addEventListener('change', () => {
    chrome.storage.local.set({ waitTime: parseInt(waitTimeInput.value) });
  });

  includeImagesCheckbox.addEventListener('change', () => {
    chrome.storage.local.set({ includeImages: includeImagesCheckbox.checked });
    // Show/hide image count row
    imageCountRow.style.display = includeImagesCheckbox.checked ? 'flex' : 'none';
  });

  imageCountInput.addEventListener('change', () => {
    chrome.storage.local.set({ imageCount: parseInt(imageCountInput.value) || 1 });
  });

  // Add log entry
  function addLog(message, type = 'info') {
    const entry = document.createElement('div');
    entry.className = `log-entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
  }

  // Clear logs
  function clearLogs() {
    logContainer.innerHTML = '';
  }

  // Update progress
  function updateProgress(current, total) {
    const percent = total > 0 ? (current / total) * 100 : 0;
    progressFill.style.width = `${percent}%`;
    progressText.textContent = `${current} / ${total}`;
  }

  // Update button states
  function updateButtonStates() {
    startBtn.style.display = isRunning ? 'none' : 'flex';
    pauseBtn.style.display = isRunning && !isPaused ? 'flex' : 'none';
    resumeBtn.style.display = isRunning && isPaused ? 'flex' : 'none';
    stopBtn.style.display = isRunning ? 'flex' : 'none';
  }

  // Start automation
  startBtn.addEventListener('click', async () => {
    const prompts = parsePrompts(promptsTextarea.value);
    
    if (prompts.length === 0) {
      alert('Por favor, insira pelo menos um prompt.');
      return;
    }

    // Verify license before starting automation
    const { licenseKey, lastValidation } = await chrome.storage.local.get(['licenseKey', 'lastValidation']);
    if (!licenseKey) {
      showLicenseScreen();
      showLicenseError('Licenca necessaria para usar a automacao', true);
      return;
    }

    // Check if validation is still within 24 hours
    const now = Date.now();
    const hoursElapsed = (now - (lastValidation || 0)) / (1000 * 60 * 60);
    
    if (hoursElapsed >= 24) {
      // Need to re-validate - don't show buy links since user already has license
      showValidationScreen(licenseKey);
      showLicenseError('Validacao expirada - clique em Validar para continuar', false);
      return;
    }

    const settings = {
      prompts,
      batchSize: parseInt(batchSizeInput.value),
      waitTime: parseInt(waitTimeInput.value) * 1000,
      includeImages: includeImagesCheckbox.checked,
      imageCount: parseInt(imageCountInput.value) || 1
    };

    // Get active tab
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    
    if (!tab) {
      alert('Nao foi possivel encontrar a aba ativa.');
      return;
    }

    // Check if we're on the Veo 3/Flow page specifically
    // URL format: labs.google/fx/{lang}/tools/flow/project/{project-id}
    const isVeo3Page = tab.url.includes('labs.google/fx/') && tab.url.includes('/tools/') ||
                       tab.url.includes('labs.google.com/fx/') && tab.url.includes('/tools/');
    
    if (!isVeo3Page) {
      alert('Por favor, navegue ate a pagina do Veo 3 Flow (labs.google/fx/.../tools/flow/project/...) antes de iniciar a automacao.');
      return;
    }

    isRunning = true;
    isPaused = false;
    updateButtonStates();
    statusContainer.style.display = 'block';
    clearLogs();
    updateProgress(0, prompts.length);
    addLog('Licenca verificada! Iniciando automacao...', 'success');

    // Save settings to storage for content script
    chrome.storage.local.set({ 
      automationSettings: settings,
      automationState: {
        running: true,
        paused: false,
        currentIndex: 0,
        batchCount: 0
      }
    });

    // Send message to content script to start automation
    try {
      chrome.tabs.sendMessage(tab.id, { 
        action: 'startAutomation', 
        settings 
      }, (response) => {
        if (chrome.runtime.lastError) {
          chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ['content.js']
          }).then(() => {
            setTimeout(() => {
              chrome.tabs.sendMessage(tab.id, { 
                action: 'startAutomation', 
                settings 
              });
            }, 500);
          }).catch(err => {
            addLog(`Erro ao injetar script: ${err.message}`, 'error');
            stopAutomation();
          });
        }
      });
    } catch (error) {
      addLog(`Erro: ${error.message}`, 'error');
      stopAutomation();
    }
  });

  // Pause automation
  pauseBtn.addEventListener('click', async () => {
    isPaused = true;
    updateButtonStates();
    statusText.textContent = 'Pausado';
    addLog('Automação pausada.', 'warning');
    
    chrome.storage.local.set({ 
      automationState: { running: true, paused: true }
    });

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      chrome.tabs.sendMessage(tab.id, { action: 'pauseAutomation' });
    }
  });

  // Resume automation
  resumeBtn.addEventListener('click', async () => {
    isPaused = false;
    updateButtonStates();
    statusText.textContent = 'Processando...';
    addLog('Automação retomada.', 'success');
    
    chrome.storage.local.set({ 
      automationState: { running: true, paused: false }
    });

    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      chrome.tabs.sendMessage(tab.id, { action: 'resumeAutomation' });
    }
  });

  // Stop automation
  stopBtn.addEventListener('click', () => {
    stopAutomation();
  });

  function stopAutomation() {
    isRunning = false;
    isPaused = false;
    updateButtonStates();
    
    chrome.storage.local.set({ 
      automationState: { running: false, paused: false }
    });

    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { action: 'stopAutomation' });
      }
    });

    addLog('Automação interrompida pelo usuário.', 'warning');
  }

  // Scan for videos on page load
  async function scanForVideos() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && (tab.url.includes('labs.google') || tab.url.includes('google.com'))) {
      try {
        chrome.tabs.sendMessage(tab.id, { action: 'scanVideos' }, (response) => {
          if (chrome.runtime.lastError) {
            downloadInfo.textContent = 'Abra a página do Veo 3 para detectar vídeos';
            return;
          }
          if (response && response.count !== undefined) {
            downloadInfo.textContent = `${response.count} vídeo${response.count !== 1 ? 's' : ''} detectado${response.count !== 1 ? 's' : ''} na página`;
            downloadAllBtn.disabled = response.count === 0;
          }
        });
      } catch (e) {
        downloadInfo.textContent = 'Abra a página do Veo 3 para detectar vídeos';
      }
    } else {
      downloadInfo.textContent = 'Abra a página do Veo 3 para detectar vídeos';
      downloadAllBtn.disabled = true;
    }
  }

  // Folder name input
  const folderNameInput = document.getElementById('folderName');
  
  // Save folder name when changed
  folderNameInput.addEventListener('change', () => {
    chrome.storage.local.set({ folderName: folderNameInput.value });
  });
  
  // Load saved folder name
  chrome.storage.local.get(['folderName'], (result) => {
    if (result.folderName) {
      folderNameInput.value = result.folderName;
    }
  });

  // Download all videos
  downloadAllBtn.addEventListener('click', async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      alert('Nao foi possivel encontrar a aba ativa.');
      return;
    }

    const folderName = folderNameInput.value.trim() || 'veo3_videos';
    // Save folder name
    chrome.storage.local.set({ folderName: folderName });

    downloadAllBtn.disabled = true;
    downloadAllBtn.textContent = 'Baixando...';
    addLog(`Iniciando download para pasta: ${folderName}`, 'info');

    // Get prompts for text matching
    const prompts = parsePrompts(promptsTextarea.value);
    chrome.tabs.sendMessage(tab.id, { action: 'downloadAllVideos', folderName: folderName, prompts: prompts }, (response) => {
      if (chrome.runtime.lastError) {
        addLog('Erro ao comunicar com a página.', 'error');
        downloadAllBtn.disabled = false;
        downloadAllBtn.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M21 15V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Baixar Todos os Vídeos
        `;
        return;
      }
      
      if (response && response.success) {
        addLog(`${response.count} vídeos baixados com sucesso!`, 'success');
      } else if (response && response.error) {
        addLog(`Erro: ${response.error}`, 'error');
      }
      
      downloadAllBtn.disabled = false;
      downloadAllBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M21 15V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Baixar Todos os Vídeos
      `;
    });
  });

  // Scan for videos on popup open
  scanForVideos();

  // Detect videos button - instantly finds all videos on page
  startScanBtn.addEventListener('click', async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || (!tab.url.includes('labs.google') && !tab.url.includes('google.com'))) {
      alert('Por favor, navegue ate a pagina do Veo 3.');
      return;
    }

    downloadInfo.textContent = 'Detectando videos...';
    addLog('Detectando todos os videos na pagina...', 'info');

    chrome.tabs.sendMessage(tab.id, { action: 'startVideoScan' }, (response) => {
      if (chrome.runtime.lastError) {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['content.js']
        }).then(() => {
          setTimeout(() => {
            chrome.tabs.sendMessage(tab.id, { action: 'startVideoScan' });
          }, 500);
        });
      }
    });
  });

  // ===== DOWNLOAD TAB SWITCHING =====
  const downloadTabBtns = document.querySelectorAll('.download-tab-btn');
  const videoDownloadTab = document.getElementById('videoDownloadTab');
  const imageDownloadTab = document.getElementById('imageDownloadTab');

  downloadTabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      downloadTabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const tab = btn.getAttribute('data-download-tab');
      if (tab === 'videos') {
        videoDownloadTab.classList.add('active');
        imageDownloadTab.classList.remove('active');
      } else {
        videoDownloadTab.classList.remove('active');
        imageDownloadTab.classList.add('active');
      }
    });
  });

  // ===== IMAGE SCANNING =====
  startImageScanBtn.addEventListener('click', async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || (!tab.url.includes('labs.google') && !tab.url.includes('google.com'))) {
      alert('Por favor, navegue ate a pagina do Veo 3.');
      return;
    }

    imageDownloadInfo.textContent = 'Detectando imagens...';
    addLog('Detectando todas as imagens na pagina...', 'info');

    chrome.tabs.sendMessage(tab.id, { action: 'startImageScan' }, (response) => {
      if (chrome.runtime.lastError) {
        chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['content.js']
        }).then(() => {
          setTimeout(() => {
            chrome.tabs.sendMessage(tab.id, { action: 'startImageScan' });
          }, 500);
        });
      }
    });
  });

  downloadAllImagesBtn.addEventListener('click', async () => {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab) {
      alert('Nao foi possivel encontrar a aba ativa.');
      return;
    }

    const folderName = imageFolderNameInput.value.trim() || 'veo3_imagens';
    chrome.storage.local.set({ imageFolderName: folderName });

    downloadAllImagesBtn.disabled = true;
    downloadAllImagesBtn.textContent = 'Baixando...';
    addLog(`Iniciando download de imagens para pasta: ${folderName}`, 'info');

    chrome.tabs.sendMessage(tab.id, { action: 'downloadAllImages', folderName: folderName }, (response) => {
      if (chrome.runtime.lastError) {
        addLog('Erro ao comunicar com a pagina.', 'error');
      } else if (response && response.success) {
        addLog(`${response.count} imagens baixadas com sucesso!`, 'success');
      } else if (response && response.error) {
        addLog(`Erro: ${response.error}`, 'error');
      }

      downloadAllImagesBtn.disabled = false;
      downloadAllImagesBtn.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M21 15V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <polyline points="7 10 12 15 17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <line x1="12" y1="15" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Baixar Todas as Imagens
      `;
    });
  });

  // Listen for messages from content script
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'log') {
      addLog(message.text, message.level || 'info');
    } else if (message.type === 'progress') {
      updateProgress(message.current, message.total);
      statusText.textContent = message.status || 'Processando...';
      
      if (message.timeRemaining) {
        timeRemaining.textContent = message.timeRemaining;
      }
    } else if (message.type === 'complete') {
      addLog('Automação concluída com sucesso!', 'success');
      isRunning = false;
      isPaused = false;
      updateButtonStates();
      statusText.textContent = 'Concluído!';
    } else if (message.type === 'error') {
      addLog(`Erro: ${message.text}`, 'error');
    } else if (message.type === 'videoCount') {
      downloadInfo.textContent = `${message.count} video${message.count !== 1 ? 's' : ''} detectado${message.count !== 1 ? 's' : ''}`;
      downloadAllBtn.disabled = message.count === 0;
    } else if (message.type === 'videoScanUpdate') {
      // Real-time update during scanning
      downloadInfo.textContent = `${message.count} video${message.count !== 1 ? 's' : ''} detectado${message.count !== 1 ? 's' : ''}`;
      downloadAllBtn.disabled = message.count === 0;
    } else if (message.type === 'imageScanUpdate') {
      imageDownloadInfo.textContent = `${message.count} imagen${message.count !== 1 ? 's' : ''} detectada${message.count !== 1 ? 's' : ''}`;
      downloadAllImagesBtn.disabled = message.count === 0;
    } else if (message.type === 'automationState') {
      // Real-time state update from content script
      isRunning = message.isRunning;
      isPaused = message.isPaused;
      
      if (message.isRunning) {
        statusContainer.style.display = 'block';
        updateProgress(message.currentPromptIndex || 0, message.totalPrompts || 0);
        
        if (message.isPaused) {
          statusText.textContent = 'Pausado';
        } else {
          statusText.textContent = `Processando prompt ${(message.currentPromptIndex || 0) + 1}/${message.totalPrompts || 0}...`;
        }
      }
      
      updateButtonStates();
    }
  });

  // ==================== PROMPT LIBRARY FUNCTIONALITY ====================

  // Tab switching
  console.log('Tab buttons found:', tabBtns.length);
  console.log('Automation tab:', automationTab);
  console.log('Library tab:', libraryTab);
  
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const tabName = btn.dataset.tab;
      
      tabBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      
      automationTab.classList.remove('active');
      generatorTab.classList.remove('active');
      libraryTab.classList.remove('active');
      
      if (tabName === 'automation') {
        automationTab.classList.add('active');
      } else if (tabName === 'generator') {
        const generatorUrl = 'https://veo3automation.shop/gerador';
        chrome.tabs.create({ url: generatorUrl });
        tabBtns.forEach(b => b.classList.remove('active'));
        document.querySelector('[data-tab="automation"]').classList.add('active');
        automationTab.classList.add('active');
        return;
      } else if (tabName === 'library') {
        libraryTab.classList.add('active');
        renderPromptsList();
      }
    });
  });

  // Load library data
  function loadLibraryData() {
    console.log('Loading library data...');
    chrome.storage.local.get(['savedPrompts', 'promptCategories'], (result) => {
      savedPrompts = result.savedPrompts || [];
      categories = result.promptCategories || ['Geral'];
      console.log('Loaded prompts:', savedPrompts.length, 'Categories:', categories);
      renderCategories();
      renderPromptsList();
    });
  }

  // Save library data
  function saveLibraryData() {
    chrome.storage.local.set({
      savedPrompts: savedPrompts,
      promptCategories: categories
    });
  }

  // Render categories in dropdowns
  function renderCategories() {
    // Filter dropdown
    categoryFilter.innerHTML = '<option value="all">Todas as categorias</option>';
    categories.forEach(cat => {
      categoryFilter.innerHTML += `<option value="${cat}">${cat}</option>`;
    });

    // New prompt dropdown
    newPromptCategory.innerHTML = '';
    categories.forEach(cat => {
      newPromptCategory.innerHTML += `<option value="${cat}">${cat}</option>`;
    });
  }

  // Render prompts list
  function renderPromptsList(filterCategory = 'all') {
    const filteredPrompts = filterCategory === 'all' 
      ? savedPrompts 
      : savedPrompts.filter(p => p.category === filterCategory);

    if (filteredPrompts.length === 0) {
      promptsList.innerHTML = '<p class="empty-library">Nenhum prompt salvo. Adicione seu primeiro!</p>';
      return;
    }

    promptsList.innerHTML = filteredPrompts.map((prompt, index) => `
      <div class="prompt-item" data-id="${prompt.id}">
        <div class="prompt-item-content">
          <div class="prompt-item-title">
            ${prompt.title}
            <span class="prompt-item-category">${prompt.category}</span>
          </div>
          <div class="prompt-item-text">${prompt.text.substring(0, 60)}${prompt.text.length > 60 ? '...' : ''}</div>
        </div>
        <div class="prompt-item-actions">
          <button class="use-btn" title="Usar este prompt" data-id="${prompt.id}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="edit-btn" title="Editar" data-id="${prompt.id}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
          <button class="delete-btn" title="Excluir" data-id="${prompt.id}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <polyline points="3 6 5 6 21 6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
      </div>
    `).join('');

    // Add event listeners
    promptsList.querySelectorAll('.use-btn').forEach(btn => {
      btn.addEventListener('click', () => usePrompt(btn.dataset.id));
    });

    promptsList.querySelectorAll('.edit-btn').forEach(btn => {
      btn.addEventListener('click', () => editPrompt(btn.dataset.id));
    });

    promptsList.querySelectorAll('.delete-btn').forEach(btn => {
      btn.addEventListener('click', () => deletePrompt(btn.dataset.id));
    });
  }

  // Add prompt to textarea
  function usePrompt(id) {
    const prompt = savedPrompts.find(p => p.id === id);
    if (!prompt) return;

    const currentText = promptsTextarea.value.trim();
    if (currentText) {
      promptsTextarea.value = currentText + '\n\n' + prompt.text;
    } else {
      promptsTextarea.value = prompt.text;
    }

    updatePromptCount();
    chrome.storage.local.set({ prompts: promptsTextarea.value });

    // Switch to automation tab
    tabBtns.forEach(b => b.classList.remove('active'));
    document.querySelector('[data-tab="automation"]').classList.add('active');
    automationTab.classList.add('active');
    libraryTab.classList.remove('active');
  }

  // Edit prompt
  function editPrompt(id) {
    const prompt = savedPrompts.find(p => p.id === id);
    if (!prompt) return;

    newPromptTitle.value = prompt.title;
    newPromptText.value = prompt.text;
    newPromptCategory.value = prompt.category;

    // Change save button to update
    savePromptBtn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <polyline points="20 6 9 17 4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>
      Atualizar
    `;
    savePromptBtn.dataset.editId = id;
  }

  // Delete prompt
  function deletePrompt(id) {
    if (!confirm('Tem certeza que deseja excluir este prompt?')) return;
    
    savedPrompts = savedPrompts.filter(p => p.id !== id);
    saveLibraryData();
    renderPromptsList(categoryFilter.value);
  }

  // Save new prompt
  savePromptBtn.addEventListener('click', () => {
    const title = newPromptTitle.value.trim();
    const text = newPromptText.value.trim();
    const category = newPromptCategory.value;

    if (!title || !text) {
      alert('Preencha o titulo e o texto do prompt.');
      return;
    }

    const editId = savePromptBtn.dataset.editId;

    if (editId) {
      // Update existing
      const index = savedPrompts.findIndex(p => p.id === editId);
      if (index !== -1) {
        savedPrompts[index] = { ...savedPrompts[index], title, text, category };
      }
      delete savePromptBtn.dataset.editId;
      savePromptBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M19 21H5a2 2 0 01-2-2V5a2 2 0 012-2h11l5 5v11a2 2 0 01-2 2z" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <polyline points="17 21 17 13 7 13 7 21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <polyline points="7 3 7 8 15 8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        Salvar
      `;
    } else {
      // Add new
      const newPrompt = {
        id: Date.now().toString(),
        title,
        text,
        category,
        createdAt: new Date().toISOString()
      };
      savedPrompts.push(newPrompt);
    }

    saveLibraryData();
    renderPromptsList(categoryFilter.value);

    // Clear form
    newPromptTitle.value = '';
    newPromptText.value = '';
  });

  // Category filter
  categoryFilter.addEventListener('change', () => {
    renderPromptsList(categoryFilter.value);
  });

  // Add new category
  addCategoryBtn.addEventListener('click', () => {
    const name = prompt('Nome da nova categoria:');
    if (name && name.trim() && !categories.includes(name.trim())) {
      categories.push(name.trim());
      saveLibraryData();
      renderCategories();
    }
  });

  // Export prompts
  exportPromptsBtn.addEventListener('click', () => {
    const data = {
      prompts: savedPrompts,
      categories: categories,
      exportedAt: new Date().toISOString()
    };
    
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `veo3_prompts_${new Date().toISOString().split('T')[0]}.json`;
    a.click();
    
    URL.revokeObjectURL(url);
  });

  // Import prompts
  importPromptsBtn.addEventListener('click', () => {
    importFileInput.click();
  });

  importFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const data = JSON.parse(event.target.result);
        
        if (data.prompts && Array.isArray(data.prompts)) {
          // Merge with existing
          const existingIds = new Set(savedPrompts.map(p => p.id));
          const newPrompts = data.prompts.filter(p => !existingIds.has(p.id));
          savedPrompts = [...savedPrompts, ...newPrompts];
        }

        if (data.categories && Array.isArray(data.categories)) {
          const newCategories = data.categories.filter(c => !categories.includes(c));
          categories = [...categories, ...newCategories];
        }

        saveLibraryData();
        renderCategories();
        renderPromptsList();

        alert(`Importado com sucesso! ${data.prompts?.length || 0} prompts encontrados.`);
      } catch (err) {
        alert('Erro ao importar arquivo. Verifique se e um arquivo JSON valido.');
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  });

  // Insert from library button (opens modal)
  insertFromLibraryBtn.addEventListener('click', () => {
    if (savedPrompts.length === 0) {
      alert('Sua biblioteca esta vazia. Adicione prompts primeiro na aba Biblioteca.');
      return;
    }
    showInsertModal();
  });

  // Show insert modal
  function showInsertModal() {
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal">
        <div class="modal-header">
          <h3>Inserir da Biblioteca</h3>
          <button class="modal-close">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            </svg>
          </button>
        </div>
        <div class="modal-content">
          ${savedPrompts.map(p => `
            <div class="modal-prompt-item" data-id="${p.id}">
              <input type="checkbox" class="modal-prompt-checkbox" value="${p.id}">
              <div class="prompt-item-content">
                <div class="prompt-item-title">${p.title}</div>
                <div class="prompt-item-text">${p.text.substring(0, 40)}...</div>
              </div>
            </div>
          `).join('')}
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary modal-cancel">Cancelar</button>
          <button class="btn btn-primary modal-insert">Inserir Selecionados</button>
        </div>
      </div>
    `;

    document.body.appendChild(modal);

    // Event listeners
    modal.querySelector('.modal-close').addEventListener('click', () => modal.remove());
    modal.querySelector('.modal-cancel').addEventListener('click', () => modal.remove());
    
    modal.querySelectorAll('.modal-prompt-item').forEach(item => {
      item.addEventListener('click', (e) => {
        if (e.target.type !== 'checkbox') {
          const checkbox = item.querySelector('.modal-prompt-checkbox');
          checkbox.checked = !checkbox.checked;
        }
        item.classList.toggle('selected', item.querySelector('.modal-prompt-checkbox').checked);
      });
    });

    modal.querySelector('.modal-insert').addEventListener('click', () => {
      const selectedIds = Array.from(modal.querySelectorAll('.modal-prompt-checkbox:checked'))
        .map(cb => cb.value);
      
      if (selectedIds.length === 0) {
        alert('Selecione pelo menos um prompt.');
        return;
      }

      const selectedPrompts = savedPrompts.filter(p => selectedIds.includes(p.id));
      const textsToAdd = selectedPrompts.map(p => p.text).join('\n\n');

      const currentText = promptsTextarea.value.trim();
      if (currentText) {
        promptsTextarea.value = currentText + '\n\n' + textsToAdd;
      } else {
        promptsTextarea.value = textsToAdd;
      }

      updatePromptCount();
      chrome.storage.local.set({ prompts: promptsTextarea.value });
      modal.remove();
    });

    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.remove();
    });
  }

  // ==================== GENERATOR IA FUNCTIONALITY ====================

  const geminiApiKeyInput = document.getElementById('geminiApiKey');
  const toggleApiKeyBtn = document.getElementById('toggleApiKeyBtn');
  const audioFileInput = document.getElementById('audioFileInput');
  const uploadArea = document.getElementById('uploadArea');
  const selectedFile = document.getElementById('selectedFile');
  const selectedFileName = document.getElementById('selectedFileName');
  const audioDurationBadge = document.getElementById('audioDurationBadge');
  const removeFileBtn = document.getElementById('removeFileBtn');
  const sceneCalc = document.getElementById('sceneCalc');
  const sceneCalcText = document.getElementById('sceneCalcText');
  const refImagesInput = document.getElementById('refImagesInput');
  const imageUploadArea = document.getElementById('imageUploadArea');
  const refImagesList = document.getElementById('refImagesList');
  const generatePromptsBtn = document.getElementById('generatePromptsBtn');
  const generationStatus = document.getElementById('generationStatus');
  const generationStatusText = document.getElementById('generationStatusText');
  const generatedPromptsSection = document.getElementById('generatedPromptsSection');
  const generatedPromptsText = document.getElementById('generatedPromptsText');
  const generatedCount = document.getElementById('generatedCount');
  const copyGeneratedBtn = document.getElementById('copyGeneratedBtn');
  const sendGeneratedToAutomationBtn = document.getElementById('sendGeneratedToAutomationBtn');

  let selectedAudioFile = null;
  let audioDurationSeconds = 0;
  let referenceImages = [];

  // Load saved API key AND restore generator state
  chrome.storage.local.get(['geminiApiKey', 'generatorState'], (result) => {
    if (result.geminiApiKey) {
      geminiApiKeyInput.value = result.geminiApiKey;
    }
    if (result.generatorState) {
      restoreGeneratorState(result.generatorState);
    }
    updateGenerateButton();
  });

  function saveGeneratorState() {
    const pasteEl = document.getElementById('generatorPaste');
    const state = {
      referenceImages: referenceImages.map(img => ({
        name: img.name,
        base64: img.base64,
        mimeType: img.mimeType,
        preview: img.preview
      })),
      generatedPrompts: generatedPromptsText.value || '',
      manualPaste: pasteEl ? pasteEl.value || '' : ''
    };
    if (selectedAudioFile && audioDurationSeconds > 0) {
      const reader = new FileReader();
      reader.onload = (ev) => {
        state.audioBase64 = ev.target.result;
        state.audioName = selectedAudioFile.name;
        state.audioType = selectedAudioFile.type;
        state.audioDuration = audioDurationSeconds;
        chrome.storage.local.set({ generatorState: state });
      };
      reader.readAsDataURL(selectedAudioFile);
    } else {
      state.audioBase64 = null;
      state.audioName = null;
      state.audioType = null;
      state.audioDuration = 0;
      chrome.storage.local.set({ generatorState: state });
    }
  }

  function restoreGeneratorState(state) {
    if (state.audioBase64 && state.audioName) {
      const byteString = atob(state.audioBase64.split(',')[1]);
      const ab = new ArrayBuffer(byteString.length);
      const ia = new Uint8Array(ab);
      for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
      }
      const blob = new Blob([ab], { type: state.audioType || 'audio/mpeg' });
      selectedAudioFile = new File([blob], state.audioName, { type: state.audioType || 'audio/mpeg' });
      audioDurationSeconds = state.audioDuration || 0;

      selectedFileName.textContent = state.audioName;
      uploadArea.style.display = 'none';
      selectedFile.style.display = 'flex';

      if (audioDurationSeconds > 0) {
        const totalSecs = Math.floor(audioDurationSeconds);
        const mins = Math.floor(totalSecs / 60);
        const secs = totalSecs % 60;
        audioDurationBadge.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        audioDurationBadge.style.display = 'inline';
        const numScenes = Math.ceil(audioDurationSeconds / 8);
        sceneCalcText.textContent = `${totalSecs}s / 8 = ${numScenes} cenas`;
        sceneCalc.style.display = 'block';
      }
    }

    if (state.referenceImages && state.referenceImages.length > 0) {
      referenceImages = state.referenceImages;
      renderRefImages();
    }

    if (state.generatedPrompts) {
      generatedPromptsText.value = state.generatedPrompts;
      const promptsArray = state.generatedPrompts.split(/\n\s*\n/).filter(p => p.trim().length > 0);
      generatedCount.textContent = `${promptsArray.length} prompt${promptsArray.length !== 1 ? 's' : ''} gerado${promptsArray.length !== 1 ? 's' : ''}`;
      generatedPromptsSection.style.display = 'flex';
    }

    const pasteEl = document.getElementById('generatorPaste');
    if (state.manualPaste && pasteEl) {
      pasteEl.value = state.manualPaste;
    }

    updateGenerateButton();
  }

  geminiApiKeyInput.addEventListener('input', () => {
    chrome.storage.local.set({ geminiApiKey: geminiApiKeyInput.value.trim() });
    updateGenerateButton();
  });

  toggleApiKeyBtn.addEventListener('click', () => {
    geminiApiKeyInput.type = geminiApiKeyInput.type === 'password' ? 'text' : 'password';
  });

  uploadArea.addEventListener('click', () => {
    audioFileInput.click();
  });

  audioFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
      selectedAudioFile = file;
      selectedFileName.textContent = file.name;
      uploadArea.style.display = 'none';
      selectedFile.style.display = 'flex';

      const audioUrl = URL.createObjectURL(file);
      const audio = new Audio();
      audio.addEventListener('loadedmetadata', () => {
        audioDurationSeconds = audio.duration;
        const totalSecs = Math.floor(audioDurationSeconds);
        const mins = Math.floor(totalSecs / 60);
        const secs = totalSecs % 60;
        audioDurationBadge.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
        audioDurationBadge.style.display = 'inline';

        const numScenes = Math.ceil(audioDurationSeconds / 8);
        sceneCalcText.textContent = `${totalSecs}s / 8 = ${numScenes} cenas`;
        sceneCalc.style.display = 'block';

        URL.revokeObjectURL(audioUrl);
        updateGenerateButton();
        saveGeneratorState();
      });
      audio.addEventListener('error', () => {
        audioDurationSeconds = 0;
        audioDurationBadge.style.display = 'none';
        sceneCalc.style.display = 'none';
        URL.revokeObjectURL(audioUrl);
        alert('Nao foi possivel ler a duracao do audio. Tente outro arquivo.');
        selectedAudioFile = null;
        audioFileInput.value = '';
        uploadArea.style.display = 'flex';
        selectedFile.style.display = 'none';
        updateGenerateButton();
      });
      audio.src = audioUrl;
    }
  });

  removeFileBtn.addEventListener('click', () => {
    selectedAudioFile = null;
    audioDurationSeconds = 0;
    audioFileInput.value = '';
    uploadArea.style.display = 'flex';
    selectedFile.style.display = 'none';
    audioDurationBadge.style.display = 'none';
    sceneCalc.style.display = 'none';
    updateGenerateButton();
    saveGeneratorState();
  });

  // Reference images handling
  imageUploadArea.addEventListener('click', () => {
    refImagesInput.click();
  });

  refImagesInput.addEventListener('change', (e) => {
    const files = Array.from(e.target.files);
    const remaining = 10 - referenceImages.length;
    const toAdd = files.slice(0, remaining);
    let loaded = 0;

    toAdd.forEach(file => {
      const reader = new FileReader();
      reader.onload = (ev) => {
        referenceImages.push({
          name: file.name,
          base64: ev.target.result.split(',')[1],
          mimeType: file.type || 'image/jpeg',
          preview: ev.target.result
        });
        renderRefImages();
        loaded++;
        if (loaded === toAdd.length) {
          saveGeneratorState();
        }
      };
      reader.readAsDataURL(file);
    });

    refImagesInput.value = '';
  });

  function renderRefImages() {
    refImagesList.innerHTML = '';
    referenceImages.forEach((img, idx) => {
      const item = document.createElement('div');
      item.className = 'ref-image-item';
      item.innerHTML = `
        <img src="${img.preview}" alt="${img.name}" class="ref-image-thumb">
        <span class="ref-image-name">${img.name}</span>
        <button class="btn-remove-file" data-idx="${idx}" title="Remover">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
            <line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
        </button>
      `;
      item.querySelector('.btn-remove-file').addEventListener('click', () => {
        referenceImages.splice(idx, 1);
        renderRefImages();
        saveGeneratorState();
      });
      refImagesList.appendChild(item);
    });
  }

  function updateGenerateButton() {
    const hasFile = !!selectedAudioFile;
    const hasKey = geminiApiKeyInput.value.trim().length > 0;
    const hasDuration = audioDurationSeconds > 0;
    generatePromptsBtn.disabled = !(hasFile && hasKey && hasDuration);
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  generatePromptsBtn.addEventListener('click', async () => {
    const apiKey = geminiApiKeyInput.value.trim();
    if (!selectedAudioFile || !apiKey) return;

    generatePromptsBtn.disabled = true;
    generationStatus.style.display = 'block';
    generationStatusText.textContent = 'Preparando audio...';
    generatedPromptsSection.style.display = 'none';

    try {
      const audioBase64 = await fileToBase64(selectedAudioFile);
      const audioMime = selectedAudioFile.type || 'audio/mpeg';

      const numScenes = Math.ceil(audioDurationSeconds / 8);

      generationStatusText.textContent = `Enviando para Gemini (${numScenes} cenas)...`;

      const parts = [];

      if (referenceImages.length > 0) {
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

      const imageContext = referenceImages.length > 0
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

      generationStatusText.textContent = 'Processando com IA (pode levar 1-2 min)...';

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
      generatedPromptsText.value = cleanedText;
      const promptsArray = cleanedText.split(/\n\s*\n/).filter(p => p.trim().length > 0);
      generatedCount.textContent = `${promptsArray.length} prompt${promptsArray.length !== 1 ? 's' : ''} gerado${promptsArray.length !== 1 ? 's' : ''}`;
      generatedPromptsSection.style.display = 'flex';
      generationStatusText.textContent = `${promptsArray.length} prompts gerados com sucesso!`;
      saveGeneratorState();
      setTimeout(() => {
        generationStatus.style.display = 'none';
      }, 3000);
    } catch (error) {
      generationStatusText.textContent = `Erro: ${error.message}`;
      setTimeout(() => {
        generationStatus.style.display = 'none';
      }, 8000);
    } finally {
      generatePromptsBtn.disabled = false;
      updateGenerateButton();
    }
  });

  copyGeneratedBtn.addEventListener('click', () => {
    const text = generatedPromptsText.value;
    navigator.clipboard.writeText(text).then(() => {
      copyGeneratedBtn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <polyline points="20 6 9 17 4 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        Copiado!
      `;
      setTimeout(() => {
        copyGeneratedBtn.innerHTML = `
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="9" y="9" width="13" height="13" rx="2" stroke="currentColor" stroke-width="2"/>
            <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" stroke="currentColor" stroke-width="2"/>
          </svg>
          Copiar
        `;
      }, 2000);
    });
  });

  sendGeneratedToAutomationBtn.addEventListener('click', () => {
    const generatedText = generatedPromptsText.value.trim();
    if (!generatedText) {
      alert('Nenhum prompt gerado para enviar.');
      return;
    }

    const currentText = promptsTextarea.value.trim();
    if (currentText) {
      promptsTextarea.value = currentText + '\n\n' + generatedText;
    } else {
      promptsTextarea.value = generatedText;
    }

    updatePromptCount();
    chrome.storage.local.set({ prompts: promptsTextarea.value });

    tabBtns.forEach(b => b.classList.remove('active'));
    document.querySelector('[data-tab="automation"]').classList.add('active');
    automationTab.classList.add('active');
    generatorTab.classList.remove('active');
    libraryTab.classList.remove('active');
  });

  // Manual paste fallback
  const generatorPaste = document.getElementById('generatorPaste');
  const sendToAutomationBtn = document.getElementById('sendToAutomationBtn');

  sendToAutomationBtn.addEventListener('click', () => {
    const pastedText = generatorPaste.value.trim();
    if (!pastedText) {
      alert('Cole os prompts antes de enviar.');
      return;
    }

    const currentText = promptsTextarea.value.trim();
    if (currentText) {
      promptsTextarea.value = currentText + '\n\n' + pastedText;
    } else {
      promptsTextarea.value = pastedText;
    }

    updatePromptCount();
    chrome.storage.local.set({ prompts: promptsTextarea.value });

    generatorPaste.value = '';

    tabBtns.forEach(b => b.classList.remove('active'));
    document.querySelector('[data-tab="automation"]').classList.add('active');
    automationTab.classList.add('active');
    generatorTab.classList.remove('active');
    libraryTab.classList.remove('active');
  });
  // Load library data on startup
  loadLibraryData();

  // ==================== SESSION / COOKIE MANAGEMENT ====================
  const sessionDot = document.getElementById('sessionDot');
  const sessionTextEl = document.getElementById('sessionText');
  const cookieFileInput = document.getElementById('cookieFileInput');
  const refreshSessionBtn = document.getElementById('refreshSessionBtn');

  // Check session on popup open
  function checkSessionStatus() {
    sessionTextEl.textContent = 'Verificando...';
    sessionDot.className = 'session-dot';
    
    chrome.runtime.sendMessage({ type: 'checkSession' }, (result) => {
      if (chrome.runtime.lastError) {
        sessionTextEl.textContent = 'Erro — recarregue extensao';
        sessionDot.className = 'session-dot expired';
        return;
      }
      
      if (result && result.active) {
        sessionDot.className = 'session-dot active';
        const email = result.email || 'conectado';
        const hours = result.hoursLeft != null ? ` (${result.hoursLeft}h)` : '';
        sessionTextEl.textContent = `✓ ${email}${hours}`;
        sessionTextEl.title = `Expira: ${result.expiresAt}`;
      } else {
        sessionDot.className = 'session-dot expired';
        const reason = result?.reason || 'desconhecido';
        sessionTextEl.textContent = `✗ ${reason}`;
        
        // Try auto-restore
        chrome.runtime.sendMessage({ type: 'restoreCookies' }, (restored) => {
          if (chrome.runtime.lastError) return;
          if (restored && restored.active) {
            sessionDot.className = 'session-dot active';
            const h = restored.hoursLeft != null ? ` (${restored.hoursLeft}h)` : '';
            sessionTextEl.textContent = `✓ ${restored.email || 'restaurado'}${h}`;
          } else {
            sessionTextEl.textContent = '✗ Importe cookies JSON';
          }
        });
      }
    });
  }
  
  checkSessionStatus();

  // Import cookies from JSON file
  cookieFileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const cookies = JSON.parse(event.target.result);
        
        if (!Array.isArray(cookies)) {
          alert('Arquivo invalido. Deve ser um JSON array de cookies (exportado do EditThisCookie).');
          return;
        }
        
        sessionTextEl.textContent = `Importando ${cookies.length} cookies...`;
        sessionDot.className = 'session-dot';
        
        chrome.runtime.sendMessage({ type: 'importCookies', cookies }, (result) => {
          if (chrome.runtime.lastError) {
            sessionTextEl.textContent = 'Erro — recarregue extensao';
            sessionDot.className = 'session-dot expired';
            return;
          }
          
          const total = result.success + result.failed;
          if (result.success > 0) {
            sessionDot.className = 'session-dot active';
            sessionTextEl.textContent = `✓ ${result.success}/${total} cookies OK`;
            
            // Verify session after import
            setTimeout(checkSessionStatus, 1500);
          } else {
            sessionDot.className = 'session-dot expired';
            sessionTextEl.textContent = `✗ Falha total — ${result.errors[0] || 'verifique o JSON'}`;
          }
        });
      } catch (err) {
        alert('Erro ao ler arquivo: ' + err.message);
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  });

  // Refresh session
  refreshSessionBtn.addEventListener('click', () => {
    checkSessionStatus();
  });
});

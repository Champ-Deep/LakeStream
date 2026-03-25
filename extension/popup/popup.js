// LakeStream Chrome Extension — Popup
// Handles configuration, connection status, and scrape triggering.

const $ = (id) => document.getElementById(id);

let sessionCount = 0;

async function init() {
  const config = await chrome.storage.sync.get(['serverUrl', 'apiKey', 'totalSent']);

  if (config.serverUrl && config.apiKey) {
    showConnectedView(config);
    detectPage();
  } else {
    showSetupView(config);
  }
}

function showSetupView(config) {
  $('setup-view').classList.remove('hidden');
  $('connected-view').classList.add('hidden');
  if (config?.serverUrl) $('server-url').value = config.serverUrl;
  if (config?.apiKey) $('api-key').value = config.apiKey;
}

function showConnectedView(config) {
  $('setup-view').classList.add('hidden');
  $('connected-view').classList.remove('hidden');

  // Show shortened URL
  try {
    const url = new URL(config.serverUrl);
    $('connected-url').textContent = url.hostname;
  } catch {
    $('connected-url').textContent = config.serverUrl;
  }

  $('stat-total').textContent = config.totalSent || 0;
  $('stat-session').textContent = sessionCount;
}

async function detectPage() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url) return;

    const url = tab.url;
    let platform = null;

    if (url.includes('linkedin.com/sales')) {
      platform = 'LinkedIn Sales Navigator';
    } else if (url.includes('apollo.io')) {
      platform = 'Apollo.io';
    }

    if (platform) {
      // Ask content script for contact count
      try {
        const response = await chrome.tabs.sendMessage(tab.id, { action: 'getCount' });
        if (response?.count > 0) {
          $('page-info').classList.remove('hidden');
          $('page-platform').textContent = platform;
          $('page-count').textContent = response.count;
          $('scrape-btn').disabled = false;
          $('scrape-btn').textContent = `Scrape ${response.count} Contacts`;
        } else {
          $('page-info').classList.remove('hidden');
          $('page-platform').textContent = platform;
          $('page-count').textContent = '0';
          $('scrape-btn').disabled = true;
          $('scrape-btn').textContent = 'No contacts found on this page';
        }
      } catch {
        // Content script not loaded yet
        $('page-info').classList.remove('hidden');
        $('page-platform').textContent = platform;
        $('page-count').textContent = 'scanning...';
        $('scrape-btn').disabled = true;
      }
    } else {
      $('scrape-btn').disabled = true;
      $('scrape-btn').textContent = 'Navigate to LinkedIn Sales Nav or Apollo';
    }
  } catch {
    // Can't access tab
  }
}

function showStatus(message, type) {
  const el = $('status');
  el.textContent = message;
  el.className = `status status-${type}`;
  el.classList.remove('hidden');
  if (type !== 'info') {
    setTimeout(() => el.classList.add('hidden'), 5000);
  }
}

// --- Event Listeners ---

$('save-btn').addEventListener('click', async () => {
  const serverUrl = $('server-url').value.trim().replace(/\/+$/, '');
  const apiKey = $('api-key').value.trim();

  if (!serverUrl || !apiKey) {
    showStatus('Please fill in both fields', 'error');
    return;
  }

  if (!apiKey.startsWith('ls_')) {
    showStatus('API key should start with "ls_"', 'error');
    return;
  }

  // Test connection
  showStatus('Connecting...', 'info');
  try {
    const res = await fetch(`${serverUrl}/api/auth/api-keys`, {
      headers: { 'X-API-Key': apiKey },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    await chrome.storage.sync.set({ serverUrl, apiKey, totalSent: 0 });
    showStatus('Connected!', 'success');
    const config = await chrome.storage.sync.get(['serverUrl', 'apiKey', 'totalSent']);
    showConnectedView(config);
    detectPage();
  } catch (e) {
    showStatus(`Connection failed: ${e.message}`, 'error');
  }
});

$('scrape-btn').addEventListener('click', async () => {
  $('scrape-btn').disabled = true;
  $('scrape-btn').textContent = 'Scraping...';

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, { action: 'scrape' });

    if (response?.contacts?.length > 0) {
      // Send to background service worker for API call
      const result = await chrome.runtime.sendMessage({
        action: 'ingest',
        contacts: response.contacts,
        domain: response.domain,
        source: response.source,
      });

      if (result?.success) {
        sessionCount += response.contacts.length;
        const config = await chrome.storage.sync.get(['totalSent']);
        const newTotal = (config.totalSent || 0) + response.contacts.length;
        await chrome.storage.sync.set({ totalSent: newTotal });
        $('stat-total').textContent = newTotal;
        $('stat-session').textContent = sessionCount;
        showStatus(`${response.contacts.length} contacts sent to LakeStream`, 'success');
      } else {
        showStatus(result?.error || 'Failed to send data', 'error');
      }
    } else {
      showStatus('No contacts found on this page', 'error');
    }
  } catch (e) {
    showStatus(`Error: ${e.message}`, 'error');
  }

  // Re-detect page state
  detectPage();
});

$('disconnect-btn').addEventListener('click', async () => {
  await chrome.storage.sync.remove(['serverUrl', 'apiKey']);
  sessionCount = 0;
  showSetupView({});
  $('status').classList.add('hidden');
});

// Init
init();

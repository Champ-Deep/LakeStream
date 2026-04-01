// LakeStream Chrome Extension — Popup
// Handles configuration, data preview, pattern cycling, and export.

const $ = (id) => document.getElementById(id);

let sessionCount = 0;
let currentPatterns = [];
let currentPatternIndex = 0;

// --- Init ---

async function init() {
  const config = await chrome.storage.sync.get(['serverUrl', 'apiKey', 'totalSent']);
  const isConnected = config.serverUrl && config.apiKey;

  if (isConnected) {
    showConnectedView(config);
  } else {
    showStandaloneView();
  }
}

// --- Views ---

function showSetupView(config) {
  $('setup-view').classList.remove('hidden');
  $('connected-view').classList.add('hidden');
  $('standalone-view').classList.add('hidden');
  if (config?.serverUrl) $('server-url').value = config.serverUrl;
  if (config?.apiKey) $('api-key').value = config.apiKey;
}

function showConnectedView(config) {
  $('setup-view').classList.add('hidden');
  $('connected-view').classList.remove('hidden');
  $('standalone-view').classList.add('hidden');

  try {
    const url = new URL(config.serverUrl);
    $('connected-url').textContent = url.hostname;
  } catch {
    $('connected-url').textContent = config.serverUrl;
  }

  $('stat-total').textContent = config.totalSent || 0;
  $('stat-session').textContent = sessionCount;

  detectPage(true);
}

async function showStandaloneView() {
  $('setup-view').classList.remove('hidden');
  $('connected-view').classList.add('hidden');
  $('standalone-view').classList.remove('hidden');

  // Still detect data for standalone CSV/copy export
  await detectGenericData(false);
}

// --- Page Detection ---

async function detectPage(isConnected) {
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
      // Show cookie export button on LinkedIn/Apollo pages
      $('export-cookies-btn').style.display = '';

      // Smart extractor path
      try {
        const response = await chrome.tabs.sendMessage(tab.id, { action: 'getCount' });
        $('page-info').classList.remove('hidden');
        $('page-platform').textContent = platform;
        $('data-preview').classList.add('hidden');
        $('no-data').classList.add('hidden');

        if (response?.count > 0) {
          $('page-count').textContent = response.count;
          $('scrape-btn').style.display = '';
          $('scrape-btn').disabled = false;
          $('scrape-btn').textContent = `Scrape ${response.count} Contacts`;
        } else {
          $('page-count').textContent = '0';
          $('scrape-btn').style.display = '';
          $('scrape-btn').disabled = true;
          $('scrape-btn').textContent = 'No contacts found on this page';
        }
      } catch {
        $('page-info').classList.remove('hidden');
        $('page-platform').textContent = platform;
        $('page-count').textContent = 'scanning...';
        $('scrape-btn').style.display = '';
        $('scrape-btn').disabled = true;
      }
    } else {
      // Generic detection path
      $('page-info').classList.add('hidden');
      $('scrape-btn').style.display = 'none';
      await detectGenericData(isConnected);
    }
  } catch {
    // Can't access tab
  }
}

async function detectGenericData(isConnected) {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) return;

    const response = await chrome.tabs.sendMessage(tab.id, { action: 'detectPatterns' });
    currentPatterns = response?.patterns || [];
    currentPatternIndex = 0;

    if (currentPatterns.length > 0) {
      if (isConnected) {
        $('data-preview').classList.remove('hidden');
        $('no-data').classList.add('hidden');
        $('pattern-count').textContent = currentPatterns.length;
        $('send-lakestream').style.display = '';
        $('export-all-csv').style.display = currentPatterns.length > 1 ? '' : 'none';
        renderPreview('preview-thead', 'preview-tbody', 'row-count', 'table-counter',
          'prev-table', 'next-table');
      } else {
        $('standalone-preview').classList.remove('hidden');
        $('standalone-no-data').classList.add('hidden');
        $('standalone-pattern-count').textContent = currentPatterns.length;
        $('standalone-export-all-csv').style.display = currentPatterns.length > 1 ? '' : 'none';
        renderPreview('standalone-preview-thead', 'standalone-preview-tbody',
          'standalone-row-count', 'standalone-table-counter',
          'standalone-prev-table', 'standalone-next-table');
      }
    } else {
      if (isConnected) {
        $('data-preview').classList.add('hidden');
        $('no-data').classList.remove('hidden');
      } else {
        $('standalone-preview').classList.add('hidden');
        $('standalone-no-data').classList.remove('hidden');
      }
    }
  } catch {
    // Content script not available (chrome:// pages, etc.)
    if (isConnected) {
      $('data-preview').classList.add('hidden');
      $('no-data').classList.remove('hidden');
    } else {
      $('standalone-preview').classList.add('hidden');
      $('standalone-no-data').classList.remove('hidden');
    }
  }
}

// --- Preview Rendering ---

function renderPreview(theadId, tbodyId, rowCountId, counterId, prevBtnId, nextBtnId) {
  const pattern = currentPatterns[currentPatternIndex];
  if (!pattern) return;

  const thead = $(theadId);
  const tbody = $(tbodyId);

  // Headers
  thead.innerHTML = '<tr>' +
    pattern.headers.map((h) => `<th>${escapeHtml(h)}</th>`).join('') +
    '</tr>';

  // Rows (show first 50 for performance)
  const displayRows = pattern.rows.slice(0, 50);
  tbody.innerHTML = displayRows
    .map((row) => {
      const cells = pattern.headers
        .map((h) => {
          const val = row[h];
          if (val && typeof val === 'object' && val.link) {
            return `<td><a href="${escapeHtml(val.link)}" title="${escapeHtml(val.link)}">${escapeHtml(val.text)}</a></td>`;
          }
          return `<td title="${escapeHtml(String(val ?? ''))}">${escapeHtml(String(val ?? ''))}</td>`;
        })
        .join('');
      return `<tr>${cells}</tr>`;
    })
    .join('');

  // Row count with truncation notice
  const totalRows = pattern.rows.length;
  $(rowCountId).textContent = totalRows > 50 ? `${totalRows} rows (showing 50)` : `${totalRows} rows`;

  // Navigation
  $(counterId).textContent = `${currentPatternIndex + 1}/${currentPatterns.length}`;
  $(prevBtnId).disabled = currentPatternIndex === 0;
  $(nextBtnId).disabled = currentPatternIndex === currentPatterns.length - 1;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// --- Export ---

async function exportCSV() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, {
      action: 'getCSV',
      patternIndex: currentPatternIndex,
    });

    if (response?.csv) {
      // Use downloads API
      const blob = new Blob([response.csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      chrome.downloads.download({
        url,
        filename: response.filename || 'lakestream-export.csv',
        saveAs: true,
      });
      showStatus('CSV downloaded', 'success');
    }
  } catch (e) {
    showStatus('Export failed: ' + e.message, 'error');
  }
}

async function copyToClipboard() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, {
      action: 'getTSV',
      patternIndex: currentPatternIndex,
    });

    if (response?.tsv) {
      await navigator.clipboard.writeText(response.tsv);
      showStatus('Copied to clipboard — paste into any spreadsheet', 'success');
    }
  } catch (e) {
    showStatus('Copy failed: ' + e.message, 'error');
  }
}

async function sendToLakeStream() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, {
      action: 'scrape',
      patternIndex: currentPatternIndex,
    });

    if (response?.contacts?.length > 0) {
      const result = await chrome.runtime.sendMessage({
        action: 'ingest',
        contacts: response.contacts,
        domain: response.domain,
        source: response.source,
        dataType: response.dataType,
      });

      if (result?.success) {
        sessionCount += response.contacts.length;
        const config = await chrome.storage.sync.get(['totalSent']);
        const newTotal = (config.totalSent || 0) + response.contacts.length;
        await chrome.storage.sync.set({ totalSent: newTotal });
        $('stat-total').textContent = newTotal;
        $('stat-session').textContent = sessionCount;
        showStatus(`${response.contacts.length} records sent to LakeStream`, 'success');
      } else {
        showStatus(result?.error || 'Failed to send data', 'error');
      }
    } else {
      showStatus('No data to send', 'error');
    }
  } catch (e) {
    showStatus('Error: ' + e.message, 'error');
  }
}

function showStatus(message, type) {
  const el = $('status');
  el.textContent = message;
  el.className = `status status-${type}`;
  el.classList.remove('hidden');
  if (type !== 'info') {
    setTimeout(() => el.classList.add('hidden'), 4000);
  }
}

// --- Navigation (Try Another Table) ---

function navigateTable(delta, theadId, tbodyId, rowCountId, counterId, prevBtnId, nextBtnId) {
  const newIndex = currentPatternIndex + delta;
  if (newIndex < 0 || newIndex >= currentPatterns.length) return;
  currentPatternIndex = newIndex;
  renderPreview(theadId, tbodyId, rowCountId, counterId, prevBtnId, nextBtnId);
}

// --- Event Listeners ---

// Save & Connect
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
  } catch (e) {
    showStatus(`Connection failed: ${e.message}`, 'error');
  }
});

// Setup button (from standalone view)
$('setup-btn').addEventListener('click', () => {
  $('standalone-view').classList.add('hidden');
  showSetupView({});
});

// Smart extractor scrape button
$('scrape-btn').addEventListener('click', async () => {
  $('scrape-btn').disabled = true;
  $('scrape-btn').textContent = 'Scraping...';

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, { action: 'scrape' });

    if (response?.contacts?.length > 0) {
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

  detectPage(true);
});

// Cookie export for server-side auth
$('export-cookies-btn').addEventListener('click', async () => {
  const btn = $('export-cookies-btn');
  btn.disabled = true;
  btn.textContent = 'Sending cookies...';

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url) {
      showStatus('Cannot access current tab', 'error');
      btn.disabled = false;
      btn.textContent = '🔑 Setup Server Auth (send cookies)';
      return;
    }

    // Determine domain from current tab
    let domain = null;
    if (tab.url.includes('linkedin.com')) domain = '.linkedin.com';
    else if (tab.url.includes('apollo.io')) domain = '.apollo.io';

    if (!domain) {
      showStatus('Navigate to LinkedIn or Apollo first', 'error');
      btn.disabled = false;
      btn.textContent = '🔑 Setup Server Auth (send cookies)';
      return;
    }

    const result = await chrome.runtime.sendMessage({
      action: 'exportCookiesForServer',
      domain,
    });

    if (result?.success) {
      showStatus('Cookies sent — server-side scraping is ready!', 'success');
    } else {
      showStatus(result?.error || 'Failed to export cookies', 'error');
    }
  } catch (e) {
    showStatus('Error: ' + e.message, 'error');
  }

  btn.disabled = false;
  btn.textContent = '🔑 Setup Server Auth (send cookies)';
});

// Disconnect
$('disconnect-btn').addEventListener('click', async () => {
  await chrome.storage.sync.remove(['serverUrl', 'apiKey']);
  sessionCount = 0;
  currentPatterns = [];
  currentPatternIndex = 0;
  showSetupView({});
  $('status').classList.add('hidden');
});

// Table navigation — connected view
$('prev-table').addEventListener('click', () => {
  navigateTable(-1, 'preview-thead', 'preview-tbody', 'row-count', 'table-counter',
    'prev-table', 'next-table');
});
$('next-table').addEventListener('click', () => {
  navigateTable(1, 'preview-thead', 'preview-tbody', 'row-count', 'table-counter',
    'prev-table', 'next-table');
});

// Table navigation — standalone view
$('standalone-prev-table').addEventListener('click', () => {
  navigateTable(-1, 'standalone-preview-thead', 'standalone-preview-tbody',
    'standalone-row-count', 'standalone-table-counter',
    'standalone-prev-table', 'standalone-next-table');
});
$('standalone-next-table').addEventListener('click', () => {
  navigateTable(1, 'standalone-preview-thead', 'standalone-preview-tbody',
    'standalone-row-count', 'standalone-table-counter',
    'standalone-prev-table', 'standalone-next-table');
});

// Export All CSV
async function exportAllCSV() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const response = await chrome.tabs.sendMessage(tab.id, { action: 'getAllCSV' });
    if (response?.csv) {
      const blob = new Blob([response.csv], { type: 'text/csv' });
      const url = URL.createObjectURL(blob);
      chrome.downloads.download({
        url,
        filename: response.filename || 'lakestream-export-all.csv',
        saveAs: true,
      });
      showStatus('All tables exported', 'success');
    }
  } catch (e) {
    showStatus('Export failed: ' + e.message, 'error');
  }
}

// Export — connected view
$('export-csv').addEventListener('click', exportCSV);
$('export-copy').addEventListener('click', copyToClipboard);
$('send-lakestream').addEventListener('click', sendToLakeStream);
$('export-all-csv').addEventListener('click', exportAllCSV);

// Export — standalone view
$('standalone-export-csv').addEventListener('click', exportCSV);
$('standalone-export-copy').addEventListener('click', copyToClipboard);
$('standalone-export-all-csv').addEventListener('click', exportAllCSV);

// Init
init();

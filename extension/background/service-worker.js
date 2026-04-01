// LakeStream Chrome Extension — Background Service Worker
// Handles API communication with LakeStream server.

const MAX_RETRIES = 2;
const RETRY_DELAY_MS = 2000;

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'ingest') {
    handleIngest(message).then(sendResponse);
    return true; // Keep channel open for async response
  }
  if (message.action === 'exportCookiesForServer') {
    handleCookieExport(message).then(sendResponse);
    return true;
  }
  if (message.action === 'multiPageComplete') {
    // Update badge with total count from multi-page extraction
    chrome.action.setBadgeText({ text: String(message.count) });
    chrome.action.setBadgeBackgroundColor({
      color: message.success ? '#16a34a' : '#ef4444',
    });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 5000);
    return false;
  }
});

async function handleIngest({ contacts, domain, source, dataType }, retryCount = 0) {
  try {
    const config = await chrome.storage.sync.get(['serverUrl', 'apiKey']);
    if (!config.serverUrl || !config.apiKey) {
      return { success: false, error: 'Not configured — open extension popup' };
    }

    // Determine data type — smart extractors send contacts, auto-detect sends table_data
    const isGeneric = source === 'auto_detect';
    const recordDataType = isGeneric ? (dataType || 'table_data') : 'contact';

    // Transform data into LakeStream ingest format
    const records = contacts.map((c) => {
      if (isGeneric) {
        // Generic data — pass all fields as metadata
        return {
          data_type: recordDataType,
          url: null,
          title: Object.values(c).filter(Boolean).slice(0, 2).join(' — ') || null,
          metadata: { ...c, source: 'chrome_extension_auto_detect' },
        };
      }

      // Smart extractor — structured contact format
      return {
        data_type: 'contact',
        url: c.linkedin_url || c.profile_url || null,
        title: [c.first_name, c.last_name].filter(Boolean).join(' ') || c.name || null,
        metadata: {
          first_name: c.first_name || null,
          last_name: c.last_name || null,
          job_title: c.job_title || null,
          email: c.email || null,
          phone: c.phone || null,
          linkedin_url: c.linkedin_url || c.profile_url || null,
          company: c.company || null,
          location: c.location || null,
          company_size: c.company_size || null,
          industry: c.industry || null,
          headline: c.headline || null,
          source: source || 'chrome_extension',
        },
      };
    });

    const res = await fetch(`${config.serverUrl}/api/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify({
        domain: domain || 'unknown',
        source: source || 'chrome_extension',
        records,
      }),
    });

    if (!res.ok) {
      const text = await res.text();

      // Retry on server errors (5xx) or rate limits (429)
      if ((res.status >= 500 || res.status === 429) && retryCount < MAX_RETRIES) {
        await new Promise((r) => setTimeout(r, RETRY_DELAY_MS * (retryCount + 1)));
        return handleIngest({ contacts, domain, source, dataType }, retryCount + 1);
      }

      return { success: false, error: `Server error ${res.status}: ${text}` };
    }

    const data = await res.json();

    // Update badge
    chrome.action.setBadgeText({ text: String(data.records_ingested) });
    chrome.action.setBadgeBackgroundColor({ color: '#16a34a' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 3000);

    return { success: true, ...data };
  } catch (e) {
    // Retry on network errors
    if (retryCount < MAX_RETRIES) {
      await new Promise((r) => setTimeout(r, RETRY_DELAY_MS * (retryCount + 1)));
      return handleIngest({ contacts, domain, source, dataType }, retryCount + 1);
    }
    return { success: false, error: e.message };
  }
}

async function handleCookieExport({ domain }) {
  try {
    const config = await chrome.storage.sync.get(['serverUrl', 'apiKey']);
    if (!config.serverUrl || !config.apiKey) {
      return { success: false, error: 'Not configured' };
    }

    // Get all cookies for the domain
    const cookies = await chrome.cookies.getAll({ domain });

    // Send cookies to server for session creation
    const res = await fetch(`${config.serverUrl}/api/auth/session-cookies`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify({ domain, cookies }),
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: `Server error ${res.status}: ${text}` };
    }

    const data = await res.json();
    return { success: true, ...data };
  } catch (e) {
    return { success: false, error: e.message };
  }
}

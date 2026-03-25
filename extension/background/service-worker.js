// LakeStream Chrome Extension — Background Service Worker
// Handles API communication with LakeStream server.

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.action === 'ingest') {
    handleIngest(message).then(sendResponse);
    return true; // Keep channel open for async response
  }
});

async function handleIngest({ contacts, domain, source }) {
  try {
    const config = await chrome.storage.sync.get(['serverUrl', 'apiKey']);
    if (!config.serverUrl || !config.apiKey) {
      return { success: false, error: 'Not configured — open extension popup' };
    }

    // Transform contacts into LakeStream ingest format
    const records = contacts.map((c) => ({
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
        source: source || 'chrome_extension',
      },
    }));

    const res = await fetch(`${config.serverUrl}/api/ingest`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': config.apiKey,
      },
      body: JSON.stringify({
        domain: domain || 'linkedin.com',
        source: source || 'chrome_extension',
        records,
      }),
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: `Server error ${res.status}: ${text}` };
    }

    const data = await res.json();

    // Update badge
    chrome.action.setBadgeText({ text: String(data.records_ingested) });
    chrome.action.setBadgeBackgroundColor({ color: '#16a34a' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 3000);

    return { success: true, ...data };
  } catch (e) {
    return { success: false, error: e.message };
  }
}

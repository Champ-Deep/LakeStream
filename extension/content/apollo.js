// LakeStream — Apollo.io Content Script
// Extracts contacts from people search results and contact pages.

(() => {
  'use strict';

  // --- Selectors (Apollo.io DOM structure) ---
  // Apollo uses a React-based SPA with data tables for search results.

  const SELECTORS = {
    // People search results table
    tableRows: [
      'tr.zp_cWbgJ',
      'table tbody tr',
      '[data-cy="contacts-table"] tbody tr',
    ],
    nameCell: [
      'td:first-child a',
      '.zp_xVJ20 a',
      'a[href*="/contacts/"]',
    ],
    titleCell: [
      'td:nth-child(3)',
      '.zp_Y6y8d',
    ],
    companyCell: [
      'td:nth-child(4) a',
      'a[href*="/accounts/"]',
    ],
    emailCell: [
      'td a[href^="mailto:"]',
      '.zp_RFed0 a[href^="mailto:"]',
    ],
    phoneCell: [
      'td a[href^="tel:"]',
    ],
    locationCell: [
      'td:nth-child(6)',
      '.zp_Y6y8d:last-child',
    ],
    linkedinLink: [
      'a[href*="linkedin.com/in/"]',
    ],
  };

  function query(parent, selectorList) {
    for (const sel of selectorList) {
      const el = parent.querySelector(sel);
      if (el) return el;
    }
    return null;
  }

  function queryAll(parent, selectorList) {
    for (const sel of selectorList) {
      const els = parent.querySelectorAll(sel);
      if (els.length > 0) return Array.from(els);
    }
    return [];
  }

  function cleanText(el) {
    return el?.textContent?.trim().replace(/\s+/g, ' ') || '';
  }

  function splitName(fullName) {
    const parts = fullName.split(/\s+/);
    if (parts.length === 0) return { first_name: '', last_name: '' };
    if (parts.length === 1) return { first_name: parts[0], last_name: '' };
    return {
      first_name: parts[0],
      last_name: parts.slice(1).join(' '),
    };
  }

  // --- Extraction ---

  function extractFromSearchTable() {
    const rows = queryAll(document, SELECTORS.tableRows);
    const contacts = [];

    for (const row of rows) {
      const nameEl = query(row, SELECTORS.nameCell);
      const fullName = cleanText(nameEl);
      if (!fullName) continue;

      const { first_name, last_name } = splitName(fullName);
      const titleEl = query(row, SELECTORS.titleCell);
      const companyEl = query(row, SELECTORS.companyCell);
      const emailEl = query(row, SELECTORS.emailCell);
      const phoneEl = query(row, SELECTORS.phoneCell);
      const locationEl = query(row, SELECTORS.locationCell);
      const linkedinEl = query(row, SELECTORS.linkedinLink);

      // Extract email from mailto: link or cell text
      let email = null;
      if (emailEl?.href?.startsWith('mailto:')) {
        email = emailEl.href.replace('mailto:', '');
      } else if (emailEl) {
        const text = cleanText(emailEl);
        if (text.includes('@')) email = text;
      }

      // Extract phone from tel: link
      let phone = null;
      if (phoneEl?.href?.startsWith('tel:')) {
        phone = phoneEl.href.replace('tel:', '');
      } else if (phoneEl) {
        phone = cleanText(phoneEl);
      }

      const profileUrl = nameEl?.href || null;
      const linkedinUrl = linkedinEl?.href || null;

      contacts.push({
        first_name,
        last_name,
        name: fullName,
        job_title: cleanText(titleEl),
        company: cleanText(companyEl),
        email,
        phone,
        location: cleanText(locationEl),
        linkedin_url: linkedinUrl,
        profile_url: profileUrl,
      });
    }

    return contacts;
  }

  function extractContacts() {
    return extractFromSearchTable();
  }

  // --- Multi-Page Extraction ---

  let multiPageRunning = false;
  let multiPageCancelled = false;

  function getNextPageButton() {
    const selectors = [
      'button[aria-label="Next"]',
      'button[aria-label="next"]',
      '.zp_bWS5y:last-child button',
      'button.pagination-next',
    ];
    for (const sel of selectors) {
      const btn = document.querySelector(sel);
      if (btn && !btn.disabled) return btn;
    }
    return null;
  }

  async function extractAllPages(maxPages = 30) {
    multiPageRunning = true;
    multiPageCancelled = false;
    const allContacts = [];
    const seenNames = new Set();

    for (let page = 1; page <= maxPages; page++) {
      if (multiPageCancelled) break;

      updateFABProgress(page, allContacts.length);

      // Wait for table to load
      await new Promise((r) => setTimeout(r, 1500));

      const contacts = extractContacts();
      for (const c of contacts) {
        const key = c.email || c.profile_url || c.name;
        if (!seenNames.has(key)) {
          seenNames.add(key);
          allContacts.push(c);
        }
      }

      if (page < maxPages) {
        const nextBtn = getNextPageButton();
        if (!nextBtn) break;
        nextBtn.click();
        // Apollo is less aggressive — 1-2s delay
        await new Promise((r) => setTimeout(r, 1000 + Math.random() * 1500));
      }
    }

    multiPageRunning = false;
    return allContacts;
  }

  function updateFABProgress(page, total) {
    const textEl = document.getElementById('lakestream-fab-text');
    if (textEl) {
      textEl.textContent = `Page ${page}... (${total} contacts)`;
    }
  }

  // --- Floating Action Button ---

  function createFAB() {
    if (document.getElementById('lakestream-fab')) return;

    const fab = document.createElement('div');
    fab.id = 'lakestream-fab';
    fab.innerHTML = `
      <div style="position: fixed; bottom: 24px; right: 24px; z-index: 99999; display: flex; flex-direction: column; gap: 8px; align-items: flex-end;">
        <button id="lakestream-all-pages-btn" style="
          background: linear-gradient(135deg, #7c3aed, #6d28d9);
          color: white; border: none; border-radius: 50px;
          padding: 10px 16px; font-size: 12px; font-weight: 600;
          cursor: pointer; box-shadow: 0 4px 16px rgba(124,58,237,0.4);
          display: flex; align-items: center; gap: 6px;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          transition: all 0.2s;
        ">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
          </svg>
          <span id="lakestream-all-text">Extract All Pages</span>
        </button>
        <button id="lakestream-scrape-btn" style="
          background: linear-gradient(135deg, #ef4444, #dc2626);
          color: white; border: none; border-radius: 50px;
          padding: 12px 20px; font-size: 13px; font-weight: 600;
          cursor: pointer; box-shadow: 0 4px 16px rgba(239,68,68,0.4);
          display: flex; align-items: center; gap: 8px;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          transition: all 0.2s;
        ">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
          <span id="lakestream-fab-text">Scrape with LakeStream</span>
        </button>
      </div>
    `;
    document.body.appendChild(fab);

    const btn = document.getElementById('lakestream-scrape-btn');
    btn.addEventListener('mouseenter', () => {
      btn.style.transform = 'scale(1.05)';
      btn.style.boxShadow = '0 6px 24px rgba(239,68,68,0.5)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.transform = 'scale(1)';
      btn.style.boxShadow = '0 4px 16px rgba(239,68,68,0.4)';
    });

    // Single page scrape
    btn.addEventListener('click', async () => {
      const textEl = document.getElementById('lakestream-fab-text');
      const contacts = extractContacts();

      if (contacts.length === 0) {
        textEl.textContent = 'No contacts found';
        setTimeout(() => { textEl.textContent = 'Scrape with LakeStream'; }, 2000);
        return;
      }

      textEl.textContent = `Sending ${contacts.length}...`;
      btn.disabled = true;

      try {
        const result = await chrome.runtime.sendMessage({
          action: 'ingest',
          contacts,
          domain: 'apollo.io',
          source: 'apollo',
        });

        if (result?.success) {
          textEl.textContent = `${contacts.length} contacts sent!`;
          btn.style.background = 'linear-gradient(135deg, #16a34a, #15803d)';
        } else {
          textEl.textContent = result?.error || 'Failed';
          btn.style.background = 'linear-gradient(135deg, #f59e0b, #d97706)';
        }
      } catch (e) {
        textEl.textContent = 'Error: ' + e.message;
      }

      btn.disabled = false;
      setTimeout(() => {
        textEl.textContent = 'Scrape with LakeStream';
        btn.style.background = 'linear-gradient(135deg, #ef4444, #dc2626)';
      }, 3000);
    });

    // Multi-page "Extract All Pages" button
    const allBtn = document.getElementById('lakestream-all-pages-btn');
    const allText = document.getElementById('lakestream-all-text');

    allBtn.addEventListener('click', async () => {
      if (multiPageRunning) {
        multiPageCancelled = true;
        allText.textContent = 'Stopping...';
        return;
      }

      allBtn.disabled = true;
      allText.textContent = 'Extracting...';

      try {
        const contacts = await extractAllPages(30);

        if (contacts.length === 0) {
          allText.textContent = 'No contacts found';
        } else {
          allText.textContent = `Sending ${contacts.length}...`;

          const result = await chrome.runtime.sendMessage({
            action: 'ingest',
            contacts,
            domain: 'apollo.io',
            source: 'apollo',
          });

          if (result?.success) {
            allText.textContent = `${contacts.length} contacts sent!`;
            allBtn.style.background = 'linear-gradient(135deg, #16a34a, #15803d)';
          } else {
            allText.textContent = result?.error || 'Failed';
          }
        }
      } catch (e) {
        allText.textContent = 'Error: ' + e.message;
      }

      allBtn.disabled = false;
      setTimeout(() => {
        allText.textContent = 'Extract All Pages';
        allBtn.style.background = 'linear-gradient(135deg, #7c3aed, #6d28d9)';
      }, 3000);
    });

    // Update contact count
    const contacts = extractContacts();
    if (contacts.length > 0) {
      document.getElementById('lakestream-fab-text').textContent =
        `Scrape ${contacts.length} contacts`;
    }
  }

  // --- Message Handler (from popup) ---

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'getCount') {
      const contacts = extractContacts();
      sendResponse({ count: contacts.length });
    } else if (message.action === 'scrape') {
      const contacts = extractContacts();
      sendResponse({
        contacts,
        domain: 'apollo.io',
        source: 'apollo',
      });
    } else if (message.action === 'extractAllPages') {
      extractAllPages(message.maxPages || 30).then((contacts) => {
        chrome.runtime.sendMessage({
          action: 'ingest',
          contacts,
          domain: 'apollo.io',
          source: 'apollo',
        }).then((result) => {
          chrome.runtime.sendMessage({
            action: 'multiPageComplete',
            count: contacts.length,
            success: result?.success || false,
          });
        });
      });
      sendResponse({ started: true });
    }
    return true;
  });

  // --- Init ---

  function initWhenReady() {
    const contacts = extractContacts();
    if (contacts.length > 0 || document.readyState === 'complete') {
      createFAB();
    } else {
      setTimeout(initWhenReady, 1500);
    }
  }

  chrome.storage.sync.get(['serverUrl', 'apiKey'], (config) => {
    if (config.serverUrl && config.apiKey) {
      initWhenReady();
    }
  });

  // Re-init on SPA navigation
  let lastUrl = window.location.href;
  const observer = new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      setTimeout(initWhenReady, 2000);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();

// LakeStream — LinkedIn Sales Navigator Content Script
// Extracts contacts from search results and profile pages.

(() => {
  'use strict';

  // --- Selectors (Sales Navigator DOM structure) ---
  // These target the standard Sales Nav layout. LinkedIn updates DOM periodically,
  // so selectors may need maintenance.

  const SELECTORS = {
    // Search results page (/sales/search/people)
    searchResultCards: [
      'li.artdeco-list__item',
      '[data-view-name="search-results-lead-card"]',
      '.search-results__result-item',
    ],
    nameLink: [
      'a[data-control-name="view_lead_panel_via_search_lead_name"]',
      '.result-lockup__name a',
      'span.entity-result__title-text a',
      '.artdeco-entity-lockup__title a',
    ],
    title: [
      '.result-lockup__highlight-keyword',
      '.artdeco-entity-lockup__subtitle',
      'span.entity-result__primary-subtitle',
    ],
    company: [
      '.result-lockup__position-company a',
      '.artdeco-entity-lockup__caption a',
      'a[data-control-name="view_lead_panel_via_search_lead_company_name"]',
    ],
    location: [
      '.result-lockup__misc-item',
      '.artdeco-entity-lockup__metadata',
      'span.entity-result__secondary-subtitle',
    ],
    connectionDegree: [
      '.result-lockup__badge',
      '.artdeco-entity-lockup__degree',
    ],

    // Profile page (/sales/lead/...)
    profileName: [
      '.profile-topcard-person-entity__name',
      'h1.inline.t-24',
      '.top-card-layout__title',
    ],
    profileTitle: [
      '.profile-topcard__summary-position',
      '.profile-topcard-person-entity__title',
    ],
    profileCompany: [
      '.profile-topcard__summary-position-company',
      '.profile-topcard-person-entity__company a',
    ],
    profileLocation: [
      '.profile-topcard__location-data',
      '.profile-topcard-person-entity__location',
    ],
  };

  /**
   * Try multiple selectors until one matches.
   */
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

  /**
   * Split a full name into first/last.
   */
  function splitName(fullName) {
    const parts = fullName.split(/\s+/);
    if (parts.length === 0) return { first_name: '', last_name: '' };
    if (parts.length === 1) return { first_name: parts[0], last_name: '' };
    return {
      first_name: parts[0],
      last_name: parts.slice(1).join(' '),
    };
  }

  /**
   * Extract LinkedIn profile URL from an anchor, normalizing it.
   */
  function extractProfileUrl(anchor) {
    if (!anchor?.href) return null;
    try {
      const url = new URL(anchor.href);
      // Sales Nav uses /sales/lead/ID — convert to standard /in/slug if possible
      // But also keep the Sales Nav URL as it's still a valid identifier
      return url.origin + url.pathname;
    } catch {
      return null;
    }
  }

  // --- Page Type Detection ---

  function isSearchPage() {
    return window.location.pathname.includes('/sales/search/people');
  }

  function isProfilePage() {
    return window.location.pathname.includes('/sales/lead/');
  }

  // --- Extraction ---

  function extractFromSearchResults() {
    const cards = queryAll(document, SELECTORS.searchResultCards);
    const contacts = [];

    for (const card of cards) {
      const nameEl = query(card, SELECTORS.nameLink);
      const titleEl = query(card, SELECTORS.title);
      const companyEl = query(card, SELECTORS.company);
      const locationEl = query(card, SELECTORS.location);

      const fullName = cleanText(nameEl);
      if (!fullName) continue; // Skip empty cards

      const { first_name, last_name } = splitName(fullName);
      const profileUrl = extractProfileUrl(nameEl);

      contacts.push({
        first_name,
        last_name,
        name: fullName,
        job_title: cleanText(titleEl),
        company: cleanText(companyEl),
        location: cleanText(locationEl),
        linkedin_url: profileUrl,
      });
    }

    return contacts;
  }

  function extractFromProfile() {
    const nameEl = query(document, SELECTORS.profileName);
    const titleEl = query(document, SELECTORS.profileTitle);
    const companyEl = query(document, SELECTORS.profileCompany);
    const locationEl = query(document, SELECTORS.profileLocation);

    const fullName = cleanText(nameEl);
    if (!fullName) return [];

    const { first_name, last_name } = splitName(fullName);

    return [{
      first_name,
      last_name,
      name: fullName,
      job_title: cleanText(titleEl),
      company: cleanText(companyEl),
      location: cleanText(locationEl),
      linkedin_url: window.location.origin + window.location.pathname,
    }];
  }

  function extractContacts() {
    if (isSearchPage()) return extractFromSearchResults();
    if (isProfilePage()) return extractFromProfile();
    return [];
  }

  // --- Floating Action Button ---

  function createFAB() {
    if (document.getElementById('lakestream-fab')) return;

    const fab = document.createElement('div');
    fab.id = 'lakestream-fab';
    fab.innerHTML = `
      <button id="lakestream-scrape-btn" style="
        position: fixed; bottom: 24px; right: 24px; z-index: 99999;
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
          domain: 'linkedin.com',
          source: 'linkedin_sales_nav',
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

    // Update contact count on FAB
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
        domain: 'linkedin.com',
        source: 'linkedin_sales_nav',
      });
    }
    return true;
  });

  // --- Init ---

  // Wait for DOM to stabilize (Sales Nav is an SPA, content loads async)
  function initWhenReady() {
    const contacts = extractContacts();
    if (contacts.length > 0 || document.readyState === 'complete') {
      createFAB();
    } else {
      setTimeout(initWhenReady, 1500);
    }
  }

  // Check if extension is configured before showing FAB
  chrome.storage.sync.get(['serverUrl', 'apiKey'], (config) => {
    if (config.serverUrl && config.apiKey) {
      initWhenReady();
    }
  });

  // Re-init on SPA navigation (Sales Nav uses pushState)
  let lastUrl = window.location.href;
  const observer = new MutationObserver(() => {
    if (window.location.href !== lastUrl) {
      lastUrl = window.location.href;
      setTimeout(initWhenReady, 2000);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
})();

// LakeStream — Generic Data Auto-Detector
// Detects repeating HTML patterns (tables, lists, card grids) on any page.
// Benchmark: Instant Data Scraper's one-click pattern detection.

(() => {
  'use strict';

  // Skip pages where smart extractors are active
  const host = window.location.hostname;
  if (host === 'www.linkedin.com' && window.location.pathname.startsWith('/sales/')) return;
  if (host === 'www.apollo.io' || host === 'app.apollo.io') return;

  // --- Cached patterns (avoids stale index bugs on re-detection) ---
  let cachedPatterns = null;

  function getCachedPatterns() {
    if (!cachedPatterns) cachedPatterns = detectAllPatterns();
    return cachedPatterns;
  }

  // --- Pattern Detection ---

  function getSignature(el) {
    const classes = Array.from(el.classList).sort().join('.');
    return classes ? `${el.tagName}.${classes}` : el.tagName;
  }

  function extractColumns(el) {
    const cols = [];
    const children = Array.from(el.children);
    if (children.length > 0) {
      for (const child of children) {
        const text = child.textContent?.trim().replace(/\s+/g, ' ') || '';
        const link = child.querySelector('a')?.href || null;
        const img = child.querySelector('img')?.src || null;
        cols.push({ text, link, img });
      }
    } else {
      const text = el.textContent?.trim().replace(/\s+/g, ' ') || '';
      cols.push({ text, link: null, img: null });
    }
    return cols;
  }

  /**
   * Deduplicate header names — append (2), (3) for duplicates.
   */
  function deduplicateHeaders(headers) {
    const counts = {};
    return headers.map((h) => {
      const name = h || 'Column';
      counts[name] = (counts[name] || 0) + 1;
      return counts[name] > 1 ? `${name} (${counts[name]})` : name;
    });
  }

  /**
   * Extract rows from an HTML <table>, handling multiple <tbody> elements.
   */
  function extractTable(table) {
    let headers = [];
    const rows = [];

    // Headers from <thead> or first <tr>
    const thead = table.querySelector('thead');
    const headerRow = thead
      ? thead.querySelector('tr')
      : table.querySelector('tr');

    if (headerRow) {
      const cells = headerRow.querySelectorAll('th, td');
      for (const cell of cells) {
        headers.push(cell.textContent?.trim().replace(/\s+/g, ' ') || '');
      }
    }

    headers = deduplicateHeaders(headers);

    // Data rows — collect from ALL <tbody> elements
    const tbodies = table.querySelectorAll('tbody');
    const rowSources = tbodies.length > 0 ? Array.from(tbodies) : [table];
    const skipFirst = !thead && rowSources[0] === table;

    for (const source of rowSources) {
      const trs = source.querySelectorAll(':scope > tr');
      const startIdx = (skipFirst && source === rowSources[0]) ? 1 : 0;

      for (let i = startIdx; i < trs.length; i++) {
        const tr = trs[i];
        const cells = tr.querySelectorAll('td, th');
        if (cells.length === 0) continue;

        const row = {};
        for (let j = 0; j < cells.length; j++) {
          const header = headers[j] || `Column ${j + 1}`;
          const cell = cells[j];
          const link = cell.querySelector('a')?.href || null;
          const text = cell.textContent?.trim().replace(/\s+/g, ' ') || '';
          row[header] = link && text ? { text, link } : text;
        }
        rows.push(row);
      }
    }

    return { headers, rows };
  }

  function detectTables() {
    const patterns = [];
    const tables = document.querySelectorAll('table');

    for (const table of tables) {
      if (table.offsetParent === null) continue;
      const { headers, rows } = extractTable(table);
      if (rows.length < 2) continue;

      const colConsistency = rows.every(
        (r) => Object.keys(r).length === headers.length
      ) ? 1.5 : 1;
      const score = rows.length * 10 * colConsistency;

      patterns.push({
        type: 'table',
        headers: headers.length > 0 ? headers : Object.keys(rows[0] || {}),
        rows,
        selector: buildSelector(table),
        score,
        element: table,
      });
    }

    return patterns;
  }

  function detectRepeatingElements() {
    const patterns = [];
    const seen = new WeakSet();
    const allElements = document.querySelectorAll('body *');

    for (const parent of allElements) {
      if (seen.has(parent)) continue;
      if (parent.children.length < 3) continue;
      if (['SCRIPT', 'STYLE', 'SVG', 'NAV', 'HEADER', 'FOOTER', 'HEAD'].includes(parent.tagName))
        continue;
      if (parent.offsetParent === null && parent.tagName !== 'BODY') continue;

      const groups = {};
      for (const child of parent.children) {
        if (child.offsetParent === null) continue;
        const sig = getSignature(child);
        if (!groups[sig]) groups[sig] = [];
        groups[sig].push(child);
      }

      for (const [sig, elements] of Object.entries(groups)) {
        if (elements.length < 3) continue;
        const avgChildren = elements.reduce((s, el) => s + el.children.length, 0) / elements.length;
        if (avgChildren < 1) continue;

        const sampleCols = extractColumns(elements[0]);
        let headers = sampleCols.map((_, i) => `Column ${i + 1}`);

        const firstEl = elements[0];
        for (let i = 0; i < firstEl.children.length && i < headers.length; i++) {
          const child = firstEl.children[i];
          const label =
            child.getAttribute('aria-label') ||
            child.getAttribute('data-label') ||
            child.querySelector('label')?.textContent?.trim();
          if (label) headers[i] = label;
        }

        headers = deduplicateHeaders(headers);

        const rows = [];
        for (const el of elements) {
          const cols = extractColumns(el);
          const row = {};
          for (let i = 0; i < cols.length; i++) {
            const header = headers[i] || `Column ${i + 1}`;
            const col = cols[i];
            row[header] = col.link && col.text ? { text: col.text, link: col.link } : col.text;
          }
          rows.push(row);
        }

        const nonEmptyRows = rows.filter((r) =>
          Object.values(r).some((v) => {
            const text = typeof v === 'object' ? v?.text : v;
            return text && text.length > 0;
          })
        );
        if (nonEmptyRows.length < 3) continue;

        const colCounts = nonEmptyRows.map((r) => Object.keys(r).length);
        const consistent = colCounts.every((c) => c === colCounts[0]) ? 1.3 : 1;
        const score = nonEmptyRows.length * 5 * consistent;

        seen.add(parent);

        patterns.push({
          type: 'list',
          headers,
          rows: nonEmptyRows,
          selector: buildSelector(parent),
          score,
          element: parent,
        });
      }
    }

    return patterns;
  }

  function detectDefinitionLists() {
    const patterns = [];
    const dls = document.querySelectorAll('dl');

    for (const dl of dls) {
      if (dl.offsetParent === null) continue;
      const dts = dl.querySelectorAll('dt');
      const dds = dl.querySelectorAll('dd');
      if (dts.length < 2 || dts.length !== dds.length) continue;

      const rows = [];
      for (let i = 0; i < dts.length; i++) {
        const key = dts[i].textContent?.trim().replace(/\s+/g, ' ') || '';
        const val = dds[i].textContent?.trim().replace(/\s+/g, ' ') || '';
        const link = dds[i].querySelector('a')?.href || null;
        rows.push({
          Term: key,
          Description: link ? { text: val, link } : val,
        });
      }

      if (rows.length < 2) continue;

      patterns.push({
        type: 'definition_list',
        headers: ['Term', 'Description'],
        rows,
        selector: buildSelector(dl),
        score: rows.length * 7,
        element: dl,
      });
    }

    return patterns;
  }

  function detectAriaGrids() {
    const patterns = [];
    const grids = document.querySelectorAll('[role="grid"], [role="table"]');

    for (const grid of grids) {
      if (grid.offsetParent === null) continue;
      if (grid.tagName === 'TABLE') continue;

      const rowEls = grid.querySelectorAll('[role="row"]');
      if (rowEls.length < 3) continue;

      const headerCells = rowEls[0].querySelectorAll(
        '[role="columnheader"], [role="gridcell"], [role="cell"]'
      );
      let headers = Array.from(headerCells).map(
        (c) => c.textContent?.trim().replace(/\s+/g, ' ') || ''
      );
      headers = deduplicateHeaders(headers);

      const rows = [];
      for (let i = 1; i < rowEls.length; i++) {
        const cells = rowEls[i].querySelectorAll('[role="gridcell"], [role="cell"]');
        const row = {};
        for (let j = 0; j < cells.length; j++) {
          const header = headers[j] || `Column ${j + 1}`;
          const text = cells[j].textContent?.trim().replace(/\s+/g, ' ') || '';
          const link = cells[j].querySelector('a')?.href || null;
          row[header] = link && text ? { text, link } : text;
        }
        rows.push(row);
      }

      if (rows.length < 2) continue;

      patterns.push({
        type: 'aria_grid',
        headers: headers.length > 0 ? headers : Object.keys(rows[0] || {}),
        rows,
        selector: buildSelector(grid),
        score: rows.length * 8,
        element: grid,
      });
    }

    return patterns;
  }

  /**
   * Build a CSS selector — safe for SVG elements where className is SVGAnimatedString.
   */
  function buildSelector(el) {
    if (el.id) return `#${el.id}`;
    const tag = el.tagName.toLowerCase();
    const cn = typeof el.className === 'string' ? el.className : '';
    const cls = cn ? '.' + cn.trim().split(/\s+/).slice(0, 2).join('.') : '';
    const parent = el.parentElement;
    if (parent) {
      const siblings = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
      if (siblings.length > 1) {
        const idx = siblings.indexOf(el);
        return `${tag}${cls}:nth-of-type(${idx + 1})`;
      }
    }
    return `${tag}${cls}`;
  }

  function deduplicatePatterns(patterns) {
    const result = [];
    for (const p of patterns) {
      const isDuplicate = patterns.some(
        (other) =>
          other !== p &&
          other.score >= p.score &&
          other.element !== p.element &&
          other.element.contains(p.element)
      );
      if (!isDuplicate) result.push(p);
    }
    return result;
  }

  function detectAllPatterns() {
    const all = [
      ...detectTables(),
      ...detectRepeatingElements(),
      ...detectDefinitionLists(),
      ...detectAriaGrids(),
    ];

    const deduped = deduplicatePatterns(all);
    deduped.sort((a, b) => b.score - a.score);

    return deduped.map(({ element, ...rest }) => rest);
  }

  // --- Flatten / Export ---

  function flattenValue(val) {
    if (val === null || val === undefined) return '';
    if (typeof val === 'object' && val.text !== undefined) return val.text;
    return String(val);
  }

  function flattenRows(pattern) {
    return pattern.rows.map((row) => {
      const flat = {};
      for (const [key, val] of Object.entries(row)) {
        flat[key] = flattenValue(val);
      }
      return flat;
    });
  }

  const escape = (val) => {
    const str = String(val ?? '');
    if (str.includes(',') || str.includes('"') || str.includes('\n')) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };

  function toCSV(pattern) {
    const headers = pattern.headers;
    const rows = flattenRows(pattern);
    const lines = [headers.map(escape).join(',')];
    for (const row of rows) {
      lines.push(headers.map((h) => escape(row[h] ?? '')).join(','));
    }
    return lines.join('\n');
  }

  function toTSV(pattern) {
    const headers = pattern.headers;
    const rows = flattenRows(pattern);
    const lines = [headers.join('\t')];
    for (const row of rows) {
      lines.push(headers.map((h) => (row[h] ?? '').replace(/\t/g, ' ')).join('\t'));
    }
    return lines.join('\n');
  }

  /**
   * Merge all patterns into one CSV with separator rows between tables.
   */
  function allToCSV(patterns) {
    if (patterns.length === 0) return '';
    if (patterns.length === 1) return toCSV(patterns[0]);

    const sections = [];
    for (let i = 0; i < patterns.length; i++) {
      const p = patterns[i];
      const label = `--- Table ${i + 1}: ${p.type} (${p.rows.length} rows) ---`;
      sections.push(label);
      sections.push(toCSV(p));
      sections.push(''); // blank line separator
    }
    return sections.join('\n');
  }

  // --- Message Handler ---

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.action === 'detectPatterns') {
      cachedPatterns = null; // Force fresh detection when popup opens
      const patterns = getCachedPatterns();
      sendResponse({ patterns });
    } else if (message.action === 'getCSV') {
      const patterns = getCachedPatterns();
      const idx = message.patternIndex || 0;
      if (patterns[idx]) {
        sendResponse({ csv: toCSV(patterns[idx]), filename: buildFilename('csv') });
      } else {
        sendResponse({ csv: null });
      }
    } else if (message.action === 'getAllCSV') {
      const patterns = getCachedPatterns();
      if (patterns.length > 0) {
        sendResponse({ csv: allToCSV(patterns), filename: buildFilename('all', 'csv') });
      } else {
        sendResponse({ csv: null });
      }
    } else if (message.action === 'getTSV') {
      const patterns = getCachedPatterns();
      const idx = message.patternIndex || 0;
      if (patterns[idx]) {
        sendResponse({ tsv: toTSV(patterns[idx]) });
      } else {
        sendResponse({ tsv: null });
      }
    } else if (message.action === 'getCount') {
      const patterns = getCachedPatterns();
      const totalRows = patterns.reduce((s, p) => s + p.rows.length, 0);
      sendResponse({ count: totalRows, patternCount: patterns.length });
    } else if (message.action === 'scrape') {
      const patterns = getCachedPatterns();
      const idx = message.patternIndex || 0;
      if (patterns[idx]) {
        const flat = flattenRows(patterns[idx]);
        sendResponse({
          contacts: flat,
          domain: window.location.hostname,
          source: 'auto_detect',
          dataType: 'table_data',
        });
      } else {
        sendResponse({ contacts: [], domain: window.location.hostname, source: 'auto_detect' });
      }
    }
    return true;
  });

  function buildFilename(...parts) {
    const host = window.location.hostname.replace(/^www\./, '');
    const path = window.location.pathname.replace(/\//g, '_').replace(/^_|_$/g, '') || 'page';
    if (parts.length === 2) return `${host}_${path}_${parts[0]}.${parts[1]}`;
    return `${host}_${path}.${parts[0]}`;
  }
})();

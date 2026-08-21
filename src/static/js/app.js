/* LakeStream - Frontend JavaScript
   HTMX Configuration + Alpine.js Components
   ========================================== */

// HTMX Configuration
document.body.addEventListener('htmx:configRequest', (event) => {
  // Add CSRF token if present
  const csrfToken = document.querySelector('meta[name="csrf-token"]');
  if (csrfToken) {
    event.detail.headers['X-CSRF-Token'] = csrfToken.content;
  }
});

// Handle successful form submissions
document.body.addEventListener('htmx:afterRequest', (event) => {
  if (event.detail.successful) {
    // Show success toast for job creation
    if (event.detail.pathInfo.requestPath === '/api/scrape/execute') {
      const response = JSON.parse(event.detail.xhr.response);
      if (response.job_id) {
        Alpine.store('toast').show('Scrape job started!', 'success');
        // Redirect to job status page
        setTimeout(() => {
          window.location.href = `/jobs/${response.job_id}`;
        }, 500);
      }
    }
  }
});

// Handle errors
document.body.addEventListener('htmx:responseError', (event) => {
  Alpine.store('toast').show('Something went wrong. Please try again.', 'error');
});

// Alpine.js initialization
document.addEventListener('alpine:init', () => {

  // Dark mode store
  Alpine.store('darkMode', {
    on: localStorage.getItem('darkMode') === 'true' ||
        (!localStorage.getItem('darkMode') && window.matchMedia('(prefers-color-scheme: dark)').matches),
    toggle() {
      this.on = !this.on;
      localStorage.setItem('darkMode', this.on);
      document.documentElement.classList.toggle('dark', this.on);
    },
    init() {
      document.documentElement.classList.toggle('dark', this.on);
    }
  });

  // Toast notification store
  Alpine.store('toast', {
    message: '',
    type: 'info', // info, success, warning, error
    visible: false,

    show(message, type = 'info') {
      this.message = message;
      this.type = type;
      this.visible = true;
      setTimeout(() => {
        this.visible = false;
      }, 4000);
    }
  });

  // Guided tour store
  Alpine.store('guide', {
    active: false,
    step: 0,
    steps: [
      {
        target: '#quick-start-input',
        title: 'Enter a Website',
        text: 'Type or paste the URL of the website you want to scrape.'
      },
      {
        target: '#data-type-checkboxes',
        title: 'Choose Data Types',
        text: 'Select what information you want to extract. Blog posts and contacts are selected by default.'
      },
      {
        target: '#start-scrape-btn',
        title: 'Start Scraping',
        text: 'Click here to begin. We\'ll automatically detect the best approach for the site.'
      }
    ],

    start() {
      this.active = true;
      this.step = 0;
      this.highlight();
    },

    next() {
      if (this.step < this.steps.length - 1) {
        this.step++;
        this.highlight();
      } else {
        this.finish();
      }
    },

    prev() {
      if (this.step > 0) {
        this.step--;
        this.highlight();
      }
    },

    skip() {
      this.finish();
    },

    finish() {
      this.active = false;
      this.clearHighlight();
      localStorage.setItem('lakeb2b_tour_completed', 'true');
    },

    highlight() {
      this.clearHighlight();
      const currentStep = this.steps[this.step];
      const target = document.querySelector(currentStep.target);
      if (target) {
        target.classList.add('ring-2', 'ring-accent', 'ring-offset-2');
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    },

    clearHighlight() {
      document.querySelectorAll('.ring-accent').forEach(el => {
        el.classList.remove('ring-2', 'ring-accent', 'ring-offset-2');
      });
    },

    shouldShow() {
      return !localStorage.getItem('lakeb2b_tour_completed');
    }
  });

  // Collapsible component
  Alpine.data('collapsible', (initialOpen = false) => ({
    open: initialOpen,
    toggle() {
      this.open = !this.open;
    }
  }));

  // Modal component
  Alpine.data('modal', () => ({
    open: false,
    show() {
      this.open = true;
      document.body.style.overflow = 'hidden';
    },
    close() {
      this.open = false;
      document.body.style.overflow = '';
    }
  }));

  // Form validation component
  Alpine.data('formValidation', () => ({
    errors: {},

    validateUrl(value) {
      if (!value) {
        return 'Please enter a website URL';
      }
      // Add https:// if missing
      if (!value.startsWith('http://') && !value.startsWith('https://')) {
        value = 'https://' + value;
      }
      try {
        new URL(value);
        return null;
      } catch {
        return 'Please enter a valid URL';
      }
    },

    validate(field, value, validator) {
      const error = validator(value);
      if (error) {
        this.errors[field] = error;
      } else {
        delete this.errors[field];
      }
      return !error;
    },

    hasErrors() {
      return Object.keys(this.errors).length > 0;
    }
  }));

  // Dropdown component
  Alpine.data('dropdown', () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    close() {
      this.open = false;
    }
  }));

  // Quick Scrape component (dashboard glassmorphic form)
  Alpine.data('quickScrape', () => ({
    domain: '',
    dataTypes: ['blog_url', 'article', 'contact', 'tech_stack', 'resource'],
    maxPages: 100,
    templateId: '',
    scrapingTier: 'auto',
    rawOnly: false,
    llmMode: 'off',      // 'off' | 'fallback' | 'only'
    llmAvailable: false,
    techWappalyzer: false,
    techLlmFallback: false,
    priority: 5,
    showPanel: false,
    showAdvanced: false,
    loading: false,
    errors: {},
    allDataTypes: [
      { value: 'blog_url', label: 'Blog Posts' },
      { value: 'article', label: 'Articles' },
      { value: 'contact', label: 'Contacts' },
      { value: 'tech_stack', label: 'Tech Stack' },
      { value: 'resource', label: 'Resources' },
      { value: 'pricing', label: 'Pricing' },
    ],

    init() {
      const params = new URLSearchParams(window.location.search);
      const prefill = params.get('domain');
      if (prefill) {
        this.domain = prefill;
        this.showPanel = true;
      }
      // Check if OpenRouter API key is configured (for LLM mode availability)
      fetch('/api/settings/', { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data && data.openrouter_api_key_set) {
            this.llmAvailable = true;
          }
        })
        .catch(() => {});
    },

    validateDomain() {
      if (!this.domain) {
        this.errors.domain = 'Please enter a website URL';
        return false;
      }
      let url = this.domain.trim();
      if (!url.startsWith('http://') && !url.startsWith('https://')) {
        url = 'https://' + url;
      }
      try {
        new URL(url);
        this.domain = url.replace(/^https?:\/\//, '').replace(/\/$/, '');
        delete this.errors.domain;
        return true;
      } catch {
        this.errors.domain = 'Please enter a valid URL';
        return false;
      }
    },

    async submit() {
      if (!this.validateDomain()) return;
      if (this.dataTypes.length === 0) {
        Alpine.store('toast').show('Select at least one data type', 'warning');
        return;
      }
      this.loading = true;

      // Map frontend tier values to backend enum values
      const tierMap = {
        'auto': null,                // null triggers adaptive escalation
        'playwright': 'playwright',  // Playwright (default)
        'proxy': 'playwright_proxy'  // Playwright + Proxy
      };

      const payload = {
        domain: this.domain,
        data_types: this.dataTypes,
        max_pages: parseInt(this.maxPages),
        priority: this.priority,
      };
      if (this.templateId) payload.template_id = this.templateId;
      if (this.rawOnly) payload.raw_only = true;
      if (this.llmMode && this.llmMode !== 'off' && !this.rawOnly) {
        payload.llm_mode = this.llmMode;
      }
      if (this.dataTypes.includes('tech_stack')) {
        if (this.techWappalyzer) payload.tech_stack_wappalyzer = true;
        if (this.techLlmFallback) payload.tech_stack_llm_fallback = true;
      }

      // Only include tier if not 'auto' (null triggers adaptive escalation)
      const backendTier = tierMap[this.scrapingTier];
      if (backendTier !== null) {
        payload.tier = backendTier;
      }

      try {
        const response = await fetch('/api/scrape/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (response.ok && data.job_id) {
          Alpine.store('toast').show('Scrape job started!', 'success');
          setTimeout(() => { window.location.href = '/jobs/' + data.job_id; }, 500);
        } else {
          Alpine.store('toast').show(data.detail || 'Failed to create job', 'error');
          this.loading = false;
        }
      } catch (err) {
        Alpine.store('toast').show('Network error. Please try again.', 'error');
        this.loading = false;
      }
    }
  }));

  // YouTube Transcript component (dashboard)
  Alpine.data('youtubeTranscript', () => ({
    url: '',
    loading: false,
    result: null,
    error: null,
    copied: false,

    async extract() {
      if (!this.url.trim()) {
        this.error = 'Please enter a YouTube URL';
        return;
      }
      this.loading = true;
      this.error = null;
      this.result = null;

      try {
        const response = await fetch('/api/scrape/youtube-transcript', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: this.url.trim() }),
        });
        const data = await response.json();
        if (data.success) {
          this.result = data;
          this._saveToHistory(data);
          Alpine.store('toast').show('Transcript extracted!', 'success');
        } else {
          this.error = data.error || 'Failed to extract transcript';
        }
      } catch (err) {
        this.error = 'Network error. Please try again.';
      }
      this.loading = false;
    },

    copyTranscript() {
      if (!this.result?.transcript_text) return;
      const text = this.result.transcript_text;
      const onSuccess = () => {
        this.copied = true;
        setTimeout(() => this.copied = false, 2000);
      };
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
          // Fallback for non-secure contexts or permission denied
          this._fallbackCopy(text);
          onSuccess();
        });
      } else {
        this._fallbackCopy(text);
        onSuccess();
      }
    },

    _fallbackCopy(text) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    },

    downloadTxt() {
      if (!this.result?.transcript_text) return;
      const title = this.result.metadata?.title || 'transcript';
      const filename = title.replace(/[^a-z0-9]/gi, '_').substring(0, 50) + '.txt';
      const blob = new Blob([this.result.transcript_text], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    },

    formatDuration(seconds) {
      if (!seconds) return '';
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return m + 'm ' + s + 's';
    },

    _saveToHistory(data) {
      try {
        const history = JSON.parse(localStorage.getItem('yt_transcripts') || '[]');
        const entry = {
          id: data.video_id,
          url: this.url.trim(),
          title: data.metadata?.title || 'Untitled',
          channel: data.metadata?.channel || '',
          duration_seconds: data.duration_seconds,
          language: data.language,
          segment_count: data.segment_count,
          transcript_text: data.transcript_text,
          extracted_at: new Date().toISOString(),
        };
        // Replace if same video_id already exists
        const idx = history.findIndex(h => h.id === entry.id);
        if (idx >= 0) history.splice(idx, 1);
        history.unshift(entry);
        // Keep last 50
        localStorage.setItem('yt_transcripts', JSON.stringify(history.slice(0, 50)));
      } catch (e) { /* localStorage full or unavailable */ }
    }
  }));

  // YouTube Transcript History component (results page)
  Alpine.data('transcriptHistory', () => ({
    transcripts: [],
    expandedId: null,
    copied: null,

    init() {
      try {
        this.transcripts = JSON.parse(localStorage.getItem('yt_transcripts') || '[]');
      } catch (e) { this.transcripts = []; }
    },

    formatDuration(seconds) {
      if (!seconds) return '';
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return m + 'm ' + s + 's';
    },

    timeAgo(iso) {
      const diff = Date.now() - new Date(iso).getTime();
      const mins = Math.floor(diff / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return mins + 'm ago';
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) return hrs + 'h ago';
      const days = Math.floor(hrs / 24);
      return days + 'd ago';
    },

    copyTranscript(t) {
      const text = t.transcript_text;
      const onSuccess = () => {
        this.copied = t.id;
        setTimeout(() => this.copied = null, 2000);
      };
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
          this._fallbackCopy(text);
          onSuccess();
        });
      } else {
        this._fallbackCopy(text);
        onSuccess();
      }
    },

    _fallbackCopy(text) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    },

    downloadTxt(t) {
      const filename = (t.title || 'transcript').replace(/[^a-z0-9]/gi, '_').substring(0, 50) + '.txt';
      const blob = new Blob([t.transcript_text], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    },

    removeTranscript(t) {
      this.transcripts = this.transcripts.filter(x => x.id !== t.id);
      localStorage.setItem('yt_transcripts', JSON.stringify(this.transcripts));
    },

    clearAll() {
      this.transcripts = [];
      localStorage.removeItem('yt_transcripts');
    }
  }));

  // MCP Connection Guide component (dashboard modal)
  Alpine.data('mcpGuide', () => ({
    open: false,
    tab: 'claude-code',
  }));

  // Add Site Modal component (domains page)
  Alpine.data('addSiteModal', () => ({
    open: false,
    domain: '',
    frequency: 'weekly',
    dataTypes: ['blog_url', 'article', 'contact', 'tech_stack', 'resource'],
    maxPages: 100,
    webhookUrl: '',
    loading: false,
    techWappalyzer: false,
    techLlmFallback: false,
    llmAvailable: false,
    allDataTypes: [
      { value: 'blog_url', label: 'Blog Posts' },
      { value: 'article', label: 'Articles' },
      { value: 'contact', label: 'Contacts' },
      { value: 'tech_stack', label: 'Tech Stack' },
      { value: 'resource', label: 'Resources' },
      { value: 'pricing', label: 'Pricing' },
    ],

    init() {
      fetch('/api/settings/', { credentials: 'same-origin' })
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data && data.openrouter_api_key_set) {
            this.llmAvailable = true;
          }
        })
        .catch(() => {});
    },

    async submit() {
      if (!this.domain) {
        Alpine.store('toast').show('Please enter a domain', 'warning');
        return;
      }
      this.loading = true;
      let cleanDomain = this.domain.trim();
      if (cleanDomain.startsWith('http://') || cleanDomain.startsWith('https://')) {
        cleanDomain = cleanDomain.replace(/^https?:\/\//, '').replace(/\/$/, '');
      }
      const payload = {
        domain: cleanDomain,
        data_types: this.dataTypes,
        scrape_frequency: this.frequency,
        max_pages: parseInt(this.maxPages),
      };
      if (this.webhookUrl) payload.webhook_url = this.webhookUrl;
      if (this.techWappalyzer) payload.tech_stack_wappalyzer = true;
      if (this.techLlmFallback) payload.tech_stack_llm_fallback = true;

      try {
        const response = await fetch('/api/tracked/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (response.ok) {
          Alpine.store('toast').show('Site added for tracking!', 'success');
          setTimeout(() => window.location.reload(), 500);
        } else {
          const data = await response.json();
          Alpine.store('toast').show(data.detail || 'Failed to add site', 'error');
        }
      } catch (err) {
        Alpine.store('toast').show('Network error. Please try again.', 'error');
      }
      this.loading = false;
    }
  }));

  // Human labels for every evidence_type. 'wappalyzer' is injected by the merge
  // step and is absent from the parser's own confidence map, so it must be here
  // explicitly or those rows render a blank Source cell.
  const TD_SOURCE_LABELS = {
    meta_generator: 'Meta generator',
    header: 'HTTP header',
    script_url: 'Script URL',
    link_url: 'Link URL',
    inline_script: 'Inline script',
    html_fallback: 'HTML match',
    wappalyzer: 'Wappalyzer',
  };
  const TD_CONFIDENCE_RANK = { high: 0, medium: 1, low: 2 };

  // Tech Detect page: single-URL lookup, durable CSV batch, client-side category filter
  Alpine.data('techDetect', () => ({
    singleUrl: '',
    urls: [],
    rows: [],
    selected: [],
    expandedKey: null,
    seq: 0,
    saveToResults: false,
    dragging: false,
    csvWarning: '',
    busy: false,
    running: false,
    done: 0,
    total: 0,
    runId: null,
    pollTimer: null,
    pollDelay: 2000,
    pollStarted: 0,
    hiddenSince: 0,

    get categories() {
      return window.TECH_CATEGORIES || [];
    },

    init(resumeRun) {
      this.selectAll();
      if (resumeRun) {
        this.runId = resumeRun;
        this.running = true;
        this.startPolling();
      }
    },

    selectAll() {
      this.selected = this.categories.map(c => c.id);
    },

    labelFor(id) {
      const hit = this.categories.find(c => c.id === id);
      return hit ? hit.label : id;
    },

    badgeClass(status) {
      return {
        ok: 'badge-success',
        blocked: 'badge-warning',
        skipped: 'badge-neutral',
        failed: 'badge-error',
      }[status] || 'badge-neutral';
    },

    // Confidence gets its own scale: medium is informational (blue), not a
    // warning (amber) — a script_url match is not something going wrong.
    confidenceClass(c) {
      return {
        high: 'badge-success',
        medium: 'badge-info',
        low: 'badge-neutral',
      }[c] || 'badge-neutral';
    },

    sourceLabel(t) {
      return TD_SOURCE_LABELS[t] || t || '—';
    },

    toggle(key) {
      this.expandedKey = this.expandedKey === key ? null : key;
    },

    isExpanded(row) {
      return this.expandedKey === row.key;
    },

    // Every detection for one URL, regardless of the category chips — the panel
    // deliberately shows the complete list. `inFilter` drives the muting so the
    // panel count and the row's # column visibly reconcile.
    detailRows(row) {
      const out = (row.detections || []).map(d => {
        const cid = d.category_id || 'other';
        return {
          name: d.name,
          category_id: cid,
          category_label: this.labelFor(cid),
          category_raw: d.category_raw || d.category || '',
          confidence: d.confidence || '',
          evidence_type: d.evidence_type || '',
          source_label: this.sourceLabel(d.evidence_type),
          evidence: d.evidence || '',
          recommended: !!d.recommended,
          inFilter: this.selected.includes(cid),
        };
      });
      out.sort((a, b) => {
        const ra = TD_CONFIDENCE_RANK[a.confidence] ?? 3;
        const rb = TD_CONFIDENCE_RANK[b.confidence] ?? 3;
        if (ra !== rb) return ra - rb;
        if (a.category_label !== b.category_label) return a.category_label.localeCompare(b.category_label);
        return a.name.localeCompare(b.name);
      });
      return out;
    },

    matchedCount(row) {
      return this.detailRows(row).filter(d => d.inFilter).length;
    },

    // Filtering is pure client state, so toggling a chip never refetches.
    shown(row) {
      const out = {};
      const byCat = row.by_category || {};
      for (const cat of Object.keys(byCat)) {
        if (this.selected.includes(cat) && byCat[cat].length) out[cat] = byCat[cat];
      }
      return out;
    },

    countShown(row) {
      return Object.values(this.shown(row)).reduce((n, v) => n + v.length, 0);
    },

    countFor(id) {
      let n = 0;
      for (const row of this.rows) {
        const names = (row.by_category || {})[id];
        if (names) n += names.length;
      }
      return n ? n : '';
    },

    get visibleRows() {
      if (!this.selected.length) return [];
      return this.rows.filter(r => r.status !== 'ok' || this.countShown(r) > 0);
    },

    get pct() {
      return this.total ? Math.round((this.done / this.total) * 100) : 0;
    },

    get progressLabel() {
      return this.running ? 'Detecting…' : 'Done';
    },

    // `key` is supplied by the caller and must be stable: batch results arrive
    // out of order (concurrency 10), so an index into the filtered list would
    // change every poll and tear down the row the user has expanded.
    normalize(r, key) {
      return {
        key: key,
        input_url: r.input_url || r.url || '',
        final_url: r.final_url || '',
        domain: r.domain || '',
        status: r.status || 'failed',
        error: r.error || '',
        by_category: r.by_category || {},
        total: r.total || 0,
        detections: Array.isArray(r.detections) ? r.detections : [],
        http_status: r.http_status ?? null,
        duration_ms: r.duration_ms || 0,
        escalated: !!r.escalated,
        truncated: !!r.truncated,
      };
    },

    reset() {
      this.urls = [];
      this.csvWarning = '';
      if (this.$refs.csv) this.$refs.csv.value = '';
    },

    async runSingle() {
      const url = this.singleUrl.trim();
      if (!url || this.busy) return;
      this.busy = true;
      try {
        const resp = await fetch('/api/tech-detect/detect', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: url }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          Alpine.store('toast').show(data.detail || 'Detection failed', 'error');
        } else {
          const fresh = this.normalize(data, 's' + (this.seq++));
          this.rows = [fresh, ...this.rows];
          this.total = this.rows.length;
          this.done = this.rows.length;
          this.expandedKey = fresh.key;
          if (data.status !== 'ok') {
            Alpine.store('toast').show(data.error || ('Status: ' + data.status), 'warning');
          }
        }
      } catch (e) {
        Alpine.store('toast').show('Network error. Please try again.', 'error');
      }
      this.busy = false;
      this.$nextTick(() => window.lucide && window.lucide.createIcons());
    },

    async onFile() {
      const input = this.$refs.csv;
      if (!input || !input.files || !input.files.length) return;
      const fd = new FormData();
      fd.append('csv_file', input.files[0]);
      this.busy = true;
      this.csvWarning = '';
      try {
        const resp = await fetch('/api/tech-detect/parse-csv', {
          method: 'POST', credentials: 'same-origin', body: fd,
        });
        const data = await resp.json();
        if (!resp.ok) {
          Alpine.store('toast').show(data.detail || 'Could not read CSV', 'error');
        } else {
          this.urls = data.urls || [];
          const bits = [];
          if (data.warning) bits.push(data.warning);
          if (data.invalid_count) bits.push(data.invalid_count + ' row(s) skipped as invalid.');
          if (data.duplicates) bits.push(data.duplicates + ' duplicate(s) removed.');
          this.csvWarning = bits.join(' ');
        }
      } catch (e) {
        Alpine.store('toast').show('Network error reading CSV', 'error');
      }
      this.busy = false;
      this.$nextTick(() => window.lucide && window.lucide.createIcons());
    },

    async runBatch() {
      if (!this.urls.length || this.busy) return;
      this.busy = true;
      this.rows = [];
      this.done = 0;
      this.total = this.urls.length;
      try {
        const resp = await fetch('/api/tech-detect/batch', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            urls: this.urls,
            save_to_results: this.saveToResults,
            client_token: 'td-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8),
          }),
        });
        const data = await resp.json();
        if (!resp.ok) {
          Alpine.store('toast').show(data.detail || 'Could not start batch', 'error');
          this.busy = false;
          return;
        }
        this.runId = data.run_id;
        this.running = true;
        this.pollDelay = 2000;
        this.pollStarted = Date.now();
        history.replaceState(null, '', '/tech-detect?run=' + data.run_id);
        this.startPolling();
      } catch (e) {
        Alpine.store('toast').show('Network error starting batch', 'error');
      }
      this.busy = false;
    },

    startPolling() {
      if (this.pollTimer) clearTimeout(this.pollTimer);
      this.pollStarted = this.pollStarted || Date.now();
      this.poll();
    },

    async poll() {
      if (!this.runId) return;
      // A forgotten background tab should stop polling rather than run forever.
      if (document.hidden) {
        if (!this.hiddenSince) this.hiddenSince = Date.now();
        if (Date.now() - this.hiddenSince > 300000) { this.running = false; return; }
      } else {
        this.hiddenSince = 0;
      }
      try {
        const resp = await fetch('/api/tech-detect/runs/' + this.runId, { credentials: 'same-origin' });
        if (resp.status === 429) {
          const retry = parseInt(resp.headers.get('Retry-After') || '5', 10);
          this.pollTimer = setTimeout(() => this.poll(), Math.max(retry, 2) * 1000);
          return;
        }
        if (!resp.ok) throw new Error('poll failed');
        const data = await resp.json();
        this.total = data.total;
        this.done = data.done;
        this.rows = (data.results || [])
          .filter(r => r.status !== 'pending')
          .map(r => this.normalize(r, 'p' + r.position));
        if (this.expandedKey && !this.rows.some(r => r.key === this.expandedKey)) {
          this.expandedKey = null;
        }
        if (['completed', 'failed', 'cancelled'].includes(data.status)) {
          this.running = false;
          this.pollTimer = null;
          if (data.status === 'failed') {
            Alpine.store('toast').show(data.error || 'Run failed', 'error');
          } else {
            Alpine.store('toast').show('Detection complete', 'success');
          }
          this.$nextTick(() => window.lucide && window.lucide.createIcons());
          return;
        }
      } catch (e) {
        // transient — keep polling
      }
      if (Date.now() - this.pollStarted > 60000) this.pollDelay = 5000;
      this.pollTimer = setTimeout(() => this.poll(), this.pollDelay);
    },

    exportRows() {
      return this.visibleRows.map(r => ({
        url: r.final_url || r.input_url,
        domain: r.domain,
        status: r.status,
        http_status: r.http_status,
        error: r.error,
        technologies: this.shown(r),
        detections: this.detailRows(r).filter(d => d.inFilter),
      }));
    },

    download(filename, text, type) {
      const blob = new Blob([text], { type: type });
      const href = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = href;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(href);
    },

    downloadJson() {
      this.download('tech-detect.json', JSON.stringify(this.exportRows(), null, 2), 'application/json');
    },

    // `evidence` is a raw substring of a third-party page, so a value starting
    // =, +, - or @ would execute as a formula when Excel opens the file.
    csvQuote(v) {
      let s = String(v == null ? '' : v).replace(/\r?\n/g, ' ');
      if (/^[=+\-@]/.test(s)) s = "'" + s;
      return '"' + s.replace(/"/g, '""') + '"';
    },

    // Long format: one line per detection so it pivots in Excel. URLs that
    // produced no technologies still get a line — otherwise a blocked domain
    // vanishes and can never be reconciled against the input list.
    downloadCsv() {
      const head = ['url', 'domain', 'status', 'technology', 'category',
        'confidence', 'evidence_type', 'evidence', 'recommended', 'error'];
      const lines = [head.map(v => this.csvQuote(v)).join(',')];
      for (const r of this.visibleRows) {
        const url = r.final_url || r.input_url;
        const dets = this.detailRows(r).filter(d => d.inFilter);
        if (!dets.length) {
          lines.push([url, r.domain, r.status, '', '', '', '', '', '', r.error]
            .map(v => this.csvQuote(v)).join(','));
          continue;
        }
        for (const d of dets) {
          lines.push([url, r.domain, r.status, d.name, d.category_label,
            d.confidence, d.source_label, d.evidence,
            d.recommended ? 'yes' : 'no', r.error]
            .map(v => this.csvQuote(v)).join(','));
        }
      }
      // BOM so Excel on Windows reads UTF-8 correctly. Added here, not in
      // download(), which is shared with the JSON export.
      this.download('tech-detect.csv', '﻿' + lines.join('\r\n'), 'text/csv');
    },
  }));

});

// Utility: Format relative time
function timeAgo(dateString) {
  const date = new Date(dateString);
  const now = new Date();
  const seconds = Math.floor((now - date) / 1000);

  if (seconds < 60) return 'just now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
  if (seconds < 604800) return Math.floor(seconds / 86400) + 'd ago';

  return date.toLocaleDateString();
}

// Utility: Format duration
function formatDuration(ms) {
  if (!ms) return '-';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return seconds + 's';
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  return minutes + 'm ' + remainingSeconds + 's';
}

// Check if first visit and show tour
document.addEventListener('DOMContentLoaded', () => {
  // Only show tour on dashboard and if not completed
  if (window.location.pathname === '/' && Alpine.store('guide').shouldShow()) {
    setTimeout(() => {
      Alpine.store('guide').start();
    }, 1000);
  }
});

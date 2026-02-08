/* Lake B2B Scraper - Frontend JavaScript
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
    dataTypes: ['blog_url', 'article', 'contact', 'tech_stack', 'resource', 'pricing'],
    maxPages: 100,
    templateId: '',
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
      const payload = {
        domain: this.domain,
        data_types: this.dataTypes,
        max_pages: parseInt(this.maxPages),
        priority: this.priority,
      };
      if (this.templateId) payload.template_id = this.templateId;

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

  // Add Site Modal component (domains page)
  Alpine.data('addSiteModal', () => ({
    open: false,
    domain: '',
    frequency: 'weekly',
    dataTypes: ['blog_url', 'article', 'contact', 'tech_stack', 'resource', 'pricing'],
    maxPages: 100,
    webhookUrl: '',
    loading: false,
    allDataTypes: [
      { value: 'blog_url', label: 'Blog Posts' },
      { value: 'article', label: 'Articles' },
      { value: 'contact', label: 'Contacts' },
      { value: 'tech_stack', label: 'Tech Stack' },
      { value: 'resource', label: 'Resources' },
      { value: 'pricing', label: 'Pricing' },
    ],

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

// Utility: Format currency
function formatCurrency(amount) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 4
  }).format(amount);
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

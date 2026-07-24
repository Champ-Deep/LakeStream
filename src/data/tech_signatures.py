"""Technology detection signatures — LakeStream's own fingerprint database.

Original, hand-curated signature set (not vendored from any third-party
fingerprint database). Each entry:

    {
        "name": "nginx",
        "category": "web_server",
        "signals": [r"nginx"],       # regex patterns, case-insensitive
        "scope": "header",          # "html" (default) | "header" | "cookie"
        "header_name": "server",    # optional: restrict to one header key
    }

`signals` are matched with `re.search` (case-insensitive), so a plain word
like "wordpress" still matches as a literal substring — existing entries
remain valid regex without changes.

Categories: cms, analytics, marketing, framework, cdn, js_library, widget,
web_server, programming_language, os. (Hosting/email-hosting/SSL are
domain-level facts resolved via DNS/TLS in dns_intel.py / ssl_intel.py, not
page signatures — kept out of this file on purpose.)
"""

TECH_SIGNATURES: list[dict] = [
    # ---- CMS ----
    {"name": "WordPress", "category": "cms", "signals": ["wp-content", "wp-includes", "wordpress", "wp-json"]},
    {"name": "HubSpot", "category": "cms", "signals": ["js.hs-scripts.com", "hubspot", r"\.hs-", "hbspt"]},
    {"name": "Webflow", "category": "cms", "signals": ["webflow.com", "wf-page", "wf-section"]},
    {"name": "Drupal", "category": "cms", "signals": [r"/sites/default/", "drupal.settings"]},
    {"name": "Squarespace", "category": "cms", "signals": ["squarespace.com", "sqsp", "static.squarespace"]},
    {"name": "Wix", "category": "cms", "signals": ["wix.com", "wixsite.com", "parastorage.com"]},
    # Bare "shopify" deliberately excluded — it false-positives on customer-logo
    # / case-study mentions (e.g. "Shopify" name-dropped on an unrelated site).
    # Domain-qualified signals only.
    {"name": "Shopify", "category": "cms", "signals": ["cdn.shopify.com", "myshopify.com", r"shopify\.shop\b", "x-shopid"]},
    {"name": "Ghost", "category": "cms", "signals": ["ghost.io", "ghost-", "content/themes"]},
    {"name": "Contentful", "category": "cms", "signals": ["contentful.com", "ctfassets.net"]},
    {"name": "Sanity", "category": "cms", "signals": ["cdn.sanity.io", "sanity.io"]},
    {"name": "Webby (custom)", "category": "cms", "signals": [r"generator[\"'\s:=]+webby"], "scope": "html"},

    # ---- Analytics ----
    {"name": "Google Analytics", "category": "analytics", "signals": ["google-analytics.com", r"gtag\(", "ga.js"]},
    {"name": "Google Tag Manager", "category": "analytics", "signals": ["googletagmanager.com", "gtm.js"]},
    {"name": "Segment", "category": "analytics", "signals": ["cdn.segment.com", "analytics.js", "segment.io"]},
    {"name": "Mixpanel", "category": "analytics", "signals": ["mixpanel.com", "mixpanel.init"]},
    {"name": "Amplitude", "category": "analytics", "signals": ["amplitude.com", "cdn.amplitude.com"]},
    {"name": "Heap", "category": "analytics", "signals": ["heap-", "heapanalytics.com"]},
    {"name": "Hotjar", "category": "analytics", "signals": ["hotjar.com", "static.hotjar.com"]},
    {"name": "Plausible", "category": "analytics", "signals": ["plausible.io"]},
    {"name": "Adobe Analytics", "category": "analytics", "signals": ["omniture.com", "2o7.net", "adobedtm.com"]},
    {"name": "Matomo", "category": "analytics", "signals": ["matomo.js", "piwik.js", "matomo.cloud"]},
    {"name": "FullStory", "category": "analytics", "signals": ["fullstory.com", "fs.js"]},
    {"name": "Crazy Egg", "category": "analytics", "signals": ["crazyegg.com"]},
    {"name": "Mouseflow", "category": "analytics", "signals": ["mouseflow.com"]},
    {"name": "Microsoft Clarity", "category": "analytics", "signals": ["clarity.ms"]},

    # ---- Marketing / martech ----
    {"name": "Marketo", "category": "marketing", "signals": ["munchkin.marketo.net", "mktoforms"]},
    {"name": "Pardot", "category": "marketing", "signals": ["pardot.com", "pi.pardot.com", "go.pardot.com"]},
    {"name": "HubSpot Marketing", "category": "marketing", "signals": ["js.hs-analytics.net", "forms.hubspot.com"]},
    {"name": "Mailchimp", "category": "marketing", "signals": ["mailchimp.com", "list-manage.com", "chimpstatic.com"]},
    {"name": "ActiveCampaign", "category": "marketing", "signals": ["activecampaign.com", "trackcmp.net"]},
    {"name": "Salesforce", "category": "marketing", "signals": ["salesforce.com", "force.com"]},
    {"name": "ZoomInfo", "category": "marketing", "signals": ["zoominfo.com", "ws.zoominfo.com"]},
    {"name": "6sense", "category": "marketing", "signals": ["6sense.com", "j.6sc.co"]},
    {"name": "Clearbit", "category": "marketing", "signals": ["clearbit.com", "x.clearbitjs.com"]},

    # ---- Widgets (chat, consent, reviews, social proof) ----
    {"name": "Intercom", "category": "widget", "signals": ["intercom.io", "intercomsettings", "widget.intercom.io"]},
    {"name": "Drift", "category": "widget", "signals": ["drift.com", "driftt.com", "js.driftt.com"]},
    {"name": "Zendesk Chat", "category": "widget", "signals": ["zdassets.com", "zendesk", "zopim"]},
    {"name": "Crisp", "category": "widget", "signals": ["crisp.chat", "client.crisp.chat"]},
    {"name": "Tawk.to", "category": "widget", "signals": ["tawk.to", "embed.tawk.to"]},
    {"name": "Olark", "category": "widget", "signals": ["olark.com", "static.olark.com"]},
    {"name": "OneTrust", "category": "widget", "signals": ["onetrust.com", "cookielaw.org"]},
    {"name": "Cookiebot", "category": "widget", "signals": ["cookiebot.com", "consent.cookiebot.com"]},
    {"name": "Termly", "category": "widget", "signals": ["termly.io"]},
    {"name": "CookieYes", "category": "widget", "signals": ["cookieyes.com"]},
    {"name": "Trustpilot", "category": "widget", "signals": ["trustpilot.com", "widget.trustpilot.com"]},
    {"name": "Yotpo", "category": "widget", "signals": ["yotpo.com", "staticw2.yotpo.com"]},
    {"name": "Judge.me", "category": "widget", "signals": ["judge.me"]},
    {"name": "Calendly", "category": "widget", "signals": ["calendly.com", "assets.calendly.com"]},
    {"name": "Typeform", "category": "widget", "signals": ["typeform.com", "embed.typeform.com"]},

    # ---- JS frameworks ----
    {"name": "React", "category": "framework", "signals": [r"react\.", "reactdom", "__next_data__", r"_next/"]},
    {"name": "Vue.js", "category": "framework", "signals": [r"vue\.js", "__vue__", r"v-if=", "vuejs"]},
    {"name": "Angular", "category": "framework", "signals": ["angular", "ng-version", "ng-app"]},
    {"name": "Next.js", "category": "framework", "signals": ["__next_data__", r"_next/static", "next/dist"]},
    {"name": "Nuxt", "category": "framework", "signals": ["__nuxt", r"nuxt\.js"]},
    {"name": "Gatsby", "category": "framework", "signals": ["gatsby", r"/page-data/"]},
    {"name": "Svelte", "category": "framework", "signals": ["svelte", "__svelte"]},
    {"name": "SvelteKit", "category": "framework", "signals": ["sveltekit", r"_app/immutable/"]},
    {"name": "Ember.js", "category": "framework", "signals": ["ember.js", "data-ember-", "ember-cli"]},
    {"name": "Backbone.js", "category": "framework", "signals": ["backbone.js", "backbone.min"]},
    {"name": "Alpine.js", "category": "framework", "signals": ["alpine.js", "x-data="]},
    {"name": "Astro", "category": "framework", "signals": ["astro-island", "/_astro/"]},
    {"name": "Remix", "category": "framework", "signals": ["__remix", "/build/_shared/"]},
    {"name": "Django", "category": "framework", "signals": ["csrfmiddlewaretoken"], "scope": "html"},
    {"name": "Django", "category": "framework", "signals": ["csrftoken"], "scope": "cookie"},
    {"name": "Ruby on Rails", "category": "framework", "signals": [r"_session"], "scope": "cookie"},
    {"name": "Ruby on Rails", "category": "framework", "signals": ["x-runtime"], "scope": "header"},
    {"name": "Laravel", "category": "framework", "signals": ["laravel_session"], "scope": "cookie"},
    {"name": "ASP.NET", "category": "framework", "signals": ["asp.net_sessionid"], "scope": "cookie"},
    {"name": "ASP.NET", "category": "framework", "signals": ["x-aspnet-version", "x-aspnetmvc-version"], "scope": "header"},
    {"name": "Express", "category": "framework", "signals": ["express"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Flask", "category": "framework", "signals": ["werkzeug"], "scope": "header", "header_name": "server"},

    # ---- Web servers (header-only; needs a real Server header) ----
    {"name": "nginx", "category": "web_server", "signals": ["nginx"], "scope": "header", "header_name": "server"},
    {"name": "Apache", "category": "web_server", "signals": ["apache"], "scope": "header", "header_name": "server"},
    {"name": "Microsoft-IIS", "category": "web_server", "signals": ["microsoft-iis"], "scope": "header", "header_name": "server"},
    {"name": "LiteSpeed", "category": "web_server", "signals": ["litespeed"], "scope": "header", "header_name": "server"},
    {"name": "Caddy", "category": "web_server", "signals": ["caddy"], "scope": "header", "header_name": "server"},
    {"name": "OpenResty", "category": "web_server", "signals": ["openresty"], "scope": "header", "header_name": "server"},
    {"name": "Gunicorn", "category": "web_server", "signals": ["gunicorn"], "scope": "header", "header_name": "server"},
    {"name": "Cowboy (Elixir/Phoenix)", "category": "web_server", "signals": ["cowboy"], "scope": "header", "header_name": "server"},

    # ---- Server OS (rare — most production servers suppress this) ----
    {"name": "Ubuntu", "category": "os", "signals": [r"\(ubuntu\)"], "scope": "header", "header_name": "server"},
    {"name": "Debian", "category": "os", "signals": [r"\(debian\)"], "scope": "header", "header_name": "server"},
    {"name": "CentOS", "category": "os", "signals": [r"\(centos\)"], "scope": "header", "header_name": "server"},
    {"name": "Red Hat", "category": "os", "signals": [r"\(red ?hat\)"], "scope": "header", "header_name": "server"},
    {"name": "Windows Server", "category": "os", "signals": [r"win(32|64)", r"\(windows\)"], "scope": "header", "header_name": "server"},
    {"name": "Amazon Linux", "category": "os", "signals": [r"\(amzn\)", "amazon linux"], "scope": "header", "header_name": "server"},
    {"name": "FreeBSD", "category": "os", "signals": [r"\(freebsd\)"], "scope": "header", "header_name": "server"},

    # ---- Programming languages (BuiltWith-style broad category: backend
    #      leaks via headers/cookies/extensions + frontend compile targets) ----
    {"name": "PHP", "category": "programming_language", "signals": ["php"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "PHP", "category": "programming_language", "signals": ["phpsessid"], "scope": "cookie"},
    {"name": "ASP.NET / C#", "category": "programming_language", "signals": ["asp.net_sessionid"], "scope": "cookie"},
    {"name": "ASP.NET / C#", "category": "programming_language", "signals": ["asp.net"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Java", "category": "programming_language", "signals": ["jsessionid"], "scope": "cookie"},
    {"name": "Ruby", "category": "programming_language", "signals": [r"_session"], "scope": "cookie"},
    {"name": "Python", "category": "programming_language", "signals": ["wsgi", "werkzeug", "gunicorn"], "scope": "header", "header_name": "server"},
    {"name": "Node.js", "category": "programming_language", "signals": ["express", "next.js"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "TypeScript", "category": "programming_language", "signals": [r"\.tsx?\.map", "webpackchunkname"], "scope": "html"},
    {"name": "WebAssembly", "category": "programming_language", "signals": [r"\.wasm", "webassembly.instantiate"], "scope": "html"},
    {"name": "Go", "category": "programming_language", "signals": ["golang"], "scope": "header", "header_name": "server"},

    # ---- CDN (page-level: header/body signals; DNS-level confirmation is
    #      separate, see dns_intel.py — the two are complementary) ----
    {"name": "Cloudflare", "category": "cdn", "signals": ["cf-ray", "cloudflare"], "scope": "header"},
    {"name": "Fastly", "category": "cdn", "signals": ["fastly", "x-served-by"], "scope": "header"},
    {"name": "Akamai", "category": "cdn", "signals": ["akamai", "akamaitech"], "scope": "header"},
    {"name": "AWS CloudFront", "category": "cdn", "signals": ["cloudfront.net", "x-amz-cf"]},
    {"name": "Vercel", "category": "cdn", "signals": ["vercel", "x-vercel-"]},
    {"name": "Netlify", "category": "cdn", "signals": ["netlify", "x-nf-request-id"]},
    {"name": "Sucuri", "category": "cdn", "signals": ["sucuri"], "scope": "header"},
    {"name": "Imperva (Incapsula)", "category": "cdn", "signals": ["incap_ses", "visid_incap"], "scope": "cookie"},
    {"name": "StackPath", "category": "cdn", "signals": ["stackpathcdn.com", "stackpath"]},
    {"name": "KeyCDN", "category": "cdn", "signals": ["kxcdn.com"]},
    {"name": "BunnyCDN", "category": "cdn", "signals": ["b-cdn.net", "bunnycdn.com"]},

    # ---- JS libraries ----
    {"name": "jQuery", "category": "js_library", "signals": ["jquery", "jquery.min.js"]},
    {"name": "Bootstrap", "category": "js_library", "signals": ["bootstrap.min", "bootstrap.css"]},
    {"name": "Tailwind CSS", "category": "js_library", "signals": ["tailwindcss", r"tailwind\."]},
    {"name": "Lodash", "category": "js_library", "signals": ["lodash", "lodash.min"]},
    {"name": "Modernizr", "category": "js_library", "signals": ["modernizr"]},
    {"name": "GSAP", "category": "js_library", "signals": ["gsap.min", "greensock"]},
    {"name": "Swiper", "category": "js_library", "signals": ["swiper-bundle", "swiper.min"]},
    {"name": "Font Awesome", "category": "js_library", "signals": ["fontawesome", "font-awesome"]},
    {"name": "Chart.js", "category": "js_library", "signals": ["chart.js", "chart.min.js"]},
    {"name": "D3.js", "category": "js_library", "signals": ["d3.min.js", "d3js.org"]},
]

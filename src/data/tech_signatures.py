"""Technology detection signatures — LakeStream's own fingerprint database.

Original, hand-curated signature set (not vendored from any third-party
fingerprint database). Consumed by src/scraping/parser/tech_engine.py, which
precompiles it once per process. Each entry:

    {
        "name": "nginx",
        "category": "web_server",
        "signals": [r"nginx"],       # regex patterns, case-insensitive
        "scope": "header",          # "html" (default) | "header" | "cookie"
        "header_name": "server",    # optional: restrict to one header key
    }

`signals` are matched with `re.search` (case-insensitive), so a plain word
like "wordpress" still matches as a literal substring. A `scope` of "header"
without a `header_name` matches header names as well as values, which is how
presence-only signals (`cf-ray`, `x-amz-cf-id`) are expressed.

Several vendors appear more than once, with a different scope per entry — a
structural match (header/cookie) is reported at high confidence and a body
match at medium, and the engine dedups by name keeping the stronger one.

Accuracy rule for `cdn` and `hosting` (measured: overall false-positive rate
1.3%, down from a run where these signals produced 103 bogus detections):
a body signal may only claim "this site is served by X" when it is a hostname
unique to X's serving infrastructure. Two kinds of signal are therefore kept
out of these two categories on purpose:

  - bare vendor words ("imperva", "stackpath"), which match any page that
    merely mentions the vendor;
  - shared-cloud and vendor-marketing hostnames (cloudfront.net,
    googleusercontent.com, cloud.google.com), which thousands of sites
    reference for assets or links without being hosted there.

Those vendors are still detected — from response headers, or from their real
serving hostnames (wpenginepowered.com, pantheonsite.io, fly.dev). A
vendor-unique subdomain of a shared cloud is fine outside these categories:
Bugsnag's d2wy8f7a9ursnm.cloudfront.net identifies Bugsnag precisely, it just
says nothing about who serves the page.

Authoritative infrastructure facts — web hosting, email hosting, SSL — are
resolved per domain via DNS/TLS in dns_intel.py / ssl_intel.py and cached on
CompanyProfile, not inferred from page markup.

Categories map to TechStackMetadata fields via CATEGORY_TO_FIELD in
tech_engine.py; anything unmapped lands in `other_technologies`.
"""

TECH_SIGNATURES: list[dict] = [

    # ---- CMS / site builders ----
    # Bare "wordpress" dropped: matches "Ghost vs WordPress" / "Netlify for
    # WordPress" comparison-page nav links on sites that don't run it —
    # reproduced live on ghost.org and netlify.com. wp-content/wp-includes/
    # wp-json are asset-path signals unique to an actual install.
    {"name": "WordPress", "category": "cms", "signals": ["wp-content", "wp-includes", "wp-json"]},
    {"name": "HubSpot", "category": "cms", "signals": ["js.hs-scripts.com", "hubspot", r"\.hs-", "hbspt"]},
    {"name": "Webflow", "category": "cms", "signals": ["webflow.com", "wf-page", "wf-section"]},
    {"name": "Drupal", "category": "cms", "signals": ["/sites/default/", "drupal.settings"]},
    {"name": "Squarespace", "category": "cms", "signals": ["squarespace.com", "sqsp", "static.squarespace"]},
    {"name": "Wix", "category": "cms", "signals": ["wix.com", "wixsite.com", "parastorage.com"]},
    {"name": "Shopify", "category": "cms", "signals": ["cdn.shopify.com", "myshopify.com", r"shopify\.shop\b", "x-shopid"]},
    {"name": "Ghost", "category": "cms", "signals": [r"ghost\.io", "/ghost/api/", "ghost-sdk"]},
    {"name": "Contentful", "category": "cms", "signals": ["contentful.com", "ctfassets.net"]},
    {"name": "Sanity", "category": "cms", "signals": ["cdn.sanity.io", "sanity.io"]},
    {"name": "Webby (custom)", "category": "cms", "signals": ["generator[\\\"'\\s:=]+webby"], "scope": "html"},
    {"name": "HubSpot", "category": "cms", "signals": [r"js\.hs-scripts\.com", "hbspt", r"\.hs-menu", "hs-cta-wrapper"]},
    {"name": "Sitecore", "category": "cms", "signals": ["sitecore", "/sitecore/", "sc_site", "scItemId"]},
    {"name": "Adobe Experience Manager", "category": "cms", "signals": ["/content/dam/", r"/etc\.clientlibs/", "cq-page-", "/libs/granite/"]},
    {"name": "Adobe Experience Manager", "category": "cms", "signals": ["aem"], "scope": "meta", "meta_name": "generator"},
    {"name": "Kentico", "category": "cms", "signals": ["kentico", "cmspageid", "CMSPreferredCulture"]},
    {"name": "Umbraco", "category": "cms", "signals": ["umbraco", "/umbraco/"]},
    {"name": "Umbraco", "category": "cms", "signals": ["umbraco"], "scope": "meta", "meta_name": "generator"},
    {"name": "Typo3", "category": "cms", "signals": ["typo3", "/typo3conf/", "/typo3temp/"]},
    {"name": "Typo3", "category": "cms", "signals": ["typo3"], "scope": "meta", "meta_name": "generator"},
    {"name": "Joomla", "category": "cms", "signals": ["/media/jui/", "joomla"]},
    {"name": "Joomla", "category": "cms", "signals": ["joomla"], "scope": "meta", "meta_name": "generator"},
    {"name": "Magento", "category": "cms", "signals": ["magento", "mage/cookies", "/static/version", "Magento_"]},
    {"name": "Magento", "category": "cms", "signals": ["x-magento-"], "scope": "header"},
    {"name": "BigCommerce", "category": "cms", "signals": [r"bigcommerce\.com", r"cdn11\.bigcommerce\.com"]},
    {"name": "Prismic", "category": "cms", "signals": [r"prismic\.io", r"cdn\.prismic\.io"]},
    {"name": "Strapi", "category": "cms", "signals": ["strapi"]},
    {"name": "Strapi", "category": "cms", "signals": ["strapi"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Craft CMS", "category": "cms", "signals": [r"craftcms\.com"]},
    {"name": "Craft CMS", "category": "cms", "signals": ["craft cms"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Storyblok", "category": "cms", "signals": [r"storyblok\.com", r"a\.storyblok\.com"]},
    {"name": "DatoCMS", "category": "cms", "signals": [r"datocms\.com", r"www\.datocms-assets\.com"]},
    {"name": "WordPress", "category": "cms", "signals": ["wordpress"], "scope": "meta", "meta_name": "generator"},
    {"name": "Drupal", "category": "cms", "signals": ["drupal"], "scope": "meta", "meta_name": "generator"},
    {"name": "Ghost", "category": "cms", "signals": ["ghost"], "scope": "meta", "meta_name": "generator"},
    {"name": "Shopify", "category": "cms", "signals": ["shopify"], "scope": "meta", "meta_name": "generator"},
    {"name": "Squarespace", "category": "cms", "signals": ["squarespace"], "scope": "meta", "meta_name": "generator"},
    {"name": "Wix", "category": "cms", "signals": ["wix"], "scope": "meta", "meta_name": "generator"},
    {"name": "Webflow", "category": "cms", "signals": ["webflow"], "scope": "meta", "meta_name": "generator"},
    {"name": "HubSpot", "category": "cms", "signals": ["hubspot"], "scope": "meta", "meta_name": "generator"},

    # ---- E-commerce ----
    # Bare "woocommerce" dropped: matches a customer-logo link
    # (stripe.com/customers/woo, data-analytics-label="...woocommerce") on a
    # page that merely lists WooCommerce as a customer, reproduced live on
    # stripe.com. The other three signals are asset/query markers a real
    # WooCommerce install actually emits.
    {"name": "WooCommerce", "category": "ecommerce", "signals": ["wc-cart-fragments", "wc-ajax=", "wp-content/plugins/woocommerce"]},
    {"name": "PrestaShop", "category": "ecommerce", "signals": ["prestashop", "/modules/ps_"]},
    {"name": "OpenCart", "category": "ecommerce", "signals": ["opencart", "route=common/"]},
    {"name": "Salesforce Commerce Cloud", "category": "ecommerce", "signals": [r"demandware\.net", "sfcc", r"demandware\.static"]},
    {"name": "SAP Commerce", "category": "ecommerce", "signals": ["hybris", "sap-commerce", "/_ui/"]},

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
    {"name": "Fathom", "category": "analytics", "signals": [r"usefathom\.com", r"cdn\.usefathom\.com"]},
    {"name": "PostHog", "category": "analytics", "signals": [r"posthog\.com", r"app\.posthog\.com", r"posthog\.init"]},
    {"name": "Microsoft Clarity", "category": "analytics", "signals": [r"clarity\.ms", "microsoft clarity"]},
    {"name": "Pendo", "category": "analytics", "signals": [r"pendo\.io", r"cdn\.pendo\.io", "pendo-"]},
    {"name": "Snowplow", "category": "analytics", "signals": ["snowplow", r"sp\.js", "snowplowanalytics"]},
    {"name": "Countly", "category": "analytics", "signals": ["countly", r"cly\.js"]},
    {"name": "Kissmetrics", "category": "analytics", "signals": [r"kissmetrics\.com", r"kmq\.push"]},

    # ---- Tag managers ----
    {"name": "Tealium", "category": "tag_manager", "signals": [r"tealium\.com", r"tags\.tiqcdn\.com", r"tealiumiq\.com"]},
    {"name": "Ensighten", "category": "tag_manager", "signals": [r"ensighten\.com", r"nexus\.ensighten\.com"]},

    # ---- Marketing / martech / experimentation ----
    {"name": "Marketo", "category": "marketing", "signals": ["munchkin.marketo.net", "mktoforms"]},
    {"name": "Pardot", "category": "marketing", "signals": ["pardot.com", "pi.pardot.com", "go.pardot.com"]},
    {"name": "HubSpot Marketing", "category": "marketing", "signals": ["js.hs-analytics.net", "forms.hubspot.com"]},
    {"name": "Mailchimp", "category": "marketing", "signals": ["mailchimp.com", "list-manage.com", "chimpstatic.com"]},
    {"name": "ActiveCampaign", "category": "marketing", "signals": ["activecampaign.com", "trackcmp.net"]},
    {"name": "Salesforce", "category": "marketing", "signals": ["salesforce.com", "force.com"]},
    {"name": "ZoomInfo", "category": "marketing", "signals": ["zoominfo.com", "ws.zoominfo.com"]},
    {"name": "6sense", "category": "marketing", "signals": ["6sense.com", "j.6sc.co"]},
    {"name": "Clearbit", "category": "marketing", "signals": ["clearbit.com", "x.clearbitjs.com"]},
    {"name": "Optimizely", "category": "marketing", "signals": [r"optimizely\.com", r"cdn\.optimizely\.com", "optimizelyEndUserId"]},
    {"name": "VWO", "category": "marketing", "signals": [r"visualwebsiteoptimizer\.com", "vwo_", r"dev\.visualwebsiteoptimizer"]},
    {"name": "Braze", "category": "marketing", "signals": [r"braze\.com", r"sdk\.iad-03\.braze\.com"]},
    {"name": "Iterable", "category": "marketing", "signals": [r"iterable\.com", r"js\.iterable\.com"]},
    {"name": "Customer.io", "category": "marketing", "signals": [r"customer\.io", r"track\.customer\.io"]},
    {"name": "Zendesk", "category": "marketing", "signals": [r"zendesk\.com", r"zdassets\.com", r"zopim\.com"]},
    {"name": "Freshdesk", "category": "marketing", "signals": [r"freshdesk\.com", r"freshchat\.com"]},
    {"name": "LiveChat", "category": "marketing", "signals": [r"livechatinc\.com", r"cdn\.livechatinc\.com"]},
    {"name": "Cookie consent tools", "category": "marketing", "signals": [r"cookiebot\.com", r"onetrust\.com", "cookieconsent", r"trustarc\.com", r"osano\.com"]},
    {"name": "LaunchDarkly", "category": "marketing", "signals": [r"launchdarkly\.com", r"app\.launchdarkly\.com"]},
    {"name": "Split.io", "category": "marketing", "signals": [r"split\.io", r"cdn\.split\.io"]},
    {"name": "Statsig", "category": "marketing", "signals": [r"statsig\.com", r"featuregates\.org"]},

    # ---- Widgets (chat, consent, reviews, video, accessibility) ----
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
    {"name": "YouTube Embed", "category": "widget", "signals": [r"youtube\.com/embed", r"youtube-nocookie\.com"]},
    {"name": "Vimeo", "category": "widget", "signals": [r"player\.vimeo\.com", r"vimeo\.com"]},
    {"name": "Wistia", "category": "widget", "signals": [r"wistia\.com", r"fast\.wistia\.com", "wistia-"]},
    {"name": "Vidyard", "category": "widget", "signals": [r"vidyard\.com", r"play\.vidyard\.com"]},
    {"name": "AccessiBe", "category": "widget", "signals": [r"accessibe\.com", r"acsbapp\.com"]},
    {"name": "UserWay", "category": "widget", "signals": [r"userway\.org", r"cdn\.userway\.org"]},
    {"name": "AudioEye", "category": "widget", "signals": [r"audioeye\.com", r"cdn\.audioeye\.com"]},

    # ---- Frameworks ----
    {"name": "React", "category": "framework", "signals": [r"react\.", "reactdom", "__next_data__", "_next/"]},
    {"name": "Vue.js", "category": "framework", "signals": [r"vue\.js", "__vue__", "v-if=", "vuejs"]},
    {"name": "Angular", "category": "framework", "signals": ["angular", "ng-version", "ng-app"]},
    {"name": "Next.js", "category": "framework", "signals": ["__next_data__", "_next/static", "next/dist"]},
    {"name": "Nuxt", "category": "framework", "signals": ["__nuxt", r"nuxt\.js"]},
    {"name": "Gatsby", "category": "framework", "signals": ["gatsby", "/page-data/"]},
    {"name": "Svelte", "category": "framework", "signals": ["svelte", "__svelte"]},
    {"name": "SvelteKit", "category": "framework", "signals": ["sveltekit", "_app/immutable/"]},
    {"name": "Ember.js", "category": "framework", "signals": ["ember.js", "data-ember-", "ember-cli"]},
    {"name": "Backbone.js", "category": "framework", "signals": ["backbone.js", "backbone.min"]},
    {"name": "Alpine.js", "category": "framework", "signals": ["alpine.js", "x-data="]},
    {"name": "Astro", "category": "framework", "signals": ["astro-island", "/_astro/"]},
    {"name": "Remix", "category": "framework", "signals": ["__remix", "/build/_shared/"]},
    {"name": "Django", "category": "framework", "signals": ["csrfmiddlewaretoken"], "scope": "html"},
    {"name": "Django", "category": "framework", "signals": ["csrftoken"], "scope": "cookie"},
    {"name": "Ruby on Rails", "category": "framework", "signals": ["_session"], "scope": "cookie"},
    {"name": "Ruby on Rails", "category": "framework", "signals": ["x-runtime"], "scope": "header"},
    {"name": "Laravel", "category": "framework", "signals": ["laravel_session"], "scope": "cookie"},
    {"name": "ASP.NET", "category": "framework", "signals": ["asp.net_sessionid"], "scope": "cookie"},
    {"name": "ASP.NET", "category": "framework", "signals": ["x-aspnet-version", "x-aspnetmvc-version"], "scope": "header"},
    {"name": "Express", "category": "framework", "signals": ["express"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Flask", "category": "framework", "signals": ["werkzeug"], "scope": "header", "header_name": "server"},
    {"name": "HTMX", "category": "framework", "signals": [r"htmx\.org", "hx-get", "hx-post", "hx-trigger"]},
    {"name": "Stimulus", "category": "framework", "signals": [r"stimulus\.js", "data-controller=", "data-action="]},
    {"name": "Turbo", "category": "framework", "signals": [r"turbo\.js", "turbo-frame", "turbo-stream"]},
    {"name": "Preact", "category": "framework", "signals": ["preact", r"preact\.min"]},
    {"name": "Solid.js", "category": "framework", "signals": ["solid-js", r"_\$createComponent"]},
    {"name": "Qwik", "category": "framework", "signals": ["qwik", "qwikloader"]},
    {"name": "Lit", "category": "framework", "signals": ["lit-html", "lit-element", "@lit/"]},
    {"name": "Stencil", "category": "framework", "signals": ["stencil", r"/build/app\.esm\.js"]},
    {"name": "GraphQL", "category": "framework", "signals": ["graphql", "/graphql", "__graphql"]},
    {"name": "Next.js", "category": "framework", "signals": [r"next\.js"], "scope": "meta", "meta_name": "generator"},
    {"name": "Gatsby", "category": "framework", "signals": ["gatsby"], "scope": "meta", "meta_name": "generator"},

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
    {"name": "Moment.js", "category": "js_library", "signals": [r"moment\.min", r"moment\.js"]},
    {"name": "Three.js", "category": "js_library", "signals": [r"three\.min", r"three\.js"]},
    {"name": "Axios", "category": "js_library", "signals": [r"axios\.min", "axios/"]},
    {"name": "Socket.io", "category": "js_library", "signals": [r"socket\.io", r"socket\.io\.min"]},
    {"name": "Lottie", "category": "js_library", "signals": ["lottie", "lottie-player", r"lottie\.min"]},
    {"name": "Framer Motion", "category": "js_library", "signals": ["framer-motion", r"framer\.com"]},
    {"name": "Mapbox", "category": "js_library", "signals": ["mapbox-gl", r"api\.mapbox\.com"]},
    {"name": "Leaflet", "category": "js_library", "signals": [r"leaflet\.js", r"leaflet\.css"]},
    {"name": "Highcharts", "category": "js_library", "signals": [r"highcharts\.com", r"highcharts\.js"]},

    # ---- Build tools ----
    {"name": "Webpack", "category": "build_tool", "signals": ["webpackjsonp", "__webpack_require__", "webpack-"]},
    {"name": "Vite", "category": "build_tool", "signals": ["/@vite/", "vite/modulepreload-polyfill"]},
    {"name": "Parcel", "category": "build_tool", "signals": ["parcelrequire", "__parcel__"]},
    {"name": "Turbopack", "category": "build_tool", "signals": ["turbopack", "__turbopack__"]},
    {"name": "esbuild", "category": "build_tool", "signals": ["esbuild"]},

    # ---- Font providers ----
    {"name": "Google Fonts", "category": "font", "signals": [r"fonts\.googleapis\.com", r"fonts\.gstatic\.com"]},
    {"name": "Adobe Fonts", "category": "font", "signals": [r"use\.typekit\.net", r"p\.typekit\.net"]},

    # ---- Programming languages / runtimes ----
    {"name": "PHP", "category": "programming_language", "signals": ["php"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "PHP", "category": "programming_language", "signals": ["phpsessid"], "scope": "cookie"},
    {"name": "ASP.NET / C#", "category": "programming_language", "signals": ["asp.net_sessionid"], "scope": "cookie"},
    {"name": "ASP.NET / C#", "category": "programming_language", "signals": ["asp.net"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Java", "category": "programming_language", "signals": ["jsessionid"], "scope": "cookie"},
    {"name": "Ruby", "category": "programming_language", "signals": ["_session"], "scope": "cookie"},
    {"name": "Python", "category": "programming_language", "signals": ["wsgi", "werkzeug", "gunicorn"], "scope": "header", "header_name": "server"},
    {"name": "Node.js", "category": "programming_language", "signals": ["express", "next.js"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "TypeScript", "category": "programming_language", "signals": [r"\.tsx?\.map", "webpackchunkname"], "scope": "html"},
    {"name": "WebAssembly", "category": "programming_language", "signals": [r"\.wasm", "webassembly.instantiate"], "scope": "html"},
    {"name": "Go", "category": "programming_language", "signals": ["golang"], "scope": "header", "header_name": "server"},
    {"name": "Java/Spring", "category": "programming_language", "signals": [r"\.jsp", "jsessionid"]},
    {"name": "Java/Spring", "category": "programming_language", "signals": ["servlet"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Java/Spring", "category": "programming_language", "signals": ["apache-coyote"], "scope": "header", "header_name": "server"},

    # ---- Databases ----
    {"name": "Elasticsearch", "category": "database", "signals": ["elasticsearch", "_msearch"]},

    # ---- Search ----
    {"name": "Algolia", "category": "search", "signals": [r"algolia\.com", r"algolianet\.com", "algoliasearch"]},
    {"name": "Typesense", "category": "search", "signals": ["typesense", r"cloud\.typesense\.org"]},
    {"name": "Meilisearch", "category": "search", "signals": ["meilisearch"]},

    # ---- Auth / identity ----
    {"name": "Auth0", "category": "auth", "signals": [r"auth0\.com", r"cdn\.auth0\.com"]},
    {"name": "Okta", "category": "auth", "signals": [r"okta\.com", r"oktacdn\.com"]},
    {"name": "Firebase Auth", "category": "auth", "signals": ["firebaseauth", r"identitytoolkit\.googleapis\.com"]},
    {"name": "Clerk", "category": "auth", "signals": [r"clerk\.com", r"clerk\.dev", r"clerk\.js"]},
    {"name": "Supabase Auth", "category": "auth", "signals": [r"supabase\.co/auth", r"supabase\.io"]},

    # ---- Monitoring / APM / error tracking ----
    {"name": "Sentry", "category": "monitoring", "signals": [r"sentry\.io", r"browser\.sentry-cdn\.com", "sentry-"]},
    {"name": "Datadog", "category": "monitoring", "signals": [r"datadoghq\.com", "dd-rum", "datadog"]},
    {"name": "New Relic", "category": "monitoring", "signals": [r"newrelic\.com", r"nr-data\.net", r"bam\.nr-data\.net", "nreum"]},
    {"name": "LogRocket", "category": "monitoring", "signals": [r"logrocket\.com", r"cdn\.logrocket\.io"]},
    {"name": "Bugsnag", "category": "monitoring", "signals": [r"bugsnag\.com", r"d2wy8f7a9ursnm\.cloudfront\.net"]},
    {"name": "Raygun", "category": "monitoring", "signals": [r"raygun\.com", "raygun4js"]},
    {"name": "AppDynamics", "category": "monitoring", "signals": [r"appdynamics\.com", "adrum-"]},
    {"name": "Dynatrace", "category": "monitoring", "signals": [r"dynatrace\.com", r"dynatracelabs\.com", "ruxitagentjs"]},

    # ---- Web servers ----
    {"name": "nginx", "category": "web_server", "signals": ["nginx"], "scope": "header", "header_name": "server"},
    {"name": "Apache", "category": "web_server", "signals": ["apache"], "scope": "header", "header_name": "server"},
    {"name": "Microsoft-IIS", "category": "web_server", "signals": ["microsoft-iis"], "scope": "header", "header_name": "server"},
    {"name": "LiteSpeed", "category": "web_server", "signals": ["litespeed"], "scope": "header", "header_name": "server"},
    {"name": "Caddy", "category": "web_server", "signals": ["caddy"], "scope": "header", "header_name": "server"},
    {"name": "OpenResty", "category": "web_server", "signals": ["openresty"], "scope": "header", "header_name": "server"},
    {"name": "Gunicorn", "category": "web_server", "signals": ["gunicorn"], "scope": "header", "header_name": "server"},
    {"name": "Cowboy (Elixir/Phoenix)", "category": "web_server", "signals": ["cowboy"], "scope": "header", "header_name": "server"},
    {"name": "Envoy", "category": "web_server", "signals": ["envoy"], "scope": "header", "header_name": "server"},
    {"name": "Envoy", "category": "web_server", "signals": ["x-envoy-"], "scope": "header"},

    # ---- Server OS (from the Server header banner) ----
    {"name": "Ubuntu", "category": "os", "signals": [r"\(ubuntu\)"], "scope": "header", "header_name": "server"},
    {"name": "Debian", "category": "os", "signals": [r"\(debian\)"], "scope": "header", "header_name": "server"},
    {"name": "CentOS", "category": "os", "signals": [r"\(centos\)"], "scope": "header", "header_name": "server"},
    {"name": "Red Hat", "category": "os", "signals": [r"\(red ?hat\)"], "scope": "header", "header_name": "server"},
    {"name": "Windows Server", "category": "os", "signals": ["win(32|64)", r"\(windows\)"], "scope": "header", "header_name": "server"},
    {"name": "Amazon Linux", "category": "os", "signals": [r"\(amzn\)", "amazon linux"], "scope": "header", "header_name": "server"},
    {"name": "FreeBSD", "category": "os", "signals": [r"\(freebsd\)"], "scope": "header", "header_name": "server"},

    # ---- CDN / edge (header-scoped — see the note above) ----
    {"name": "Cloudflare", "category": "cdn", "signals": ["cf-ray"], "scope": "header"},
    {"name": "Cloudflare", "category": "cdn", "signals": ["cloudflare"], "scope": "header", "header_name": "server"},
    {"name": "Fastly", "category": "cdn", "signals": ["fastly-debug-digest", "x-fastly"], "scope": "header"},
    {"name": "Fastly", "category": "cdn", "signals": ["cache-"], "scope": "header", "header_name": "x-served-by"},
    {"name": "Akamai", "category": "cdn", "signals": ["x-akamai-", "akamai-transform"], "scope": "header"},
    {"name": "Akamai", "category": "cdn", "signals": [r"akamaized\.net"]},
    {"name": "AWS CloudFront", "category": "cdn", "signals": ["x-amz-cf-id", "x-amz-cf-pop"], "scope": "header"},
    {"name": "Vercel", "category": "cdn", "signals": ["x-vercel-"], "scope": "header"},
    {"name": "Vercel", "category": "cdn", "signals": ["vercel"], "scope": "header", "header_name": "server"},
    {"name": "Vercel", "category": "cdn", "signals": [r"vercel\.app"]},
    {"name": "Netlify", "category": "cdn", "signals": ["x-nf-request-id"], "scope": "header"},
    {"name": "Netlify", "category": "cdn", "signals": ["netlify"], "scope": "header", "header_name": "server"},
    {"name": "Netlify", "category": "cdn", "signals": [r"netlify\.app"]},
    {"name": "Sucuri", "category": "cdn", "signals": ["sucuri"], "scope": "header"},
    {"name": "Imperva (Incapsula)", "category": "cdn", "signals": ["incap_ses", "visid_incap"], "scope": "cookie"},
    {"name": "StackPath", "category": "cdn", "signals": ["stackpathcdn.com"]},
    {"name": "KeyCDN", "category": "cdn", "signals": ["kxcdn.com"]},
    {"name": "BunnyCDN", "category": "cdn", "signals": ["b-cdn.net"]},
    {"name": "Azure CDN", "category": "cdn", "signals": ["x-azure-ref"], "scope": "header"},
    {"name": "Azure CDN", "category": "cdn", "signals": ["x-msedge-ref"], "scope": "header"},
    {"name": "Google Cloud CDN", "category": "cdn", "signals": ["x-goog-meta-"], "scope": "header"},
    {"name": "Google Cloud CDN", "category": "cdn", "signals": ["x-goog-generation"], "scope": "header"},
    {"name": "Imperva (Incapsula)", "category": "cdn", "signals": ["imperva"], "scope": "header", "header_name": "x-cdn"},
    {"name": "Imperva (Incapsula)", "category": "cdn", "signals": ["x-iinfo"], "scope": "header"},

    # ---- Hosting (page-level hints; DNS/TLS is authoritative) ----
    {"name": "AWS", "category": "hosting", "signals": ["x-amz-request-id"], "scope": "header"},
    {"name": "AWS", "category": "hosting", "signals": ["x-amz-id-2"], "scope": "header"},
    {"name": "AWS", "category": "hosting", "signals": ["aws"], "scope": "header", "header_name": "server"},
    {"name": "Google Cloud", "category": "hosting", "signals": [r"run\.app"]},
    {"name": "Google Cloud", "category": "hosting", "signals": ["x-cloud-trace-context"], "scope": "header"},
    {"name": "Azure", "category": "hosting", "signals": ["x-azure-ref"], "scope": "header"},
    {"name": "Azure", "category": "hosting", "signals": ["x-ms-edge"], "scope": "header"},
    {"name": "Azure", "category": "hosting", "signals": ["x-ms-request-id"], "scope": "header"},
    {"name": "Heroku", "category": "hosting", "signals": [r"herokuapp\.com"]},
    {"name": "Heroku", "category": "hosting", "signals": ["heroku"], "scope": "header", "header_name": "via"},
    {"name": "DigitalOcean", "category": "hosting", "signals": [r"digitaloceanspaces\.com"]},
    {"name": "Render", "category": "hosting", "signals": [r"onrender\.com"]},
    {"name": "Render", "category": "hosting", "signals": ["render"], "scope": "header", "header_name": "server"},
    {"name": "Railway", "category": "hosting", "signals": [r"up\.railway\.app"]},
    {"name": "Fly.io", "category": "hosting", "signals": [r"fly\.dev"]},
    {"name": "Fly.io", "category": "hosting", "signals": ["fly-request-id"], "scope": "header"},
    {"name": "Hetzner", "category": "hosting", "signals": ["hetzner"], "scope": "header", "header_name": "server"},
    {"name": "WP Engine", "category": "hosting", "signals": [r"wpenginepowered\.com"]},
    {"name": "WP Engine", "category": "hosting", "signals": ["wp engine"], "scope": "header", "header_name": "x-powered-by"},
    {"name": "Pantheon", "category": "hosting", "signals": [r"pantheonsite\.io"]},
    {"name": "Pantheon", "category": "hosting", "signals": ["x-pantheon-"], "scope": "header"},

    # ---- Other ----
    {"name": "Stripe", "category": "payment_processor", "signals": [r"js\.stripe\.com", r"stripe\.com/v3", "stripe-js"]},
    {"name": "PayPal", "category": "payment_processor", "signals": [r"paypal\.com/sdk", r"paypalobjects\.com"]},
    {"name": "Braintree", "category": "payment_processor", "signals": [r"braintreegateway\.com", "braintree-api"]},
    {"name": "Square", "category": "payment_processor", "signals": [r"squareup\.com", r"square\.js"]},
    {"name": "Adyen", "category": "payment_processor", "signals": [r"adyen\.com", "checkoutshopper-"]},
]

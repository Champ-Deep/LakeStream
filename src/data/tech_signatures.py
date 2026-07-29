"""Technology detection signatures for identifying a site's tech stack.

Each signature has:
  - name: display name
  - category: one of cms, analytics, marketing, framework, cdn, js_library,
              hosting, backend, build_tool, font, payment, auth, database,
              monitoring, testing, a11y, search
  - signals: list of substrings to match in HTML source, script URLs, meta tags,
             or HTTP headers (case-insensitive)
  - header_signals (optional): match ONLY in headers (e.g. "x-powered-by: express")
  - meta_generator (optional): exact match on <meta name="generator" content="...">
"""

TECH_SIGNATURES: list[dict] = [
    # ===== CMS / Site Builders =====
    {
        "name": "WordPress",
        "category": "cms",
        "signals": ["wp-content", "wp-includes", "wp-json", "/wp-admin"],
        "meta_generator": "wordpress",
    },
    {
        "name": "HubSpot CMS",
        "category": "cms",
        "signals": ["js.hs-scripts.com", "hbspt", ".hs-menu", "hs-cta-wrapper"],
    },
    {"name": "Webflow", "category": "cms", "signals": ["webflow.com", "wf-page", "wf-section", "w-webflow-badge"]},
    {"name": "Drupal", "category": "cms", "signals": ["/sites/default/", "drupal.settings", "/core/misc/drupal.js"], "meta_generator": "drupal"},
    {"name": "Squarespace", "category": "cms", "signals": ["squarespace.com", "sqsp", "static.squarespace", "squarespace-cdn"]},
    {"name": "Wix", "category": "cms", "signals": ["wix.com", "wixsite.com", "parastorage.com", "wix-code-sdk"]},
    {"name": "Shopify", "category": "cms", "signals": ["cdn.shopify.com", "myshopify.com", "shopify-section"]},
    {"name": "Ghost", "category": "cms", "signals": ["ghost.io"]},
    {"name": "Contentful", "category": "cms", "signals": ["contentful.com", "ctfassets.net", "images.ctfassets.net"]},
    {"name": "Sitecore", "category": "cms", "signals": ["sitecore", "/sitecore/", "sc_site", "scItemId"]},
    {"name": "Adobe Experience Manager", "category": "cms", "signals": ["/content/dam/", "/etc.clientlibs/", "cq-page-", "/libs/granite/"], "meta_generator": "aem"},
    {"name": "Kentico", "category": "cms", "signals": ["kentico", "cmspageid", "CMSPreferredCulture"]},
    {"name": "Umbraco", "category": "cms", "signals": ["umbraco", "/umbraco/"], "meta_generator": "umbraco"},
    {"name": "Typo3", "category": "cms", "signals": ["typo3", "/typo3conf/", "/typo3temp/"], "meta_generator": "typo3"},
    {"name": "Joomla", "category": "cms", "signals": ["/media/jui/", "joomla"], "meta_generator": "joomla"},
    {"name": "Magento", "category": "cms", "signals": ["magento", "mage/cookies", "/static/version", "Magento_"], "header_signals": ["x-magento-"]},
    {"name": "BigCommerce", "category": "cms", "signals": ["bigcommerce.com", "cdn11.bigcommerce.com"]},
    {"name": "Prismic", "category": "cms", "signals": ["prismic.io", "cdn.prismic.io"]},
    {"name": "Sanity", "category": "cms", "signals": ["sanity.io", "cdn.sanity.io", "apicdn.sanity.io"]},
    {"name": "Strapi", "category": "cms", "signals": ["strapi"], "header_signals": ["x-powered-by: strapi"]},
    {"name": "Craft CMS", "category": "cms", "signals": ["craftcms.com"], "header_signals": ["x-powered-by: craft cms"]},
    {"name": "Storyblok", "category": "cms", "signals": ["storyblok.com", "a.storyblok.com"]},
    {"name": "DatoCMS", "category": "cms", "signals": ["datocms.com", "www.datocms-assets.com"]},

    # ===== Analytics =====
    {
        "name": "Google Analytics",
        "category": "analytics",
        "signals": ["google-analytics.com", "gtag(", "ga.js", "googletagmanager.com", "gtm.js", "gtm.start"],
    },
    {"name": "Google Tag Manager", "category": "analytics", "signals": ["googletagmanager.com/gtm.js", "gtm.start", "google_tag_manager"]},
    {"name": "Segment", "category": "analytics", "signals": ["cdn.segment.com", "analytics.js/v1/", "segment.io"]},
    {"name": "Mixpanel", "category": "analytics", "signals": ["mixpanel.com", "mixpanel.init", "cdn.mxpnl.com"]},
    {"name": "Amplitude", "category": "analytics", "signals": ["amplitude.com", "cdn.amplitude.com", "amplitude.getInstance"]},
    {"name": "Heap", "category": "analytics", "signals": ["heap-", "heapanalytics.com", "cdn.heapanalytics.com"]},
    {"name": "Hotjar", "category": "analytics", "signals": ["hotjar.com", "static.hotjar.com", "hj("]},
    {"name": "Plausible", "category": "analytics", "signals": ["plausible.io/js/", "cdn.plausible.io"]},
    {"name": "Matomo", "category": "analytics", "signals": ["matomo.js", "matomo.php", "piwik.js"]},
    {"name": "Fathom", "category": "analytics", "signals": ["usefathom.com", "cdn.usefathom.com"]},
    {"name": "PostHog", "category": "analytics", "signals": ["posthog.com", "app.posthog.com", "posthog.init"]},
    {"name": "FullStory", "category": "analytics", "signals": ["fullstory.com", "fullstory.js", "edge.fullstory.com"]},
    {"name": "Clarity", "category": "analytics", "signals": ["clarity.ms", "microsoft clarity"]},
    {"name": "Adobe Analytics", "category": "analytics", "signals": ["omniture.com", "sc.omtrdc.net", "demdex.net", "adobedtm.com", "AppMeasurement"]},
    {"name": "Pendo", "category": "analytics", "signals": ["pendo.io", "cdn.pendo.io", "pendo-"]},
    {"name": "Snowplow", "category": "analytics", "signals": ["snowplow", "sp.js", "snowplowanalytics"]},
    {"name": "Countly", "category": "analytics", "signals": ["countly", "cly.js"]},
    {"name": "Kissmetrics", "category": "analytics", "signals": ["kissmetrics.com", "kmq.push"]},

    # ===== Marketing Automation =====
    {"name": "Marketo", "category": "marketing", "signals": ["munchkin.marketo.net", "mktoforms", "marketo.com"]},
    {"name": "Pardot", "category": "marketing", "signals": ["pardot.com", "pi.pardot.com", "go.pardot.com"]},
    {"name": "Drift", "category": "marketing", "signals": ["drift.com", "driftt.com", "js.driftt.com"]},
    {"name": "Intercom", "category": "marketing", "signals": ["intercom.io", "intercomsettings", "widget.intercom.io"]},
    {"name": "HubSpot Marketing", "category": "marketing", "signals": ["js.hs-analytics.net", "forms.hubspot.com", "js.hsforms.net"]},
    {"name": "Mailchimp", "category": "marketing", "signals": ["mailchimp.com", "list-manage.com", "chimpstatic.com"]},
    {"name": "ActiveCampaign", "category": "marketing", "signals": ["activecampaign.com", "trackcmp.net"]},
    {"name": "Salesforce", "category": "marketing", "signals": ["salesforce.com", "force.com", "pardot.com"]},
    {"name": "ZoomInfo", "category": "marketing", "signals": ["zoominfo.com", "ws.zoominfo.com"]},
    {"name": "6sense", "category": "marketing", "signals": ["6sense.com", "j.6sc.co"]},
    {"name": "Clearbit", "category": "marketing", "signals": ["clearbit.com", "x.clearbitjs.com"]},
    {"name": "Optimizely", "category": "marketing", "signals": ["optimizely.com", "cdn.optimizely.com", "optimizelyEndUserId"]},
    {"name": "VWO", "category": "marketing", "signals": ["visualwebsiteoptimizer.com", "vwo_", "dev.visualwebsiteoptimizer"]},
    {"name": "Crazy Egg", "category": "marketing", "signals": ["crazyegg.com", "script.crazyegg.com"]},
    {"name": "Braze", "category": "marketing", "signals": ["braze.com", "sdk.iad-03.braze.com"]},
    {"name": "Iterable", "category": "marketing", "signals": ["iterable.com", "js.iterable.com"]},
    {"name": "Customer.io", "category": "marketing", "signals": ["customer.io", "track.customer.io"]},
    {"name": "Crisp", "category": "marketing", "signals": ["crisp.chat", "client.crisp.chat"]},
    {"name": "Zendesk", "category": "marketing", "signals": ["zendesk.com", "zdassets.com", "zopim.com"]},
    {"name": "Freshdesk", "category": "marketing", "signals": ["freshdesk.com", "freshchat.com"]},
    {"name": "LiveChat", "category": "marketing", "signals": ["livechatinc.com", "cdn.livechatinc.com"]},
    {"name": "Tawk.to", "category": "marketing", "signals": ["tawk.to", "embed.tawk.to"]},
    {"name": "Olark", "category": "marketing", "signals": ["olark.com", "static.olark.com"]},
    {"name": "Cookie consent tools", "category": "marketing", "signals": ["cookiebot.com", "onetrust.com", "cookieconsent", "trustarc.com", "osano.com"]},

    # ===== JS Frameworks =====
    {
        "name": "React",
        "category": "framework",
        "signals": ["react.production.min", "react-dom", "__reactfiber", "__reactinternalinstance", "data-reactroot", "data-reactid"],
    },
    {"name": "Vue.js", "category": "framework", "signals": ["vue.runtime", "__vue__", "v-if=", "vue.global", "vue.esm", "data-v-"]},
    {"name": "Angular", "category": "framework", "signals": ["angular.min", "ng-version", "ng-app", "angular.io", "ng-reflect-"]},
    {"name": "Next.js", "category": "framework", "signals": ["__next_data__", "_next/static", "next/dist"], "meta_generator": "next.js"},
    {"name": "Gatsby", "category": "framework", "signals": ["gatsby-", "/page-data/", "gatsby-image", "gatsby-chunk"], "meta_generator": "gatsby"},
    {"name": "Nuxt", "category": "framework", "signals": ["__nuxt", "_nuxt/", "nuxt.js"]},
    {"name": "Svelte", "category": "framework", "signals": ["svelte", "__svelte", ".svelte-"]},
    {"name": "SvelteKit", "category": "framework", "signals": ["__sveltekit", "sveltekit:"]},
    {"name": "Remix", "category": "framework", "signals": ["__remix", "remix.run", "__remixContext"]},
    {"name": "Astro", "category": "framework", "signals": ["astro-island", "astro-slot", "astro-"]},
    {"name": "Ember.js", "category": "framework", "signals": ["ember.js", "ember-view", "ember-application"]},
    {"name": "Backbone.js", "category": "framework", "signals": ["backbone.js", "backbone.min"]},
    {"name": "Alpine.js", "category": "framework", "signals": ["alpine.js", "x-data=", "x-init=", "@click."]},
    {"name": "HTMX", "category": "framework", "signals": ["htmx.org", "hx-get", "hx-post", "hx-trigger"]},
    {"name": "Stimulus", "category": "framework", "signals": ["stimulus.js", "data-controller=", "data-action="]},
    {"name": "Turbo", "category": "framework", "signals": ["turbo.js", "turbo-frame", "turbo-stream"]},
    {"name": "Preact", "category": "framework", "signals": ["preact", "preact.min"]},
    {"name": "Solid.js", "category": "framework", "signals": ["solid-js", "_$createComponent"]},
    {"name": "Qwik", "category": "framework", "signals": ["qwik", "qwikloader"]},
    {"name": "Lit", "category": "framework", "signals": ["lit-html", "lit-element", "@lit/"]},
    {"name": "Stencil", "category": "framework", "signals": ["stencil", "/build/app.esm.js"]},

    # ===== CDN / Edge =====
    {"name": "Cloudflare", "category": "cdn", "signals": [], "header_signals": ["cf-ray", "server: cloudflare"]},
    

    {"name": "Fastly", "category": "cdn", "signals": [], "header_signals": ["x-served-by: cache-", "fastly-debug-digest"]},
    {"name": "Akamai", "category": "cdn", "signals": ["akamaized.net"], "header_signals": ["x-akamai-", "akamai-transform"]},
    {"name": "AWS CloudFront", "category": "cdn", "signals": [], "header_signals": ["x-amz-cf-id", "x-amz-cf-pop"]},
    {"name": "Vercel", "category": "cdn", "signals": ["vercel.app", "vercel.com"], "header_signals": ["x-vercel-", "server: vercel"]},
    {"name": "Netlify", "category": "cdn", "signals": ["netlify.app", "netlify.com"], "header_signals": ["x-nf-request-id", "server: netlify"]},
    {"name": "Azure CDN", "category": "cdn", "signals": [], "header_signals": ["x-azure-ref", "x-msedge-ref"]},
    {"name": "Google Cloud CDN", "category": "cdn", "signals": ["googleusercontent.com"], "header_signals": ["x-goog-meta-", "x-goog-generation"]},
    {"name": "KeyCDN", "category": "cdn", "signals": ["kxcdn.com", "keycdn.com"]},
    {"name": "StackPath", "category": "cdn", "signals": ["stackpath.com", "stackpathcdn.com"]},
    {"name": "Imperva/Incapsula", "category": "cdn", "signals": ["incapsula", "imperva"], "header_signals": ["x-cdn: imperva", "x-iinfo"]},

    # ===== Hosting / Cloud =====
    {"name": "AWS", "category": "hosting", "signals": [], "header_signals": ["x-amz-request-id", "x-amz-id-2", "server: aws"]},
    {"name": "Google Cloud", "category": "hosting", "signals": ["cloud.google.com", "appspot.com", "run.app"], "header_signals": ["x-cloud-trace-context"]},
    {"name": "Azure", "category": "hosting", "signals": [], "header_signals": ["x-azure-ref", "x-ms-edge", "x-ms-request-id"]},
    {"name": "Heroku", "category": "hosting", "signals": ["herokuapp.com"], "header_signals": ["via: heroku"]},
    {"name": "DigitalOcean", "category": "hosting", "signals": ["digitalocean.com", "digitaloceanspaces.com"]},
    {"name": "Render", "category": "hosting", "signals": ["onrender.com"], "header_signals": ["server: render"]},
    {"name": "Railway", "category": "hosting", "signals": ["railway.app", "up.railway.app"]},
    {"name": "Fly.io", "category": "hosting", "signals": ["fly.dev", "fly.io"], "header_signals": ["fly-request-id"]},
    {"name": "Hetzner", "category": "hosting", "signals": ["hetzner.com"], "header_signals": ["server: hetzner"]},
    {"name": "WP Engine", "category": "hosting", "signals": ["wpengine.com", "wpenginepowered.com"], "header_signals": ["x-powered-by: wp engine"]},
    {"name": "Pantheon", "category": "hosting", "signals": ["pantheonsite.io", "pantheon.io"], "header_signals": ["x-pantheon-"]},

    # ===== Backend / Server =====
    {"name": "Node.js", "category": "backend", "signals": [], "header_signals": ["x-powered-by: express", "x-powered-by: next.js"]},
    {"name": "Express", "category": "backend", "signals": [], "header_signals": ["x-powered-by: express"]},
    {"name": "PHP", "category": "backend", "signals": [], "header_signals": ["x-powered-by: php"]},
    {"name": "ASP.NET", "category": "backend", "signals": ["__viewstate", "__dopostback"], "header_signals": ["x-powered-by: asp.net", "x-aspnet-version", "server: microsoft-iis"]},

    {"name": "Java/Spring", "category": "backend", "signals": [".jsp", "jsessionid"], "header_signals": ["x-powered-by: servlet", "server: apache-coyote"]},
    {"name": "Ruby on Rails", "category": "backend", "signals": ["data-turbo-track", "turbo-frame"], "header_signals": ["x-powered-by: phusion passenger"]},
    # ponytail: no header_signals — csrftoken cookie name collides with PHP csrfToken; csrfmiddlewaretoken HTML signal is the reliable one
    {"name": "Django", "category": "backend", "signals": ["csrfmiddlewaretoken", "django"], "header_signals": []},
    {"name": "Laravel", "category": "backend", "signals": ["laravel", "csrf_token"], "header_signals": ["x-powered-by: laravel", "set-cookie: laravel_session"]},
    {"name": "Nginx", "category": "backend", "signals": [], "header_signals": ["server: nginx"]},
    {"name": "Apache", "category": "backend", "signals": [], "header_signals": ["server: apache"]},
    {"name": "Envoy", "category": "backend", "signals": [], "header_signals": ["server: envoy", "x-envoy-"]},
    {"name": "GraphQL", "category": "backend", "signals": ["graphql", "/graphql", "__graphql"]},

    # ===== JS Libraries =====
    {"name": "jQuery", "category": "js_library", "signals": ["jquery.min.js", "jquery-", "jquery/"]},
    {"name": "Bootstrap", "category": "js_library", "signals": ["bootstrap.min", "bootstrap.css", "bootstrap.bundle"]},
    {"name": "Tailwind CSS", "category": "js_library", "signals": ["tailwindcss", "tailwind.css"]},
    {"name": "Lodash", "category": "js_library", "signals": ["lodash.min", "lodash.js"]},
    {"name": "Moment.js", "category": "js_library", "signals": ["moment.min", "moment.js"]},
    {"name": "D3.js", "category": "js_library", "signals": ["d3.min", "d3.js", "d3.v"]},
    {"name": "Three.js", "category": "js_library", "signals": ["three.min", "three.js"]},
    {"name": "GSAP", "category": "js_library", "signals": ["gsap.min", "gsap.js", "greensock"]},
    {"name": "Axios", "category": "js_library", "signals": ["axios.min", "axios/"]},
    {"name": "Socket.io", "category": "js_library", "signals": ["socket.io", "socket.io.min"]},
    {"name": "Chart.js", "category": "js_library", "signals": ["chart.min", "chart.js", "chartjs"]},
    {"name": "Swiper", "category": "js_library", "signals": ["swiper-bundle", "swiper.min", "swiper.js"]},
    {"name": "Lottie", "category": "js_library", "signals": ["lottie", "lottie-player", "lottie.min"]},
    {"name": "Framer Motion", "category": "js_library", "signals": ["framer-motion", "framer.com"]},
    {"name": "Mapbox", "category": "js_library", "signals": ["mapbox-gl", "api.mapbox.com"]},
    {"name": "Leaflet", "category": "js_library", "signals": ["leaflet.js", "leaflet.css"]},
    {"name": "Highcharts", "category": "js_library", "signals": ["highcharts.com", "highcharts.js"]},

    # ===== Build Tools (visible in source) =====
    {"name": "Webpack", "category": "build_tool", "signals": ["webpackjsonp", "__webpack_require__", "webpack-"]},
    {"name": "Vite", "category": "build_tool", "signals": ["/@vite/", "vite/modulepreload-polyfill"]},
    {"name": "Parcel", "category": "build_tool", "signals": ["parcelrequire", "__parcel__"]},
    {"name": "Turbopack", "category": "build_tool", "signals": ["turbopack", "__turbopack__"]},
    {"name": "esbuild", "category": "build_tool", "signals": ["esbuild"]},

    # ===== Fonts =====
    {"name": "Google Fonts", "category": "font", "signals": ["fonts.googleapis.com", "fonts.gstatic.com"]},
    {"name": "Adobe Fonts", "category": "font", "signals": ["use.typekit.net", "p.typekit.net"]},
    {"name": "Font Awesome", "category": "font", "signals": ["fontawesome", "font-awesome"]},

    # ===== Payment =====
    {"name": "Stripe", "category": "payment", "signals": ["js.stripe.com", "stripe.com/v3", "stripe-js"]},
    {"name": "PayPal", "category": "payment", "signals": ["paypal.com/sdk", "paypalobjects.com"]},
    {"name": "Braintree", "category": "payment", "signals": ["braintreegateway.com", "braintree-api"]},
    {"name": "Square", "category": "payment", "signals": ["squareup.com", "square.js"]},
    {"name": "Adyen", "category": "payment", "signals": ["adyen.com", "checkoutshopper-"]},

    # ===== Auth =====
    {"name": "Auth0", "category": "auth", "signals": ["auth0.com", "cdn.auth0.com"]},
    {"name": "Okta", "category": "auth", "signals": ["okta.com", "oktacdn.com"]},
    {"name": "Firebase Auth", "category": "auth", "signals": ["firebaseauth", "identitytoolkit.googleapis.com"]},
    {"name": "Clerk", "category": "auth", "signals": ["clerk.com", "clerk.dev", "clerk.js"]},
    {"name": "Supabase Auth", "category": "auth", "signals": ["supabase.co/auth", "supabase.io"]},

    # ===== Monitoring / Error Tracking =====
    {"name": "Sentry", "category": "monitoring", "signals": ["sentry.io", "browser.sentry-cdn.com", "sentry-"]},
    {"name": "Datadog", "category": "monitoring", "signals": ["datadoghq.com", "dd-rum", "datadog"]},
    {"name": "New Relic", "category": "monitoring", "signals": ["newrelic.com", "nr-data.net", "bam.nr-data.net", "nreum"]},
    {"name": "LogRocket", "category": "monitoring", "signals": ["logrocket.com", "cdn.logrocket.io"]},
    {"name": "Bugsnag", "category": "monitoring", "signals": ["bugsnag.com", "d2wy8f7a9ursnm.cloudfront.net"]},
    {"name": "Raygun", "category": "monitoring", "signals": ["raygun.com", "raygun4js"]},
    {"name": "AppDynamics", "category": "monitoring", "signals": ["appdynamics.com", "adrum-"]},
    {"name": "Dynatrace", "category": "monitoring", "signals": ["dynatrace.com", "dynatracelabs.com", "ruxitagentjs"]},

    # ===== Search =====
    {"name": "Algolia", "category": "search", "signals": ["algolia.com", "algolianet.com", "algoliasearch"]},
    {"name": "Elasticsearch", "category": "search", "signals": ["elasticsearch", "_msearch"]},
    {"name": "Typesense", "category": "search", "signals": ["typesense", "cloud.typesense.org"]},
    {"name": "Meilisearch", "category": "search", "signals": ["meilisearch"]},

    # ===== A/B Testing =====
    {"name": "LaunchDarkly", "category": "ab_testing", "signals": ["launchdarkly.com", "app.launchdarkly.com"]},
    {"name": "Split.io", "category": "ab_testing", "signals": ["split.io", "cdn.split.io"]},
    {"name": "Statsig", "category": "ab_testing", "signals": ["statsig.com", "featuregates.org"]},

    # ===== Tag Managers =====
    {"name": "Tealium", "category": "tag_manager", "signals": ["tealium.com", "tags.tiqcdn.com", "tealiumiq.com"]},
    {"name": "Ensighten", "category": "tag_manager", "signals": ["ensighten.com", "nexus.ensighten.com"]},

    # ===== Video =====
    {"name": "YouTube Embed", "category": "video", "signals": ["youtube.com/embed", "youtube-nocookie.com"]},
    {"name": "Vimeo", "category": "video", "signals": ["player.vimeo.com", "vimeo.com"]},
    {"name": "Wistia", "category": "video", "signals": ["wistia.com", "fast.wistia.com", "wistia-"]},
    {"name": "Vidyard", "category": "video", "signals": ["vidyard.com", "play.vidyard.com"]},

    # ===== E-commerce =====
    {"name": "WooCommerce", "category": "ecommerce", "signals": ["woocommerce", "wc-cart-fragments", "wc-ajax=", "wp-content/plugins/woocommerce"]},
    {"name": "PrestaShop", "category": "ecommerce", "signals": ["prestashop", "/modules/ps_"]},
    {"name": "OpenCart", "category": "ecommerce", "signals": ["opencart", "route=common/"]},
    {"name": "Salesforce Commerce Cloud", "category": "ecommerce", "signals": ["demandware.net", "sfcc", "demandware.static"]},
    {"name": "SAP Commerce", "category": "ecommerce", "signals": ["hybris", "sap-commerce", "/_ui/"]},

    # ===== Accessibility =====
    {"name": "AccessiBe", "category": "a11y", "signals": ["accessibe.com", "acsbapp.com"]},
    {"name": "UserWay", "category": "a11y", "signals": ["userway.org", "cdn.userway.org"]},
    {"name": "AudioEye", "category": "a11y", "signals": ["audioeye.com", "cdn.audioeye.com"]},
]

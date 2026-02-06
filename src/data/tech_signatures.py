"""Technology detection signatures for identifying a site's tech stack."""

TECH_SIGNATURES: list[dict] = [
    # CMS
    {
        "name": "WordPress",
        "category": "cms",
        "signals": ["wp-content", "wp-includes", "wordpress", "wp-json"],
    },
    {
        "name": "HubSpot",
        "category": "cms",
        "signals": ["js.hs-scripts.com", "hubspot", ".hs-", "hbspt"],
    },
    {"name": "Webflow", "category": "cms", "signals": ["webflow.com", "wf-page", "wf-section"]},
    {"name": "Drupal", "category": "cms", "signals": ["/sites/default/", "drupal.settings"]},
    {
        "name": "Squarespace",
        "category": "cms",
        "signals": ["squarespace.com", "sqsp", "static.squarespace"],
    },
    {"name": "Wix", "category": "cms", "signals": ["wix.com", "wixsite.com", "parastorage.com"]},
    {
        "name": "Shopify",
        "category": "cms",
        "signals": ["cdn.shopify.com", "shopify", "myshopify.com"],
    },
    {"name": "Ghost", "category": "cms", "signals": ["ghost.io", "ghost-", "content/themes"]},
    {"name": "Contentful", "category": "cms", "signals": ["contentful.com", "ctfassets.net"]},
    # Analytics
    {
        "name": "Google Analytics",
        "category": "analytics",
        "signals": ["google-analytics.com", "gtag(", "ga.js", "googletagmanager.com"],
    },
    {
        "name": "Segment",
        "category": "analytics",
        "signals": ["cdn.segment.com", "analytics.js", "segment.io"],
    },
    {"name": "Mixpanel", "category": "analytics", "signals": ["mixpanel.com", "mixpanel.init"]},
    {
        "name": "Amplitude",
        "category": "analytics",
        "signals": ["amplitude.com", "cdn.amplitude.com"],
    },
    {"name": "Heap", "category": "analytics", "signals": ["heap-", "heapanalytics.com"]},
    {"name": "Hotjar", "category": "analytics", "signals": ["hotjar.com", "static.hotjar.com"]},
    {"name": "Plausible", "category": "analytics", "signals": ["plausible.io"]},
    # Marketing Automation
    {"name": "Marketo", "category": "marketing", "signals": ["munchkin.marketo.net", "mktoforms"]},
    {
        "name": "Pardot",
        "category": "marketing",
        "signals": ["pardot.com", "pi.pardot.com", "go.pardot.com"],
    },
    {
        "name": "Drift",
        "category": "marketing",
        "signals": ["drift.com", "driftt.com", "js.driftt.com"],
    },
    {
        "name": "Intercom",
        "category": "marketing",
        "signals": ["intercom.io", "intercomsettings", "widget.intercom.io"],
    },
    {
        "name": "HubSpot Marketing",
        "category": "marketing",
        "signals": ["js.hs-analytics.net", "forms.hubspot.com"],
    },
    {
        "name": "Mailchimp",
        "category": "marketing",
        "signals": ["mailchimp.com", "list-manage.com", "chimpstatic.com"],
    },
    {
        "name": "ActiveCampaign",
        "category": "marketing",
        "signals": ["activecampaign.com", "trackcmp.net"],
    },
    {"name": "Salesforce", "category": "marketing", "signals": ["salesforce.com", "force.com"]},
    {"name": "ZoomInfo", "category": "marketing", "signals": ["zoominfo.com", "ws.zoominfo.com"]},
    {"name": "6sense", "category": "marketing", "signals": ["6sense.com", "j.6sc.co"]},
    {"name": "Clearbit", "category": "marketing", "signals": ["clearbit.com", "x.clearbitjs.com"]},
    # JS Frameworks
    {
        "name": "React",
        "category": "framework",
        "signals": ["react.", "reactdom", "__next_data__", "_next/"],
    },
    {"name": "Vue.js", "category": "framework", "signals": ["vue.js", "__vue__", "v-if=", "vuejs"]},
    {"name": "Angular", "category": "framework", "signals": ["angular", "ng-version", "ng-app"]},
    {
        "name": "Next.js",
        "category": "framework",
        "signals": ["__next_data__", "_next/static", "next/dist"],
    },
    {"name": "Gatsby", "category": "framework", "signals": ["gatsby", "/page-data/"]},
    {"name": "Nuxt", "category": "framework", "signals": ["__nuxt", "nuxt.js"]},
    {"name": "Svelte", "category": "framework", "signals": ["svelte", "__svelte"]},
    # CDN
    {"name": "Cloudflare", "category": "cdn", "signals": ["cf-ray", "cloudflare"]},
    {"name": "Fastly", "category": "cdn", "signals": ["fastly", "x-served-by"]},
    {"name": "Akamai", "category": "cdn", "signals": ["akamai", "akamaitech"]},
    {"name": "AWS CloudFront", "category": "cdn", "signals": ["cloudfront.net", "x-amz-cf"]},
    {"name": "Vercel", "category": "cdn", "signals": ["vercel", "x-vercel-"]},
    {"name": "Netlify", "category": "cdn", "signals": ["netlify", "x-nf-request-id"]},
    # JS Libraries
    {"name": "jQuery", "category": "js_library", "signals": ["jquery", "jquery.min.js"]},
    {"name": "Bootstrap", "category": "js_library", "signals": ["bootstrap.min", "bootstrap.css"]},
    {"name": "Tailwind CSS", "category": "js_library", "signals": ["tailwindcss", "tailwind."]},
    {"name": "Lodash", "category": "js_library", "signals": ["lodash", "lodash.min"]},
]

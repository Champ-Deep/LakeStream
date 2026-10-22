#!/usr/bin/env python3
"""Validate tech detection accuracy by cross-checking detections against raw HTML."""
import asyncio
import csv
import re
import httpx
from collections import defaultdict
from src.data.tech_signatures import TECH_SIGNATURES
from src.scraping.parser.tech_engine import (
    detect,
    detections_to_metadata,
    extract_page_signals,
)

XLSX_PATH = "/Users/deep/Downloads/100-200 Test.xlsx"
RESULTS_CSV = "/Users/deep/Downloads/tech_stack_results.csv"
VALIDATION_CSV = "/Users/deep/Downloads/tech_stack_validation.csv"

CONCURRENT = 4
TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}

semaphore = asyncio.Semaphore(CONCURRENT)


async def fetch(client: httpx.AsyncClient, domain: str):
    async with semaphore:
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}"
        try:
            resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=TIMEOUT)
            return resp.text, dict(resp.headers), None
        except Exception as e:
            return "", {}, repr(e)


def validate_detection(detection_name: str, html: str, headers: dict) -> dict:
    """Cross-check a single detection for plausibility."""
    html_lower = html.lower()
    headers_lower = {k.lower(): v.lower() for k, v in headers.items()}

    result = {
        "name": detection_name,
        "signal_match": False,
        "independent_check": "unknown",
        "notes": "",
    }

    # Build independent ground-truth checks per technology
    checks = {
        "WordPress": lambda: "wp-content" in html_lower or "wp-includes" in html_lower,
        "Drupal": lambda: "/sites/default/" in html_lower or "drupal" in html_lower,
        "Joomla": lambda: "joomla" in html_lower or "/media/jui/" in html_lower,
        "Typo3": lambda: "typo3" in html_lower,
        "Wix": lambda: "wix" in html_lower or "parastorage" in html_lower,
        "Shopify": lambda: "cdn.shopify.com" in html_lower or "myshopify" in html_lower,
        "Squarespace": lambda: "squarespace" in html_lower,
        "Ghost": lambda: "ghost.io" in html_lower,
        "Webflow": lambda: "webflow" in html_lower,
        "HubSpot CMS": lambda: "hs-scripts.com" in html_lower or "hubspot" in html_lower,
        "Cloudflare": lambda: "cf-ray" in headers_lower or "cf-cache-status" in headers_lower or "cloudflare" in headers_lower.get("server", "") or "cdnjs.cloudflare.com" in html_lower,
        "Fastly": lambda: "x-served-by" in headers_lower and "cache-" in headers_lower.get("x-served-by", ""),
        "Akamai": lambda: "akamai" in headers_lower.get("x-cache", "") or "akamai" in str(headers_lower),
        "AWS CloudFront": lambda: "x-amz-cf-id" in headers_lower,
        "Vercel": lambda: "vercel" in headers_lower.get("x-vercel-id", ""),
        "Netlify": lambda: "netlify" in headers_lower.get("server", "") or "x-nf-request-id" in headers_lower,
        "Nginx": lambda: "nginx" in headers_lower.get("server", ""),
        "Apache": lambda: "apache" in headers_lower.get("server", ""),
        "PHP": lambda: "php" in headers_lower.get("x-powered-by", "") or "php" in headers_lower.get("server", ""),
        "Node.js": lambda: "express" in headers_lower.get("x-powered-by", ""),
        "Ruby on Rails": lambda: "x-wix-request-id" not in headers_lower and ("rails" in html_lower or "phusion" in html_lower),
        "jQuery": lambda: "jquery" in html_lower,
        "Bootstrap": lambda: "bootstrap" in html_lower,
        "React": lambda: "react" in html_lower or "reactdom" in html_lower,
        "Next.js": lambda: "_next/static" in html_lower or "__next_data__" in html_lower,
        "Vue.js": lambda: "vue" in html_lower or "vuejs" in html_lower,
        "Angular": lambda: "ng-" in html_lower or "angular" in html_lower,
        "Google Analytics": lambda: "google-analytics.com" in html_lower or "gtag" in html_lower or "googletagmanager.com" in html_lower,
        "Google Tag Manager": lambda: "googletagmanager.com/gtm.js" in html_lower or "gtm.start" in html_lower,
        "Google Fonts": lambda: "fonts.googleapis.com" in html_lower or "fonts.gstatic.com" in html_lower,
        "Font Awesome": lambda: "fontawesome" in html_lower or "font-awesome" in html_lower,
        "Stripe": lambda: "stripe.com" in html_lower or "js.stripe.com" in html_lower,
        "PayPal": lambda: "paypal" in html_lower and ("objects" in html_lower or "sdk" in html_lower),
        "Sentry": lambda: "sentry" in html_lower,
        "Matomo": lambda: "matomo" in html_lower or "piwik" in html_lower,
        "Plausible": lambda: "plausible.io" in html_lower,
        "Hotjar": lambda: "hotjar" in html_lower,
        "Intercom": lambda: "intercom" in html_lower,
        "HubSpot Marketing": lambda: "hs-analytics" in html_lower or "hsforms" in html_lower,
        "Mailchimp": lambda: "chimpstatic" in html_lower or "list-manage" in html_lower,
        "Segment": lambda: "segment.com" in html_lower or "cdn.segment" in html_lower,
        "Mixpanel": lambda: "mixpanel" in html_lower,
        "PostHog": lambda: "posthog" in html_lower,
        "YouTube Embed": lambda: "youtube.com/embed" in html_lower,
        "Cookie consent tools": lambda: "cookieconsent" in html_lower or "onetrust" in html_lower or "osano" in html_lower,
        "Webpack": lambda: "webpack" in html_lower,
        "Google Cloud CDN": lambda: "googleusercontent.com" in html_lower,
        "Azure": lambda: "azure" in html_lower or "azureedge" in html_lower or "windows.net" in html_lower,
        "AWS": lambda: "amazonaws.com" in html_lower,
        "Lodash": lambda: "lodash" in html_lower,
        "Moment.js": lambda: "moment.min" in html_lower or "moment.js" in html_lower,
        "Masonry": lambda: "masonry" in html_lower,
        "Swiper": lambda: "swiper-bundle" in html_lower or "swiper.min.js" in html_lower,
        "WooCommerce": lambda: "woocommerce" in html_lower,
        "Google Cloud": lambda: False,  # hard to verify without headers
        "Heroku": lambda: "herokuapp" in html_lower,
        "Varnish": lambda: "varnish" in headers_lower.get("x-varnish", ""),
        "IIS": lambda: "iis" in headers_lower.get("server", "") or "microsoft-iis" in headers_lower.get("server", ""),
        "ASP.NET": lambda: ("x-powered-by" in headers_lower and "asp.net" in headers_lower.get("x-powered-by", "")) or "__viewstate" in html_lower or "__dopostback" in html_lower or ("server" in headers_lower and "microsoft-iis" in headers_lower.get("server", "")),
        "Django": lambda: "django" in html_lower or ("csrftoken" in html_lower and "csrfmiddlewaretoken" in html_lower),
        "Laravel": lambda: "laravel" in html_lower,
        "Vite": lambda: "vite" in html_lower,
        "Lottie": lambda: "lottie" in html_lower,
        "Tailwind CSS": lambda: "tailwind" in html_lower,
        "GSAP": lambda: "gsap" in html_lower or "tweenmax" in html_lower,
        "Alpine.js": lambda: "alpine" in html_lower,
        "UserWay": lambda: "userway" in html_lower,
        "AccessiBe": lambda: "accessibe" in html_lower,
        "AudioEye": lambda: "audioeye" in html_lower,
        "Datadog": lambda: "datadog" in html_lower,
        "New Relic": lambda: "newrelic" in html_lower or "nr-data" in html_lower,
        "Bugsnag": lambda: "bugsnag" in html_lower,
        "LogRocket": lambda: "logrocket" in html_lower,
        "FullStory": lambda: "fullstory" in html_lower,
        "Clarity": lambda: "clarity.ms" in html_lower,
        "Adobe Analytics": lambda: "adobedtm" in html_lower or "omniture" in html_lower or "demdex" in html_lower,
        "Adobe Fonts": lambda: "typekit" in html_lower or "use.typekit" in html_lower,
        "Optimizely": lambda: "optimizely" in html_lower,
        "VWO": lambda: "vwo" in html_lower or "visualwebsiteoptimizer" in html_lower,
        "Tawk.to": lambda: "tawk.to" in html_lower or "tawk" in html_lower,
        "Drift": lambda: "drift.com" in html_lower or "driftt.com" in html_lower,
        "Pendo": lambda: "pendo.io" in html_lower,
        "Salesforce": lambda: "salesforce" in html_lower or "force.com" in html_lower,
        "Zendesk": lambda: "zendesk" in html_lower or "zdassets" in html_lower,
    }

    check_fn = checks.get(detection_name)
    if check_fn:
        try:
            independent = check_fn()
            result["independent_check"] = "confirmed" if independent else "NOT_FOUND"
        except Exception as e:
            result["independent_check"] = f"error: {e}"
    else:
        result["independent_check"] = "no_check"
        result["notes"] = "no validation rule"

    return result


async def main():
    # Read the existing results
    with open(RESULTS_CSV) as f:
        reader = csv.DictReader(f)
        results = list(reader)

    # Get domains with OK status
    ok_domains = [r for r in results if r["status"] == "OK"]
    print(f"Domains with OK status: {len(ok_domains)}")

    # Re-fetch for validation
    print("Re-fetching domains for validation...\n")

    async with httpx.AsyncClient() as client:
        tasks = [fetch(client, r["domain"]) for r in ok_domains]
        fetched = await asyncio.gather(*tasks)

    # Validate each detection
    validation_rows = []
    stats = defaultdict(lambda: defaultdict(int))  # tech -> {confirmed, not_found, no_check}

    for (row, (html, headers, error)) in zip(ok_domains, fetched):
        if error or not html or len(html) < 200:
            continue

        # Re-run the catalog engine to get fresh detections
        result = detections_to_metadata(
            detect(extract_page_signals(html, headers=headers))
        )

        for det in result.get("detections", []):
            name = det["name"]
            confidence = det["confidence"]
            evidence_type = det["evidence_type"]

            # Run independent check
            validation = validate_detection(name, html, headers)

            stats[name][validation["independent_check"]] += 1
            stats[name][f"total"] += 1

            validation_rows.append({
                "domain": row["domain"],
                "company": row["company"],
                "platform": row["platform"],
                "tech_detected": name,
                "confidence": confidence,
                "evidence_type": det["evidence_type"],
                "evidence": det["evidence"][:100],
                "independent_check": validation["independent_check"],
                "notes": validation["notes"],
            })

    # Write validation CSV
    fieldnames = ["domain", "company", "platform", "tech_detected", "confidence",
                  "evidence_type", "evidence", "independent_check", "notes"]
    with open(VALIDATION_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(validation_rows)

    # Accuracy analysis
    print("=" * 80)
    print("VALIDATION RESULTS")
    print("=" * 80)

    confirmed = sum(v.get("confirmed", 0) for v in stats.values())
    not_found = sum(v.get("NOT_FOUND", 0) for v in stats.values())
    no_check = sum(v.get("no_check", 0) for v in stats.values())
    total = sum(v.get("total", 0) for v in stats.values())

    print(f"\nTotal detections validated: {total}")
    print(f"  Confirmed by independent check: {confirmed} ({confirmed/total*100:.1f}%)")
    print(f"  NOT FOUND by independent check:  {not_found} ({not_found/total*100:.1f}%)")
    print(f"  No validation rule:              {no_check} ({no_check/total*100:.1f}%)")

    # By confidence level
    print("\n--- Accuracy by confidence level ---")
    conf_stats = defaultdict(lambda: defaultdict(int))
    for row in validation_rows:
        conf_stats[row["confidence"]][row["independent_check"]] += 1
        conf_stats[row["confidence"]]["total"] += 1

    for conf in ["high", "medium", "low"]:
        s = conf_stats[conf]
        c = s.get("confirmed", 0)
        nf = s.get("NOT_FOUND", 0)
        nc = s.get("no_check", 0)
        t = s.get("total", 0)
        if t:
            acc = c / (c + nf) * 100 if (c + nf) > 0 else 0
            print(f"  {conf.upper():<8} {t:>4} detections | confirmed: {c:>4} | NOT_FOUND: {nf:>4} | no_check: {nc:>4} | accuracy (of checked): {acc:.1f}%")

    # Technologies with NOT_FOUND > 0
    print("\n--- Technologies with independent check failures ---")
    failures = []
    for tech, counts in sorted(stats.items(), key=lambda x: x[1].get("NOT_FOUND", 0), reverse=True):
        nf = counts.get("NOT_FOUND", 0)
        if nf > 0:
            c = counts.get("confirmed", 0)
            t = counts["total"]
            failures.append((tech, nf, c, t))

    for tech, nf, c, t in failures[:20]:
        print(f"  {tech:<30} NOT_FOUND: {nf:>3}/{t}  (confirmed: {c})")

    print(f"\nValidation saved to: {VALIDATION_CSV}")


if __name__ == "__main__":
    asyncio.run(main())

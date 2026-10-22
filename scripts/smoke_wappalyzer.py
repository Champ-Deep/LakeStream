import json, pkg_resources, warnings, requests
warnings.filterwarnings('ignore')
from Wappalyzer import Wappalyzer, WebPage
w = Wappalyzer.latest('src/data/wappalyzer_technologies.json')
for url in ['https://wordpress.org', 'https://www.shopify.com', 'https://vercel.com']:
    r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
    page = WebPage(url, r.text, dict(r.headers))
    print(url, '->', sorted(w.analyze(page)))

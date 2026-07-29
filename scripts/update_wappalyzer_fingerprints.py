"""Pin current Wappalyzer fingerprints (enthec/webappalyzer fork) to src/data/."""
import json, string, requests

BASE = "https://raw.githubusercontent.com/HTTPArchive/wappalyzer/main/src"
technologies = {}
for ch in string.ascii_lowercase + "_":
    r = requests.get(f"{BASE}/technologies/{ch}.json", timeout=30)
    if r.status_code == 200:
        technologies.update(r.json())
cats = requests.get(f"{BASE}/categories.json", timeout=30).json()

# ponytail: python-Wappalyzer 0.3.1 only supports url/html/scripts/headers/meta/implies.
# Newer schema extras (js, dom, css, xhr, robots...) are dropped here.
# Upgrade path: maintained lib with full-schema support.
for name, tech in technologies.items():
    # scriptSrc is the modern name for scripts
    ss = tech.pop("scriptSrc", None)
    if ss:
        cur = tech.get("scripts", [])
        if not isinstance(cur, list):
            cur = [cur]
        tech["scripts"] = cur + (ss if isinstance(ss, list) else [ss])
    # headers/meta values must be plain strings for the old lib; lists -> alternation
    for key in ("headers", "meta"):
        obj = tech.get(key)
        if isinstance(obj, dict):
            for hk, v in list(obj.items()):
                if isinstance(v, list):
                    parts = [p.split("\\;")[0] for p in v]
                    obj[hk] = "(?:" + "|".join(parts) + ")" if len(parts) > 1 else parts[0]

out = {"categories": cats, "technologies": technologies}
path = "src/data/wappalyzer_technologies.json"
with open(path, "w", encoding="utf-8") as f:
    json.dump(out, f)
print("techs:", len(technologies), "cats:", len(cats), "->", path)

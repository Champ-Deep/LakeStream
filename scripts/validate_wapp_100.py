"""Validate companies_100_wapp_test.csv: enrichment stats, overlap, samples."""
import csv
from collections import Counter

rows = list(csv.DictReader(open("companies_100_wapp_test.csv", encoding="utf-8")))
ok = [r for r in rows if r["status"] == "OK"]
print(f"total {len(rows)} | OK {len(ok)}")

with_wapp = [r for r in ok if r["wappalyzer_techs"]]
counts = [len(r["wappalyzer_techs"].split("|")) for r in with_wapp]
print(f"wappalyzer hits: {len(with_wapp)}/{len(ok)} OK rows | avg {sum(counts)/len(counts):.1f} techs | max {max(counts)}")

# overlap with curated detections
both = only_wapp = 0
overlap_names = Counter()
for r in ok:
    curated = {d.split(" (")[0].strip().lower() for d in r["detections"].split("; ") if d}
    wapp = {t.strip() for t in r["wappalyzer_techs"].split("|") if t}
    for t in wapp:
        if t.lower() in curated:
            overlap_names[t] += 1
    if wapp - {c for c in curated}:
        only_wapp += 1
print(f"rows where wappalyzer adds techs beyond curated: {only_wapp}")
print("top overlaps (also caught by curated):", overlap_names.most_common(10))

# frequency of wappalyzer-only techs
freq = Counter()
for r in ok:
    curated = {d.split(" (")[0].strip().lower() for d in r["detections"].split("; ") if d}
    for t in (r["wappalyzer_techs"].split("|") if r["wappalyzer_techs"] else []):
        if t.lower() not in curated:
            freq[t] += 1
print("top NEW techs from wappalyzer:", freq.most_common(15))

print("\n--- samples ---")
for r in ok[:8]:
    print(r["WEB_ADDRESS"], "| curated:", r["detections"][:80], "| wapp:", r["wappalyzer_techs"][:120])

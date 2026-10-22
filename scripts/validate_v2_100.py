"""Validate companies_500_first100_v2.csv: columns, hit rates, samples, deep.csv cross-check."""
import csv

rows = list(csv.DictReader(open("companies_500_first100_v2.csv", encoding="utf-8")))
ok = [r for r in rows if r["status"] == "OK"]
print(f"total {len(rows)} | OK {len(ok)} | FETCH_ERROR {sum(r['status']=='FETCH_ERROR' for r in rows)} | EMPTY {sum(r['status']=='EMPTY' for r in rows)}")

for col in ["platform", "web_servers", "os", "programming_languages", "cdn", "hosting",
            "js_libraries", "widgets", "analytics", "email_hosting", "ssl_issuer"]:
    hits = sum(bool(r[col]) for r in ok)
    print(f"{col:25s} hits: {hits}/{len(ok)}")

print("\n--- samples ---")
for r in ok[:6]:
    print(f"\n{r['WEB_ADDRESS']}")
    for col in ["platform", "web_servers", "os", "programming_languages", "cdn",
                "hosting", "js_libraries", "analytics", "widgets", "email_hosting",
                "ssl_issuer", "ssl_expiry"]:
        if r[col]:
            print(f"  {col}: {r[col][:110]}")

# deep.csv cross-check
deep = {r["domain"].strip().lower() for r in csv.DictReader(open("deep.csv", encoding="utf-8-sig"))} \
    if __import__("os").path.exists("deep.csv") else set()
both = [r for r in ok if r["WEB_ADDRESS"].strip().lower() in deep]
print(f"\ndeep.csv overlap in this 100: {len(both)}")
for r in both[:5]:
    print(f"  {r['WEB_ADDRESS']} | platform: {r['platform'][:60]} | web_servers: {r['web_servers'][:40]}")

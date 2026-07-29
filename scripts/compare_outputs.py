"""Compare tech detection output vs deep.csv reference."""
import csv
from collections import Counter


def load(path, domain_col):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[r[domain_col].strip().lower().removeprefix("www.")] = r
    return rows


def names(row):
    out = set()
    for part in (row.get("detections") or "").split(";"):
        part = part.strip()
        if part:
            out.add(part.split(" (")[0].strip().lower())
    return out


ours = load("companies_500_tech_stacks.csv", "WEBSITE")
deep = load("deep.csv", "domain")

common = sorted(set(ours) & set(deep))
only_ours = set(ours) - set(deep)
only_deep = set(deep) - set(ours)
print(f"ours={len(ours)} deep={len(deep)} common={len(common)} only_ours={len(only_ours)} only_deep={len(only_deep)}")

both_ok = 0
status_agree = 0
platform_agree = 0
platform_both = 0
jaccards = []
we_missed = Counter()
they_missed = Counter()
disagree_rows = []

for d in common:
    a, b = ours[d], deep[d]
    if a["status"] == b["status"]:
        status_agree += 1
    if a["status"] != "OK" or b["status"] != "OK":
        continue
    both_ok += 1
    pa, pb = (a.get("platform") or "").lower(), (b.get("platform") or "").lower()
    if pa and pb:
        platform_both += 1
        if pa == pb:
            platform_agree += 1
    na, nb = names(a), names(b)
    if na or nb:
        inter = na & nb
        j = len(inter) / len(na | nb)
        jaccards.append(j)
        for n in nb - na:
            we_missed[n] += 1
        for n in na - nb:
            they_missed[n] += 1
        if j < 0.5:
            disagree_rows.append((d, j, sorted(na), sorted(nb)))

print(f"status agreement: {status_agree}/{len(common)} ({100*status_agree/len(common):.1f}%)")
print(f"both fetch OK: {both_ok}")
print(f"platform agreement (both detected): {platform_agree}/{platform_both} ({100*platform_agree/max(platform_both,1):.1f}%)")
if jaccards:
    avg = sum(jaccards) / len(jaccards)
    exact = sum(1 for j in jaccards if j == 1.0)
    zero = sum(1 for j in jaccards if j == 0.0)
    print(f"detection-set Jaccard: avg={avg:.3f} exact={exact}/{len(jaccards)} zero-overlap={zero}")

print("\nTop techs DEEP found that we missed:")
for n, c in we_missed.most_common(15):
    print(f"  {c:4d}  {n}")
print("\nTop techs WE found that deep missed:")
for n, c in they_missed.most_common(15):
    print(f"  {c:4d}  {n}")

with open("compare_disagreements.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["domain", "jaccard", "ours", "deep"])
    for d, j, na, nb in sorted(disagree_rows, key=lambda x: x[1]):
        w.writerow([d, f"{j:.2f}", "; ".join(na), "; ".join(nb)])
print(f"\n{len(disagree_rows)} domains with Jaccard<0.5 -> compare_disagreements.csv")

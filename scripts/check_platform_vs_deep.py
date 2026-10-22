import csv
deep = {r['domain'].strip().lower(): r['platform'] for r in csv.DictReader(open('deep.csv', encoding='utf-8-sig'))}
v2 = [r for r in csv.DictReader(open('companies_500_first100_v2.csv', encoding='utf-8')) if r['status'] == 'OK']
rows_with_deep = [r for r in v2 if deep.get(r['WEB_ADDRESS'].strip().lower())]
agree = 0
for r in rows_with_deep:
    d = deep[r['WEB_ADDRESS'].strip().lower()]
    v2p = r['platform'].split('|')[0].split(' (')[0] if r['platform'] else ''
    if v2p.lower() == d.lower():
        agree += 1
    else:
        print('  mismatch:', r['WEB_ADDRESS'], '| deep:', d, '| v2:', v2p or '(empty)')
print(f'rows where deep.csv had a platform: {len(rows_with_deep)} | v2 agrees: {agree}')

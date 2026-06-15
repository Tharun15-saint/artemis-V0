"""Diagnostic: locate historical WCI data on Drewry public page."""

import re
import urllib.request

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml',
}

URL = (
    'https://www.drewry.co.uk/supply-chain-advisors/supply-chain-expertise/'
    'world-container-index-assessed-by-drewry'
)

KEYWORDS = [
    'historical',
    'download',
    'weekly',
    '2024',
    '2023',
    '2022',
    '2021',
]

req = urllib.request.Request(URL, headers=HEADERS)
resp = urllib.request.urlopen(req, timeout=20)
content = resp.read().decode('utf-8')
text = re.sub(r'<[^>]+>', ' ', content)
text = re.sub(r'\s+', ' ', text)

print(f'Total page chars (html): {len(content)}')
print(f'Total page chars (text): {len(text)}')
print()

for keyword in KEYWORDS:
    print(f'=== KEYWORD: {keyword} ===')
    idx = 0
    found = False
    while True:
        pos = text.lower().find(keyword.lower(), idx)
        if pos < 0:
            break
        found = True
        start = max(0, pos - 1000)
        end = min(len(text), pos + 1000)
        snippet = text[start:end]
        print(f'\n--- Match at position {pos} ---')
        print(snippet)
        print()
        idx = pos + len(keyword)
    if not found:
        print('  (no matches)')
    print()

# Check for embedded JSON / chart data
print('=== EMBEDDED DATA MARKERS ===')
for marker in ['__NEXT_DATA__', 'application/json', 'chart', 'highcharts', 'data-series', 'csv', 'xlsx']:
    if marker.lower() in content.lower():
        pos = content.lower().find(marker.lower())
        print(f'FOUND [{marker}] at html position {pos}')
    else:
        print(f'NOT FOUND [{marker}]')

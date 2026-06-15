"""
Diagnostic: test Freightos FBX API endpoints and inspect response shape.

Reads FREIGHTOS_API_KEY from the environment — never hardcode keys.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

ENDPOINTS = [
    'https://fbx.freightos.com/api/v1/rates',
    'https://api.freightos.com/fbx/v1/index',
]

HEADERS_BASE = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}


def _try_fetch(url: str, api_key: str) -> None:
    print(f'\n=== Trying: {url} ===')
    headers = {
        **HEADERS_BASE,
        'Authorization': api_key,
    }
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
            resp_headers = dict(resp.headers.items())
            body = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        status = exc.code
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
        body = exc.read().decode('utf-8', errors='replace')
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}')
        return

    print(f'Status: {status}')
    print('Headers:')
    for key, value in resp_headers.items():
        print(f'  {key}: {value}')
    print(f'\nBody (first 1000 chars):\n{body[:1000]}')

    if status >= 400:
        return

    _inspect_success_payload(body)


def _inspect_success_payload(body: str) -> None:
    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        print('\nResponse is not JSON — cannot inspect date range or corridors.')
        return

    print('\n--- Success payload inspection ---')
    print(f'Top-level type: {type(payload).__name__}')

    if isinstance(payload, dict):
        print(f'Top-level keys: {list(payload.keys())}')
        data = payload.get('data', payload)
    elif isinstance(payload, list):
        data = payload
        print(f'Record count: {len(payload)}')
    else:
        print('Unexpected payload shape.')
        return

    records: list[Any]
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        # Common nested shapes: { rates: [...] } or { index: [...] }
        for key in ('rates', 'index', 'results', 'items', 'series'):
            if isinstance(data.get(key), list):
                records = data[key]
                print(f'Nested list key: {key} ({len(records)} records)')
                break
        else:
            records = [data]
            print('Single dict record — inspecting keys for date/corridor fields.')
    else:
        print('No list payload found for corridor/date inspection.')
        return

    if not records:
        print('No records in payload.')
        return

    sample = records[0] if isinstance(records[0], dict) else {}
    if sample:
        print(f'Sample record keys: {list(sample.keys())}')

    dates: set[str] = set()
    corridors: set[str] = set()
    date_keys = ('date', 'as_of_date', 'week', 'period', 'timestamp', 'observation_date')
    corridor_keys = (
        'corridor', 'route', 'lane', 'origin', 'destination',
        'origin_port', 'destination_port', 'index_name', 'name', 'symbol',
    )

    for record in records:
        if not isinstance(record, dict):
            continue
        for dk in date_keys:
            if record.get(dk) is not None:
                dates.add(str(record[dk]))
        origin = record.get('origin_port') or record.get('origin') or ''
        dest = record.get('destination_port') or record.get('destination') or ''
        if origin or dest:
            corridors.add(f'{origin} → {dest}'.strip(' →'))
        for ck in corridor_keys:
            if ck in record and record[ck] and ck not in ('origin', 'destination', 'origin_port', 'destination_port'):
                corridors.add(str(record[ck]))
        if record.get('route'):
            corridors.add(str(record['route']))
        if record.get('lane'):
            corridors.add(str(record['lane']))
        if record.get('index_name'):
            corridors.add(str(record['index_name']))
        if record.get('name'):
            corridors.add(str(record['name']))

    if dates:
        sorted_dates = sorted(dates)
        print(f'\nDate range ({len(sorted_dates)} unique values): '
              f'{sorted_dates[0]} → {sorted_dates[-1]}')
        if len(sorted_dates) <= 10:
            print(f'All dates: {sorted_dates}')
    else:
        print('\nNo date fields detected in records.')

    if corridors:
        sorted_corridors = sorted(corridors)
        print(f'\nCorridors / indices ({len(sorted_corridors)} unique):')
        for corridor in sorted_corridors[:30]:
            print(f'  - {corridor}')
        if len(sorted_corridors) > 30:
            print(f'  ... and {len(sorted_corridors) - 30} more')
    else:
        print('\nNo corridor fields detected in records.')


def main() -> None:
    api_key = os.getenv('FREIGHTOS_API_KEY', '')
    if not api_key:
        print('FREIGHTOS_API_KEY is not set.')
        return

    masked = api_key[:4] + '...' + api_key[-4:] if len(api_key) > 8 else '(short key)'
    print(f'Using FREIGHTOS_API_KEY: {masked}')

    for url in ENDPOINTS:
        _try_fetch(url, api_key)


if __name__ == '__main__':
    main()

"""Offline identity and structure check; does not navigate or modify a capture."""
import argparse
import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit


def check(raw, expected_id):
    errors = []
    if raw.get('state') != 'DETAIL':
        errors.append('state is not DETAIL')
    if str(raw.get('id')) != str(expected_id):
        errors.append('ID does not match the queue')
    source_url = raw.get('source_url', '')
    host = urlsplit(source_url).hostname or ''
    if host != 'avito.ru' and not host.endswith('.avito.ru'):
        errors.append('source_url is not an Avito URL')
    if not isinstance(raw.get('description'), str) or not raw['description'].strip():
        errors.append('description is empty or missing')
    if not isinstance(raw.get('fields'), list):
        errors.append('fields must be a list; empty is distinct from unread')
    elif any(not isinstance(field, dict) or
             any(not isinstance(field.get(key), str) or not field[key].strip() for key in ('name', 'value'))
             for field in raw['fields']):
        errors.append('fields contain missing or empty names/values')
    try:
        observed = datetime.datetime.fromisoformat(raw.get('observed_at', '').replace('Z', '+00:00'))
        if observed.tzinfo is None:
            errors.append('observed_at has no timezone')
    except (TypeError, ValueError):
        errors.append('observed_at is not an ISO timestamp')
    return {'id': str(expected_id), 'structural_check_passed': not errors,
            'errors': errors, 'full_parameters_read': raw.get('full_parameters_read'),
            'semantic_review_performed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', required=True)
    parser.add_argument('--expected-id', required=True)
    args = parser.parse_args()
    result = check(json.loads(Path(args.raw).read_text(encoding='utf-8')), args.expected_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['structural_check_passed'] else 2)

"""Run DOM regressions in a newly owned local gstack tab. Never opens Avito."""
import argparse
import json
from pathlib import Path
import avito


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='Data/avito_runs/current_verification_2026-10-20/dom_fixtures')
    args = parser.parse_args()
    out = Path(args.output).resolve()
    cases = {'blocked_overlay': 'NEEDS_HUMAN', 'listing': 'LISTING', 'detail': 'DETAIL',
             'pagination': 'LISTING', 'pagination_end': 'LISTING', 'empty': 'EMPTY_RESULT',
             'incomplete': 'UNKNOWN_PAGE', 'detail_multivalue': 'DETAIL',
             'detail_location_heading': 'DETAIL', 'detail_location_missing': 'DETAIL'}
    report = {'kind': 'LOCAL_FIXTURES_IN_REAL_CHROMIUM', 'live_avito': False, 'cases': {}}
    with avito.session_lock():
        tab = json.loads(avito.browser('newtab', (avito.ROOT / 'fixtures/listing.html').as_uri(), '--json'))
        try:
            for case, expected in cases.items():
                avito.browser('tab', tab['tabId'])
                avito.browser('goto', (avito.ROOT / f'fixtures/{case}.html').as_uri())
                target = out / f'{case}.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                avito.browser('eval', avito.ROOT / 'extract.js', '--out', target)
                data = json.loads(target.read_text())
                assert data['state'] == expected, (case, data)
                if case == 'blocked_overlay':
                    assert data['items'] == [] and 'description' not in data
                if case == 'listing':
                    assert data['items'][0]['salary_raw'] == 'до 12 000 ₽ за смену на руки'
                    assert not data['pagination']['end_confirmed']
                if case == 'detail':
                    assert data['fields'][0]['name'] == 'Можно заканчивать в 17:00'
                if case == 'detail_location_heading':
                    assert data['location_raw'] == ['Московская обл., Серпухов, 5-я Борисовская ул., 16']
                    assert data['read_status']['location'] == 'observed'
                    assert data['location_evidence'][0]['method'] == 'visible_location_heading'
                if case == 'detail_location_missing':
                    assert data['location_raw'] == []
                    assert data['read_status']['location'] == 'unread'
                if case == 'pagination':
                    assert data['pagination']['next_url'] == 'https://www.avito.ru/moskva/vakansii?f=opaque&p=2'
                    assert not data['pagination']['end_confirmed']
                if case == 'pagination_end':
                    assert data['pagination']['end_confirmed'] and data['pagination']['next_url'] is None
                if case == 'detail_multivalue':
                    assert data['fields'][0]['values'] == ['B', 'C']
                    assert data['employer_raw'] == ['Fixture Employer']
                    assert data['location_raw'] == ['Москва, улица Примерная']
                    assert 'не нужно' in data['description'] and 'example@example.org' in data['description']
                    assert '[email removed]' in data['description_redacted']
                    assert data['full_parameters_read'] is False
                report['cases'][case] = {'expected': expected, 'actual': data['state'], 'passed': True}
        finally:
            avito.browser('closetab', tab['tabId'])
    avito.atomic_json(out / 'verification.json', report)
    print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__':
    main()

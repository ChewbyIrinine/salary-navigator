"""Synthetic state-machine tests. These are not live Avito verification."""
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import avito
from normalize import salary

URL = 'https://www.avito.ru/moskva/vakansii?f=opaque'
NEXT = URL + '&p=2'

def item(i):
    return {'id': str(i), 'url': f'https://www.avito.ru/moskva/vakansii/test_{i}', 'title': f'Job {i}'}

def page(ids=(1,), url=URL, next_url=None, end=False):
    return {'state': 'LISTING', 'source_url': url, 'items': [item(i) for i in ids],
            'selected_controls': [{'label': 'Упаковщик', 'marker': 'observed_marker', 'value': 'observed_value'}],
            'pagination': {'next_url': next_url, 'end_confirmed': end}}

def detail(i):
    return {'state': 'DETAIL', 'id': str(i), 'source_url': item(i)['url'], 'observed_at': '2026-10-20T17:00:00Z',
            'title': 'T', 'description': 'Разгружать машины не нужно. Аванс 5000 ₽.',
            'params_blocks': ['График: 2/2, 3/3'], 'fields': [], 'salary_raw': 'от 3 000 ₽ за смену\nна руки'}

def args(out, **kw):
    return argparse.Namespace(output=out, resume=kw.get('resume', False), tab_id=kw.get('tab_id', 7),
                              limit=kw.get('limit', 20), max_pages=kw.get('max_pages', 4), delay=0,
                              extend=kw.get('extend', False), url=URL)

class TraversalTests(unittest.TestCase):
    def test_two_pages_deduplicate_and_preserve_slice(self):
        seq = iter([page((1, 1, 2), next_url=NEXT), detail(1), detail(2), page((2, 3), NEXT, end=True), detail(3)])
        with tempfile.TemporaryDirectory() as out:
            nav = []
            run = avito.collect(args(out), read=lambda: next(seq), navigate=nav.append)
            self.assertEqual(run['state'], 'EXHAUSTED')
            self.assertEqual(run['details_done'], ['1', '2', '3'])
            self.assertEqual(len(run['duplicates']), 2)
            self.assertEqual(nav, [item(1)['url'], item(2)['url'], NEXT, item(3)['url']])
            saved = json.loads((Path(out) / 'detail_3.json').read_text())
            self.assertEqual(saved['collection_context']['discovered_on'], NEXT)
            self.assertEqual(saved['normalized']['salary']['amount_period'], 'shift')
            self.assertIn('не нужно', saved['description'])

    def test_limit_keeps_full_queue_and_extend_resumes(self):
        with tempfile.TemporaryDirectory() as out:
            seq = iter([page((1, 2, 3), end=True), detail(1)])
            run = avito.collect(args(out, limit=1), read=lambda: next(seq), navigate=lambda u: None)
            self.assertEqual(run['state'], 'COMPLETE_SAMPLE')
            self.assertEqual(run['remaining_ids'], ['2', '3'])
            self.assertIn('detail_limit', run['stop_reason'])
            nav = []
            run = avito.collect(args(out, resume=True, limit=1), read=lambda: self.fail('Completed job reread'), navigate=nav.append)
            self.assertEqual(nav, [])
            seq = iter([detail(2), detail(3)])
            run = avito.collect(args(out, resume=True, extend=True, limit=3), read=lambda: next(seq), navigate=nav.append)
            self.assertEqual(run['details_done'], ['1', '2', '3'])

    def test_page_repeat_with_new_url_stops(self):
        seq = iter([page(next_url=NEXT), detail(1), page(url=NEXT, next_url=NEXT+'&p=3')])
        with tempfile.TemporaryDirectory() as out:
            run = avito.collect(args(out), read=lambda: next(seq), navigate=lambda u: None)
            self.assertEqual(run['state'], 'PAGINATION_LOOP')
            self.assertEqual(run['details_done'], ['1'])

    def test_no_next_is_not_end(self):
        seq = iter([page(), detail(1)])
        with tempfile.TemporaryDirectory() as out:
            run = avito.collect(args(out), read=lambda: next(seq), navigate=lambda u: None)
            self.assertEqual(run['state'], 'PAGINATION_UNVERIFIED')

    def test_page_budget_is_not_exhaustion(self):
        seq = iter([page(next_url=NEXT), detail(1)])
        with tempfile.TemporaryDirectory() as out:
            run = avito.collect(args(out, max_pages=1), read=lambda: next(seq), navigate=lambda u: None)
            self.assertEqual(run['state'], 'COMPLETE_SAMPLE')
            self.assertIn('page_limit', run['stop_reason'])

    def test_next_link_cannot_change_filters(self):
        seq = iter([page(next_url='https://www.avito.ru/moskva/vakansii?f=other&p=2'), detail(1)])
        with tempfile.TemporaryDirectory() as out:
            nav = []
            run = avito.collect(args(out), read=lambda: next(seq), navigate=nav.append)
            self.assertEqual(run['state'], 'PAGINATION_UNVERIFIED')
            self.assertEqual(nav, [item(1)['url']])

    def test_timeout_reads_same_page_and_succeeds_without_retry(self):
        seq = iter([page(end=True), detail(1)])
        def timeout(url):
            raise avito.subprocess.TimeoutExpired('goto', 65)
        with tempfile.TemporaryDirectory() as out:
            run = avito.collect(args(out), read=lambda: next(seq), navigate=timeout)
            self.assertEqual(run['state'], 'EXHAUSTED')

    def test_interrupt_records_pending_before_navigation(self):
        with tempfile.TemporaryDirectory() as out:
            def interrupted(url):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                avito.collect(args(out), read=lambda: page(end=True), navigate=interrupted)
            saved = json.loads((Path(out)/'manifest.json').read_text())
            self.assertEqual(saved['state'], 'INTERRUPTED')
            self.assertEqual(saved['pending_id'], '1')
            nav = []
            run = avito.collect(args(out, resume=True, tab_id=99), read=lambda: detail(1), navigate=nav.append)
            self.assertEqual(nav, [])
            self.assertEqual(run['state'], 'EXHAUSTED')

    def test_block_on_next_page_resumes_after_new_tab_without_losing_cards(self):
        seq = iter([page(next_url=NEXT), detail(1), {'state': 'NEEDS_HUMAN', 'source_url': NEXT}])
        with tempfile.TemporaryDirectory() as out:
            avito.collect(args(out), read=lambda: next(seq), navigate=lambda u: None)
            seq = iter([page((2,), NEXT, end=True), page((2,), NEXT, end=True), detail(2)])
            nav = []
            run = avito.collect(args(out, resume=True, tab_id=99), read=lambda: next(seq), navigate=nav.append)
            self.assertEqual(run['state'], 'EXHAUSTED')
            self.assertEqual(run['details_done'], ['1', '2'])
            self.assertEqual(nav, [item(2)['url']])

    def test_unknown_empty_and_confirmed_empty_are_different(self):
        for state in ('UNKNOWN_PAGE', 'EMPTY_RESULT'):
            with tempfile.TemporaryDirectory() as out:
                run = avito.collect(args(out), read=lambda: {'state': state, 'source_url': URL, 'items': []}, navigate=lambda u: self.fail('Navigation from empty'))
                self.assertEqual(run['state'], 'EMPTY_RESULT' if state == 'EMPTY_RESULT' else 'LISTING_UNVERIFIED')

    def test_crash_between_card_and_manifest_recovers_saved_card(self):
        with tempfile.TemporaryDirectory() as out:
            def interrupted(url):
                data = detail(1)
                data['collection_context'] = {'requested_url': URL}
                avito.atomic_json(Path(out)/'detail_1.json', data)
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                avito.collect(args(out), read=lambda: page(end=True), navigate=interrupted)
            run = avito.collect(args(out, resume=True), read=lambda: self.fail('Saved card reread'), navigate=lambda u: self.fail('Saved card reloaded'))
            self.assertEqual(run['details_done'], ['1'])

    def test_catalog_records_new_reobserved_changed_and_keeps_memberships(self):
        with tempfile.TemporaryDirectory() as out:
            for i, title in enumerate(('A', 'A', 'B')):
                data = dict(detail(1), title=title, observed_at=f'2026-10-{17+i}T10:00:00Z', collection_context={'requested_url': URL+str(i)})
                avito.atomic_json(Path(out)/str(i)/'detail_1.json', data)
            result = avito.build_catalog(out)
            self.assertEqual(result['unique_ids'], 1)
            rows = result['items']['1']
            self.assertEqual([r['observation_kind'] for r in rows], ['NEW', 'REOBSERVED', 'CHANGED'])
            self.assertNotEqual(rows[0]['slice'], rows[1]['slice'])
            self.assertEqual(result['closure_inference'], 'not_performed')

    def test_access_stop_blocks_other_profile_navigation(self):
        with tempfile.TemporaryDirectory() as out:
            stop = Path(out)/'stop.json'; stop.write_text('{}')
            with patch.object(avito, 'ACCESS_STOP', stop), patch.object(avito.subprocess, 'run') as call:
                with self.assertRaisesRegex(RuntimeError, 'Access stop'):
                    avito.browser('newtab', URL)
                call.assert_not_called()

    def test_salary_never_derives_amount_period_from_payouts(self):
        result = salary('от 80 000 ₽', [{'name': 'Частота выплат', 'value': 'Дважды в месяц'}])
        self.assertEqual(result['lower'], 80000)
        self.assertIsNone(result['upper'])
        self.assertIsNone(result['amount_period'])
        self.assertIsNone(result['tax_basis'])
        self.assertEqual(result['bonuses'], [])
        self.assertIsNone(result['advance'])
        self.assertEqual(salary('до 4 000 ₽ за смену')['upper'], 4000)
        result = salary('80 000 – 100 000 ₽ в месяц\nна руки')
        self.assertEqual((result['lower'], result['upper'], result['amount_period'], result['tax_basis']), (80000, 100000, 'month', 'net'))

if __name__ == '__main__':
    unittest.main()

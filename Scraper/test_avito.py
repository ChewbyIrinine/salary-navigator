import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('avito', Path(__file__).with_name('avito.py'))
avito = importlib.util.module_from_spec(spec)
spec.loader.exec_module(avito)


class CollectorTests(unittest.TestCase):
    def args(self, directory, resume=False):
        return argparse.Namespace(output=directory, resume=resume, tab_id=37, limit=3, delay=0,
                                  url='https://www.avito.ru/moskva/vakansii?f=opaque&localPriority=0')

    def test_url_retains_filters_and_rejects_other_origins(self):
        url = self.args('').url
        self.assertEqual(avito.validate_url(url), url)
        for bad in ['https://evil.test/vakansii', 'https://avito.ru.evil.test/vakansii',
                    'https://u:p@www.avito.ru/vakansii', 'https://www.avito.ru:999/vakansii',
                    'https://www.avito.ru/logout/vakansii']:
            with self.assertRaises(ValueError):
                avito.validate_url(bad)

    def test_known_redirect_does_not_drop_semantic_filter(self):
        self.assertEqual(avito.search_identity('https://www.avito.ru/moskva/vakansii?f=one&localPriority=0&context=abc#block'),
                         avito.search_identity('https://www.avito.ru/moskva/vakansii?f=one'))
        self.assertNotEqual(avito.search_identity('https://www.avito.ru/moskva/vakansii?f=one'),
                            avito.search_identity('https://www.avito.ru/moskva/vakansii?f=two'))

    def test_blocked_listing_never_navigates_or_claims_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            result = avito.collect(self.args(tmp), read=lambda: {'state': 'NEEDS_HUMAN'}, navigate=calls.append)
            self.assertEqual(result['state'], 'NEEDS_HUMAN')
            self.assertEqual(calls, [])
            self.assertIsNone(result['listing'])

    def test_checkpoint_resume_preserves_completed_cards_without_reloading_restored_page(self):
        first = {'id': '1', 'url': 'https://www.avito.ru/moskva/vakansii/a_1'}
        second = {'id': '2', 'url': 'https://www.avito.ru/moskva/vakansii/a_2'}
        def detail(item):
            return dict(item, state='DETAIL', title='Title', description='Description', params_blocks=['График: 2/2'], fields=[])
        observations = iter([{'state': 'LISTING', 'source_url': self.args('').url, 'items': [first, first, second]}, detail(first), {'state': 'NEEDS_HUMAN'}])
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            result = avito.collect(self.args(tmp), read=lambda: next(observations), navigate=calls.append)
            self.assertEqual(result['details_done'], ['1'])
            self.assertEqual(result['state'], 'NEEDS_HUMAN')
            self.assertEqual(len(calls), 2)
            self.assertFalse((Path(tmp) / 'detail_2.json').exists())
            result = avito.collect(self.args(tmp, resume=True), read=lambda: detail(second), navigate=calls.append)
            self.assertEqual(result['details_done'], ['1', '2'])
            self.assertEqual(result['state'], 'COMPLETE_SAMPLE')
            self.assertEqual(len(calls), 2)  # no fresh navigation after human restored detail

    def test_wrong_id_or_missing_content_cannot_pass(self):
        d = {'id': '1', 'state': 'DETAIL', 'title': 'T', 'description': 'D', 'params_blocks': ['P']}
        self.assertTrue(avito.valid_detail(d, '1'))
        self.assertFalse(avito.valid_detail(d, '2'))
        self.assertFalse(avito.valid_detail(dict(d, description=None), '1'))

    def test_browser_failure_persists_pending_work(self):
        item = {'id': '1', 'url': 'https://www.avito.ru/moskva/vakansii/a_1'}
        def failed_navigation(url):
            raise RuntimeError('browser connection lost')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                avito.collect(self.args(tmp), read=lambda: {'state': 'LISTING', 'source_url': self.args('').url, 'items': [item]}, navigate=failed_navigation)
            saved = json.loads((Path(tmp) / 'manifest.json').read_text())
            self.assertEqual(saved['state'], 'BROWSER_ERROR')
            self.assertEqual(saved['pending_id'], '1')
            self.assertEqual(saved['details_done'], [])

    def test_filter_retries_unconfirmed_click_but_does_not_toggle_confirmed_value(self):
        marker = 'filters/localPriority/localPriority'
        clicks = []
        def fake_browser(command, *args):
            if command == 'newtab':
                return '{"tabId": 7}'
            if command == 'click':
                clicks.append(args)
            return ''
        page = {'state': 'LISTING', 'source_url': 'https://www.avito.ru/moskva/vakansii', 'items': []}
        with tempfile.TemporaryDirectory() as tmp:
            recipe = Path(tmp) / 'recipe.json'
            recipe.write_text(json.dumps({'start_url': page['source_url'], 'controls': [{'marker': marker, 'checked': True}]}))
            with patch.object(avito, 'browser', side_effect=fake_browser), patch.object(avito, 'settled_observation', return_value=page), patch.object(avito, 'control_state', side_effect=lambda m: {'type': 'switch', 'checked': len(clicks) >= 2}), patch.object(avito.time, 'sleep'):
                result = avito.apply_recipe(recipe, Path(tmp) / 'result.json')
            self.assertEqual(result['state'], 'FILTERS_VERIFIED')
            self.assertEqual(len(clicks), 2)

    def test_conflict_keeps_evidence_and_does_not_choose_truth(self):
        d = {'id': '1', 'source_url': 'https://www.avito.ru/moskva/vakansii/a_1',
             'description': 'Выплаты каждый день!', 'fields': [{'name': 'Частота выплат', 'value': 'Раз в неделю'}]}
        conflicts = avito.classify_conflicts(d)
        self.assertEqual(len(conflicts), 1)
        self.assertIsNone(conflicts[0]['resolution'])
        self.assertIn(conflicts[0]['description_value_raw'], d['description'])

    def test_isolated_browser_environment_cannot_reuse_project_profile(self):
        with patch.object(avito, 'ISOLATED', True):
            env = avito.browser_env()
        self.assertEqual(env['BROWSE_STATE_FILE'], str(avito.ROOT / '.gstack/browse.json'))
        self.assertEqual(env['CHROMIUM_PROFILE'], str(avito.ROOT / '.gstack/chromium-profile'))
        self.assertEqual(env['BROWSE_HEADED'], '1')

    def test_restart_rebinds_only_proven_pending_detail_without_reload(self):
        item = {'id': '1', 'url': 'https://www.avito.ru/moskva/vakansii/a_1'}
        listing = {'state': 'LISTING', 'source_url': self.args('').url, 'items': [item]}
        obs = iter([listing, {'state': 'NEEDS_HUMAN'}])
        restored = dict(item, state='DETAIL', title='Title', description='Description', params_blocks=['График: 2/2'], fields=[])
        with tempfile.TemporaryDirectory() as tmp:
            avito.collect(self.args(tmp), read=lambda: next(obs), navigate=lambda u: None)
            args = self.args(tmp, resume=True)
            args.tab_id = 99
            nav = []
            result = avito.collect(args, read=lambda: restored, navigate=nav.append)
            self.assertEqual(result['state'], 'COMPLETE_SAMPLE')
            self.assertEqual(result['tab_id'], 99)
            self.assertEqual(result['previous_tab_ids'], [37])
            self.assertEqual(nav, [])

    def test_resume_after_block_cannot_silently_switch_segment(self):
        with tempfile.TemporaryDirectory() as tmp:
            avito.collect(self.args(tmp), read=lambda: {'state': 'NEEDS_HUMAN'})
            different = {'state': 'LISTING', 'source_url': 'https://www.avito.ru/moskva/vakansii?f=DIFFERENT', 'items': []}
            with self.assertRaisesRegex(ValueError, 'Restored listing'):
                avito.collect(self.args(tmp, resume=True), read=lambda: different)

    def test_resume_cannot_switch_browser_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            avito.collect(self.args(tmp), read=lambda: {'state': 'NEEDS_HUMAN'})
            with patch.object(avito, 'ISOLATED', True), self.assertRaisesRegex(ValueError, 'browser session'):
                avito.collect(self.args(tmp, resume=True), read=lambda: {'state': 'NEEDS_HUMAN'})

    def test_session_lock_prevents_concurrent_control_and_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.lock'
            with avito.session_lock(path):
                with self.assertRaisesRegex(RuntimeError, 'already using'):
                    with avito.session_lock(path):
                        self.fail('Second collector acquired the same session')
            with avito.session_lock(path):
                pass


if __name__ == '__main__':
    unittest.main()

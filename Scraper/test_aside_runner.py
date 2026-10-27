"""Offline collector regressions; no network, Aside or GitHub required."""
import argparse
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).parent / 'skills/collect-avito/scripts'
spec = importlib.util.spec_from_file_location('aside_runner', SCRIPTS / 'run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def item(identity='123'):
    return dict(id=identity, url=f'https://www.avito.ru/moskva/vakansii/example_{identity}',
                listing_rank=1, promoted=True, location_raw='Москва')


def courier_controls():
    # Actual duplicated label/input controls saved by the first-run smoke.
    return [dict(label='Курьер', marker='params[144170]/checkbox/3201765', value=None),
            dict(label='Курьер', marker='params[144170]/checkbox/toggle', value='3201765'),
            dict(label='Любой', marker='params[196929]/toggle', value='all')]


def raw(identity='123'):
    return dict(id=identity, state='DETAIL', title='Работа', source_url=item(identity)['url'],
        observed_at='2026-10-27T14:00:00Z', description='Полное описание\nВторая строка',
        fields=[dict(name='Новый параметр', value='Да')], employer_raw=['Компания'],
        location_raw=['Москва, пос. Коммунарка'], parameter_audit={'rendered_complete': True})


class RunnerTests(unittest.TestCase):
    def test_city_anomaly_can_be_missing_or_explicit_search_fallback(self):
        data = dict(raw(), location_raw=['Свердловская обл., Белоярский муниципальный округ, промышленный район Прохладный'])
        regions = {'Екатеринбург':'Свердловская область'}
        plan = dict(city_anomaly_policy='blank', geography='Екатеринбург',
                    listing_url='https://www.avito.ru/ekaterinburg/vakansii', filter_evidence='Екатеринбург подтверждён')
        row, _, region = runner.reviewed_row(data,item(),regions,plan)
        self.assertEqual((row['city'],row['city__source'],region),('','missing','Свердловская область'))
        row, _, region = runner.reviewed_row(data,item(),regions,dict(plan,city_anomaly_policy='search_filter'))
        self.assertEqual((row['city'],row['city__source']),('Екатеринбург','search_filter'))
        self.assertEqual(row['city__evidence'],plan['listing_url'])
        self.assertEqual(row['location_observed'],data['location_raw'][0])
        self.assertTrue(row['city_anomaly'])
        for changes in [dict(geography='Все регионы'),dict(filter_evidence='')]:
            with self.assertRaises(ValueError):
                runner.reviewed_row(data,item(),regions,dict(plan,city_anomaly_policy='search_filter',**changes))
        with self.assertRaises(ValueError):
            runner.reviewed_row(dict(data,id='wrong'),item(),regions,dict(plan,city_anomaly_policy='search_filter'))

    def test_aside_target_is_pinned_across_capture_and_profile_changes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(runner.os.environ, {}, clear=True):
            diag = Path(directory) / 'diagnostics'
            with patch.object(runner, 'find_aside', return_value='aside'), \
                    patch.object(runner, 'aside_command', return_value=['aside', '--host', 'local', '--account', 'u0', '--profile', 'first']) as choose:
                runner.pin_aside_target(diag)
                self.assertEqual(runner.os.environ['TL_ASIDE_PROFILE'], 'first')
                self.assertEqual(runner.os.environ['TL_ASIDE_ACCOUNT'], 'u0')
                runner.os.environ.pop('TL_ASIDE_ACCOUNT')
                runner.os.environ.pop('TL_ASIDE_PROFILE')
                runner.pin_aside_target(diag)
                self.assertEqual(runner.os.environ['TL_ASIDE_PROFILE'], 'first')
                choose.assert_called_once()
                self.assertEqual(runner.os.environ['TL_ASIDE_ACCOUNT'], 'u0')
            runner.os.environ['TL_ASIDE_PROFILE'] = 'different'
            with self.assertRaisesRegex(RuntimeError, 'pinned'):
                runner.pin_aside_target(diag)
            self.assertEqual(runner.read(diag/'aside-target.local.json')['TL_ASIDE_PROFILE'], 'first')

    def test_publish_retry_reuses_own_commit_and_existing_pr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run_id = 'publish_retry'
            out, service, diag = runner.paths(root, run_id)
            runner.write(service/'manifest.json', dict(status='complete', files={}, collection={}, coverage={'accepted':1}))
            state = {'commits': 0, 'pushes': 0}
            def output(command, **kwargs):
                if command[:2] == ['git','branch']:
                    return 'codex/publish_retry'
                if command[:5] == ['git','diff','--cached','--name-only']:
                    return str((out/'raw/123.json').relative_to(root)) if not state['commits'] else ''
                if command[:2] == ['git','commit']:
                    state['commits'] += 1
                if command[:2] == ['git','push']:
                    state['pushes'] += 1
                    if state['pushes'] == 1:
                        raise subprocess.CalledProcessError(1,command)
                if command[:3] == ['gh','pr','create']:
                    self.fail('Existing PR must be reused')
                return ''
            def run(command, **kwargs):
                return subprocess.CompletedProcess(command,0,stdout=json.dumps({'state':'OPEN','url':'https://github.com/example/repo/pull/1'}))
            args = argparse.Namespace(run_id=run_id, retry_reason=None, summary='Sample', title='Data', prepare_only=False)
            with patch.object(runner,'ROOT',root), patch.object(runner.subprocess,'check_output',side_effect=output), patch.object(runner.subprocess,'run',side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    runner.publish(args)
                runner.record_failure(diag,'publish','push failed','push')
                args.retry_reason = 'Connection restored'
                runner.publish(args)
            self.assertEqual(state, {'commits':1,'pushes':2})

    def test_queue_preserves_unique_order_and_requires_enough_real_links(self):
        listing = dict(state='LISTING', items=[item('123'), item('123'), item('456')])
        queue = runner.queue_from(listing, 2)
        self.assertEqual([q['id'] for q in queue], ['123', '456'])
        self.assertEqual([q['listing_rank'] for q in queue], [1, 2])
        with self.assertRaisesRegex(ValueError, 'Only 2'):
            runner.queue_from(listing, 3)
        for url in ['https://avito.ru.evil.test/vakansii/a_123',
                    'https://user:pass@www.avito.ru/moskva/vakansii/a_123',
                    'https://www.avito.ru/moskva/vakansii/a_999']:
            with self.assertRaises(ValueError):
                runner.queue_from(dict(state='LISTING', items=[dict(item(), url=url)]), 1)

    def test_checks_catch_real_selector_gap_and_keep_description_out_of_geography(self):
        data = raw()
        data['description'] += '\nРаботать в другом регионе, Екатеринбург'
        row, review, region = runner.reviewed_row(data, item(),
            {'Москва': 'Москва', 'Коммунарка': 'Москва'}, {})
        self.assertEqual(row['city'], 'Коммунарка')
        self.assertEqual(row['description'], data['description'])
        self.assertEqual(row['profession'], '')
        self.assertEqual(row['Новый параметр'], 'Да')
        self.assertFalse(review['semantic_review_performed'])
        for change in [dict(location_raw=[]), dict(parameter_audit={}), dict(id='999'),
                       dict(description=''), dict(fields=[{'name': 'id', 'value': 'oops'}])]:
            with self.assertRaises(ValueError):
                runner.reviewed_row(dict(data, **change), item(), {'Москва': 'Москва'}, {})

    def test_unknown_city_requires_evidence_and_does_not_trust_url_slug(self):
        data = dict(raw(), location_raw=['Москва, д. Сосенки'])
        city, region, _ = runner.placement(data, item(), {'Сосенки': 'Москва'})
        self.assertEqual(city, 'Сосенки')
        with self.assertRaises(ValueError):
            runner.placement(data, item(), {})
        with self.assertRaises(ValueError):
            runner.placement(data, item(), {}, {'city': 'Москва', 'region': 'Москва', 'evidence': 'not in address'})
        data = dict(raw(), location_raw=['Москва, улица Тверь, дом 3'])
        self.assertEqual(runner.placement(data, item(), {'Москва': 'Москва', 'Тверь': 'Тверская область'})[0], 'Москва')
        with self.assertRaisesRegex(ValueError, 'Unknown locality'):
            runner.placement(dict(raw(), location_raw=['Москва, пос. Неизвестный']), item(), {'Москва': 'Москва'})

    def test_empty_city_requires_explicit_confirmation_and_visible_evidence(self):
        data = dict(raw(), location_raw=['Свердловская обл., Белоярский муниципальный округ'])
        override = {'city': '', 'region': 'Свердловская область', 'evidence': 'Свердловская обл.', 'city_unknown_confirmed': True}
        self.assertEqual(runner.placement(data, item(), {}, override)[:2], ('', 'Свердловская область'))
        for invalid in [dict(override, city_unknown_confirmed=False), dict(override, evidence='Москва'), dict(override, region='')]:
            with self.assertRaises(ValueError):
                runner.placement(data, item(), {}, invalid)

    def test_location_address_excludes_metro_and_stops_at_streets(self):
        regions = {'Екатеринбург': 'Свердловская область', 'Тверь': 'Тверская область'}
        for address in ['Свердловская обл., Екатеринбург\nПлощадь 1905 года\nдо 5 мин.',
                        'Свердловская обл., Екатеринбург, пл. 1905 года\nПлощадь 1905 года',
                        'Свердловская обл., Екатеринбург, Первая линия\nУралмаш',
                        'Свердловская обл., Екатеринбург, Сибирский тракт, 16-й километр, 2']:
            with self.subTest(address=address):
                self.assertEqual(runner.placement(dict(raw(), location_raw=[address]), item(), regions)[0], 'Екатеринбург')
        with self.assertRaisesRegex(ValueError, 'Unknown locality'):
            runner.placement(dict(raw(), location_raw=['Свердловская обл., Екатеринбург, пос. Неизвестный\nТверь']), item(), regions)

    def test_interrupted_transfer_preserves_raw_and_refuses_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); sandbox = root / 'sandbox'; out = root / 'out'; diag = root / 'diag'
            diag.mkdir(); runner.write(sandbox / 'raw/123.json', raw())
            runner.write(sandbox / 'checkpoint.json', {'state': 'CAPTURED', 'completed_ids': ['123']})
            runner.import_sandbox(sandbox, out, diag)
            self.assertEqual(runner.read(out / 'raw/123.json'), raw())
            runner.write(sandbox / 'raw/123.json', dict(raw(), title='Changed'))
            (sandbox/'raw/456.json.tmp').write_text('{unfinished')
            with self.assertRaisesRegex(ValueError, 'immutable'):
                runner.import_sandbox(sandbox, out, diag)
            self.assertEqual(runner.read(out / 'raw/123.json')['title'], 'Работа')

    def test_profession_is_observed_deduplicated_and_not_inferred_from_other_fields(self):
        listing = dict(selected_controls=courier_controls(), heading='Курьер')
        self.assertEqual(runner.profession_from_listing(listing), 'Курьер')
        self.assertEqual(runner.profession_from_listing(listing, '  курьер '), 'Курьер')
        # Whitespace from the DOM is normalized, including nonbreaking spaces.
        truck = dict(label='Водитель\u00a0 грузового транспорта', marker='params[144170]/checkbox/22283175', value=None)
        controls = courier_controls() + [truck]
        self.assertEqual(runner.profession_from_listing({'selected_controls': controls}),
                         'Водитель грузового транспорта; Курьер')
        other = dict(label='Курьер', marker='params[999]/checkbox/1', value='1')
        for controls in [[], [other], [dict(label='Все', marker='params[144170]/all', value='all')]]:
            self.assertEqual(runner.profession_from_listing(dict(selected_controls=controls,
                profession_filter={'observed': True, 'selected_count': 0},
                source_url='https://www.avito.ru/moskva/vakansii/kurer', heading='Работа и вакансии')), '')

    def test_profession_mismatch_and_unreadable_filter_are_rejected(self):
        for listing, requested in [({'selected_controls': courier_controls()}, 'Водитель'),
                ({'selected_controls': []}, 'Курьер'),
                ({'selected_controls': [], 'heading': 'Вакансии по профессии Курьер'}, ''),
                ({'selected_controls': [], 'heading': 'Курьер: работа и вакансии в Москве'}, ''),
                ({'selected_controls': [], 'profession_filter': {'observed': False, 'selected_count': 0}}, ''),
                ({'selected_controls': [], 'profession_filter': {'observed': True, 'selected_count': 1}}, ''),
                ({'selected_controls': [dict(label='', marker='params[144170]/checkbox/1')]}, ''),
                ({}, '')]:
            with self.subTest(listing=listing, requested=requested), self.assertRaises(ValueError):
                runner.profession_from_listing(listing, requested)

    def test_inconsistent_plan_stops_capture_and_finalize_before_browser_or_export(self):
        for profession in ['', 'Водитель']:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory); out, service, diag = runner.paths(root, 'bad_profession')
                runner.write(service/'plan.json', {'profession': profession})
                runner.write(out/'listing.json', {'selected_controls': courier_controls()})
                runner.write(out/'queue.json', [item()])
                args = argparse.Namespace(run_id='bad_profession', retry_reason=None)
                with patch.object(runner, 'ROOT', root), patch.object(runner, 'preflight') as probe, \
                        patch.object(runner, 'browser') as browser:
                    for stage in [runner.capture, runner.finalize]:
                        with self.assertRaisesRegex(ValueError, 'Plan profession'):
                            stage(args)
                    probe.assert_not_called(); browser.assert_not_called()
                self.assertFalse((out/'exports').exists())
                self.assertFalse((service/'manifest.json').exists())

    def test_profession_validation_rejects_missing_key_and_changed_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            listing = Path(directory)/'listing.json'
            runner.write(listing, {'selected_controls': [],
                'profession_filter': {'observed': True, 'selected_count': 0}})
            with self.assertRaisesRegex(ValueError, 'missing'):
                runner.validate_plan_profession({}, listing)
            plan = {'profession': '', 'listing_sha256': runner.sha(listing)}
            runner.validate_plan_profession(plan, listing)
            runner.write(listing, {'selected_controls': [], 'heading': 'Changed',
                'profession_filter': {'observed': True, 'selected_count': 0}})
            with self.assertRaisesRegex(ValueError, 'Frozen listing changed'):
                runner.validate_plan_profession(plan, listing)

    def test_freeze_resumes_even_if_interrupted_before_request_was_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out, service, diag = runner.paths(root, 'resume_test')
            diag.mkdir(parents=True); out.mkdir(parents=True)
            for file in runner.AUTOMATION_FILES:
                path = root/file; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((runner.ROOT/file).read_bytes())
            def fake_browser(job, output, diagnostics):
                self.assertTrue((diagnostics/'freeze_request.json').exists())
                runner.write(diagnostics/'listing.json', dict(state='LISTING', source_url=item()['url'],
                    heading='Вакансии', selected_controls=courier_controls(), items=[item()]))
                cp = dict(state='READY', completed_ids=[], next_id=None)
                runner.write(diagnostics/'checkpoint.json', cp)
                return cp, 0
            args = argparse.Namespace(run_id='resume_test', resume=True, url=item()['url'], limit=1,
                geography='Москва', profession='', segments='', filter_evidence='Москва, без фильтра', agent_model='gpt-6-luna')
            with patch.object(runner,'ROOT',root), patch.object(runner,'preflight'), \
                 patch.object(runner,'browser',side_effect=fake_browser), patch.object(runner,'dirty_automation',return_value=[]), \
                 patch.object(runner.subprocess,'check_output',return_value='abc'):
                runner.freeze(args)
            self.assertEqual(runner.read(out/'queue.json')[0]['id'], '123')
            self.assertEqual(runner.read(service/'plan.json')['execution_model'], 'gpt-6-luna')
            self.assertEqual(runner.read(service/'plan.json')['profession'], 'Курьер')

    def test_finalize_registers_and_builds_once_without_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); run_id = 'test_sample'
            out, service, diag = runner.paths(root, run_id)
            runner.write(root / 'Data/dataset/sources.json', {'schema_version': '1', 'exports': []})
            (root / 'Data/README.md').write_text('# Data\n')
            (root / 'Data/dataset/README.md').write_text('Текущий состав: старый\n')
            runner.write(out / 'queue.json', [item()]); runner.write(out / 'raw/123.json', raw())
            runner.write(out / 'listing.json', {'selected_controls': courier_controls()})
            runner.write(service / 'plan.json', {'setup_seconds': 1, 'profession': 'Курьер', 'segments': 'all'})
            runner.write(diag / 'checkpoint.json', {'capture_seconds': 0.5})
            placements = root / 'placements.json'
            runner.write(placements, {'123': {'city': 'Коммунарка', 'region': 'Москва', 'evidence': 'пос. Коммунарка'}})
            args = argparse.Namespace(run_id=run_id, placements=placements, review_model='gpt-6.1-terra')
            with patch.object(runner, 'ROOT', root):
                original_write = runner.write
                def fail_completion(path, data):
                    if path == service/'manifest.json':
                        raise OSError('simulated interruption after registry write')
                    return original_write(path, data)
                with patch.object(runner,'write',side_effect=fail_completion):
                    with self.assertRaisesRegex(OSError, 'simulated interruption'):
                        runner.finalize(args)
                self.assertEqual(len(runner.read(root/'Data/dataset/sources.json')['exports']), 1)
                self.assertFalse((service/'manifest.json').exists())
                runner.finalize(args)
                before = (out / 'exports/vacancies_flat.csv').read_bytes()
                with self.assertRaisesRegex(ValueError, 'overwrite'):
                    runner.finalize(args)
            registry = runner.read(root / 'Data/dataset/sources.json')
            self.assertEqual(len(registry['exports']), 1)
            shared, _ = runner.build(root, registry)
            self.assertEqual(shared, (root / 'Data/dataset/vacancies.csv').read_bytes())
            self.assertEqual(before, (out / 'exports/vacancies_flat.csv').read_bytes())
            manifest = runner.read(service / 'manifest.json')
            self.assertEqual(manifest['coverage']['accepted'], 1)
            with (out/'exports/vacancies_flat.csv').open(encoding='utf-8-sig', newline='') as stream:
                self.assertEqual(next(csv.DictReader(stream))['profession'], 'Курьер')
            self.assertEqual(manifest['collection']['profession'], 'Курьер')
            self.assertTrue(all('diagnostics' not in name for name in manifest['files']))


if __name__ == '__main__':
    unittest.main()

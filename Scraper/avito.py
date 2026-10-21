"""Checkpointed Avito collector through the installed gstack browser. No direct HTTP.

No passwords, cookies or browser profiles are read by this program.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from normalize import salary as normalize_salary
from urllib.parse import urlsplit, parse_qsl

ROOT = Path(__file__).resolve().parent
BROWSE = Path.home() / '.codex/skills/gstack/browse/dist/browse'
ISOLATED = False
BOUND_TAB = None
ACCESS_STOP = ROOT.parent / '.gstack/avito-access-stop.json'


def browser_env():
    env = os.environ.copy()
    if ISOLATED:
        env['BROWSE_STATE_FILE'] = str(ROOT / '.gstack/browse.json')
        env['CHROMIUM_PROFILE'] = str(ROOT / '.gstack/chromium-profile')
        env['BROWSE_HEADED'] = '1'
    return env


@contextmanager
def session_lock(path=None):
    path = path or ROOT / '.gstack' / ('isolated-collector.lock' if ISOLATED else 'project-collector.lock')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as handle:
        path.chmod(0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another collector command is already using this browser session') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in {'www.avito.ru', 'avito.ru'} or p.username or p.password or p.port:
        raise ValueError('Expected an HTTPS Avito URL without credentials or custom port')
    if '/vakansii' not in p.path or re.search(r'/(logout|signout|delete|remove|unsubscribe)(/|$)', p.path, re.I):
        raise ValueError('Expected a vacancy search or detail URL')
    return url


def search_identity(url):
    p = urlsplit(validate_url(url))
    # Observed Avito redirect drops localPriority=0. Context is tracking only.
    query = sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if k != 'context' and not (k == 'localPriority' and v == '0'))
    return p.path.rstrip('/'), query


def browser(*args):
    if ACCESS_STOP.exists() and args[0] in {'goto', 'newtab', 'reload', 'click', 'press', 'fill', 'select'}:
        # A stop spans profiles; changing driver is not recovery from an IP restriction.
        local_navigation = args[0] in {'goto', 'newtab'} and str(args[1]).startswith(('file://', 'http://127.0.0.1', 'http://localhost'))
        if not local_navigation:
            raise RuntimeError('Access stop is active. Restore the saved tab manually, then use recover with the same session; no new Avito navigation is allowed')
    p = subprocess.run([str(BROWSE), *map(str, args)], capture_output=True, text=True, timeout=65, env=browser_env())
    if p.returncode:
        # Deliberately do not echo potentially sensitive daemon diagnostics.
        raise RuntimeError(f'gstack command {args[0]} failed, exit={p.returncode}')
    return p.stdout.strip()


def observe():
    fd, name = tempfile.mkstemp(prefix='avito-observation-', suffix='.json', dir=ROOT)
    os.close(fd)
    path = Path(name)
    try:
        browser('eval', ROOT / 'extract.js', '--out', path)
        data = json.loads(path.read_text())
        validate_url(data['source_url'])
        if data.get('state') == 'NEEDS_HUMAN':
            atomic_json(ACCESS_STOP, {'channel': 'gstack', 'browser_session': 'isolated' if ISOLATED else 'project',
                'tab_id': BOUND_TAB, 'url': data['source_url'], 'observed_at': data.get('observed_at'),
                'reason': data.get('reason'), 'new_navigation_stopped': True})
        return data
    finally:
        path.unlink(missing_ok=True)


def settled_observation():
    # Read the same DOM again; never reload/retry a blocked URL.
    for attempt in range(3):
        data = observe()
        if data['state'] != 'UNKNOWN_PAGE':
            return data
        if attempt < 2:
            time.sleep(1)
    return data


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w') as handle:
        tmp.chmod(0o600)
        handle.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def valid_detail(detail, expected_id):
    if detail.get('state') != 'DETAIL' or str(detail.get('id')) != str(expected_id):
        return False
    if detail.get('source_url'):
        validate_url(detail['source_url'])
        match = re.search(r'_(\d+)$', urlsplit(detail['source_url']).path)
        if not match or match.group(1) != str(expected_id):
            return False
    return bool(detail.get('title') and detail.get('description') and detail.get('params_blocks'))


def classify_conflicts(detail):
    """Conservative candidate detector. Does not assert all conflicts were found."""
    description = detail.get('description') or ''
    field = next((x for x in detail.get('fields', []) if x['name'] == 'Частота выплат'), None)
    found = []
    if field and re.search(r'раз\s+в\s+неделю', field['value'], re.I):
        # Exact phrase only. Mixed Cyrillic/Latin spelling needs separate NLP.
        match = re.search(r'[^\n.!?]*(?:выплат|оплат)[^\n.!?]*каждый\s+день[^\n.!?]*', description, re.I)
        if match:
            found.append({'vacancy_id': detail['id'], 'url': detail['source_url'],
                          'feature': 'payout_frequency', 'description_value_raw': match.group().strip(),
                          'structured_value_raw': field['value'], 'structured_field': field['name'],
                          'mismatch_type': 'frequency_conflict_candidate', 'resolution': None})
    return found


def control_state(marker):
    selector = f'[data-marker="{marker}"]'
    expr = '''(() => { const e=document.querySelector(SELECTOR); if(!e)return null;
    const input=e.matches('input')?e:e.querySelector('input');
    return {type:e.getAttribute('role')||input?.type,checked:input?input.checked:e.getAttribute('aria-checked')==='true'}; })()'''
    return json.loads(browser('js', expr.replace('SELECTOR', json.dumps(selector))))


def apply_recipe(path, output, tab_id=None):
    recipe = json.loads(Path(path).read_text())
    validate_url(recipe['start_url'])
    # Recipes may only change known search controls, never arbitrary buttons.
    for action in recipe['controls']:
        if not re.fullmatch(r'(?:params\[\d+\]/[A-Za-z0-9_/-]+|filters/localPriority/(?:localPriority|toggle))', action['marker']) or type(action['checked']) is not bool:
            raise ValueError('Invalid filter control recipe')
    if tab_id is None:
        tab = json.loads(browser('newtab', recipe['start_url'], '--json'))
    else:
        browser('tab', tab_id)
        tab = {'tabId': tab_id}
    atomic_json(Path(output), {'state': 'CONFIGURING', 'tab_id': tab['tabId'], 'recipe': recipe})
    checks = []
    for action in recipe['controls']:
        page = settled_observation()
        if page['state'] == 'NEEDS_HUMAN':
            result = {'state': 'NEEDS_HUMAN', 'tab_id': tab['tabId'], 'checks': checks}
            atomic_json(Path(output), result)
            return result
        if urlsplit(page['source_url']).path.split('/')[1] != urlsplit(recipe['start_url']).path.split('/')[1]:
            raise ValueError('Filter recipe city differs from the current page')
        state = control_state(action['marker'])
        if not state or state['type'] not in {'checkbox', 'radio', 'switch'}:
            raise ValueError(f'Unverified search control: {action["marker"]}')
        if state['type'] == 'radio' and not action['checked']:
            raise ValueError('Select the desired radio option instead of unchecking it')
        for click_attempt in range(2):
            if state and state['checked'] == action['checked']:
                break
            if settled_observation()['state'] == 'NEEDS_HUMAN':
                result = {'state': 'NEEDS_HUMAN', 'tab_id': tab['tabId'], 'checks': checks}
                atomic_json(Path(output), result)
                return result
            browser('click', f'[data-marker="{action["marker"]}"]')
            # Hydration can replace an SSR control and discard an early click.
            # Poll UI state, and retry only if the requested value is absent.
            for poll in range(6):
                time.sleep(0.5)
                state = control_state(action['marker'])
                if state and state['checked'] == action['checked']:
                    break
            time.sleep(1)
            state = control_state(action['marker'])
        if not state or state['checked'] != action['checked']:
            raise ValueError(f'Search control did not apply: {action["marker"]}')
        checks.append(dict(action, actual=state['checked']))
    final = settled_observation()
    # Recheck every control after all changes, not just immediately after each.
    verified = all((control_state(x['marker']) or {}).get('checked') == x['checked'] for x in recipe['controls'])
    result = {'state': 'NEEDS_HUMAN' if final['state'] == 'NEEDS_HUMAN' else ('FILTERS_VERIFIED' if verified and final['state'] in {'LISTING', 'EMPTY_RESULT'} else 'FILTERS_UNVERIFIED'),
              'tab_id': tab['tabId'], 'recipe': recipe, 'checks': checks, 'observation': final}
    atomic_json(Path(output), result)
    return result


def slice_identity(url):
    """Page number changes position, never profession/region/filter identity."""
    path, query = search_identity(url)
    return path, [(k, v) for k, v in query if k != 'p']


def content_hash(detail):
    # Dates/views/collection timestamps change independently of vacancy content.
    keys = ('title', 'salary_raw', 'params_blocks', 'description', 'employer_raw', 'location_raw')
    return hashlib.sha256(json.dumps({k: detail.get(k) for k in keys},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def build_catalog(root):
    """Offline, reproducible index. No disappearance => closure inference."""
    observations = {}
    for path in sorted(Path(root).rglob('detail_*.json')):
        data = json.loads(path.read_text())
        if not valid_detail(data, data.get('id')):
            continue
        observations.setdefault(str(data['id']), []).append({
            'path': str(path.resolve()), 'observed_at': data.get('observed_at'),
            'content_hash': content_hash(data), 'source_url': data.get('source_url'),
            'slice': data.get('collection_context'), 'legacy': 'collection_context' not in data})
    for rows in observations.values():
        rows.sort(key=lambda r: (r['observed_at'] or '', r['path']))
        for i, row in enumerate(rows):
            row['observation_kind'] = 'NEW' if i == 0 else (
                'REOBSERVED' if row['content_hash'] == rows[i-1]['content_hash'] else 'CHANGED')
    return {'executor': 'AI agent', 'unique_ids': len(observations),
            'detail_observations': sum(map(len, observations.values())),
            'closure_inference': 'not_performed', 'items': observations}


def collect(args, read=settled_observation, navigate=None):
    navigate = navigate or (lambda url: browser('goto', validate_url(url)))
    out = Path(args.output).resolve()
    manifest_path = out / 'manifest.json'
    now = lambda: dt.datetime.now(dt.timezone.utc).isoformat()
    session = 'isolated' if ISOLATED else 'project'
    if args.resume:
        run = json.loads(manifest_path.read_text())
        if args.url != run['requested_url'] or run.get('browser_session', 'project') != session:
            raise ValueError('Resume requires the same requested URL and browser session')
        # Upgrade old manifests in place without losing raw observations.
        if 'queue' not in run:
            run.update(queue=list({str(x['id']): x for x in (run.get('listing') or {}).get('items', [])}.values()),
                       pages=[], visited_urls=[], seen_ids=[], max_pages=1)
            if run.get('listing'):
                run['pages'] = [run['listing']]
                run['visited_urls'] = [run['listing']['source_url']]
                run['seen_ids'] = [x['id'] for x in run['queue']]
        if getattr(args, 'extend', False):
            if args.limit < len(run['details_done']) or args.max_pages < len(run['pages']):
                raise ValueError('Extended budgets cannot be less than completed work')
            run.update(limit=args.limit, max_pages=args.max_pages)
            run['state'] = 'COLLECTING'
        elif run['state'] in {'COMPLETE_SAMPLE', 'EXHAUSTED', 'EMPTY_RESULT'}:
            return run
        if args.tab_id != run['tab_id']:
            current = read()
            target = run.get('pending_url') or (run.get('listing') or {}).get('source_url') or run['requested_url']
            expected = current.get('state') in {'LISTING', 'EMPTY_RESULT'} and search_identity(current['source_url']) == search_identity(target)
            restored_detail = current.get('state') == 'DETAIL' and str(current.get('id')) == str(run.get('pending_id'))
            if not (expected or restored_detail):
                raise ValueError('Replacement tab must prove the same search or the pending vacancy')
            run.setdefault('previous_tab_ids', []).append(run['tab_id'])
            run['tab_id'] = args.tab_id
    else:
        if manifest_path.exists():
            raise ValueError('Run already exists: use --resume or a fresh output directory')
        run = {'schema_version': 3, 'requested_url': args.url, 'tab_id': args.tab_id,
               'limit': args.limit, 'max_pages': getattr(args, 'max_pages', 1),
               'browser_session': session, 'started_at': now(), 'details_done': [], 'conflicts': [],
               'state': 'STARTING', 'listing': None, 'queue': [], 'pages': [],
               'seen_ids': [], 'visited_urls': [], 'duplicates': []}

    def save(state=None, reason=None):
        if state:
            run['state'] = state
        if reason:
            run['stop_reason'] = reason
        run['updated_at'] = now()
        run['remaining_ids'] = [x['id'] for x in run['queue'] if x['id'] not in run['details_done']]
        atomic_json(manifest_path, run)
        return run

    def blocked(data):
        run['last_observation'] = data
        save('NEEDS_HUMAN', data.get('reason', 'access_restriction_or_captcha'))
        if read is settled_observation:
            # Screenshot failure must not erase the checkpoint or start another navigation.
            try:
                browser('screenshot', '--viewport', out / 'access_restriction.png')
                run['restriction_screenshot'] = str(out / 'access_restriction.png')
            except (RuntimeError, subprocess.TimeoutExpired):
                run['restriction_screenshot_error'] = True
            save()
        return run

    def go(url, kind, item_id=None):
        validate_url(url)
        run.update(pending_url=url, pending_kind=kind, pending_id=item_id)
        save('NAVIGATING')  # BEFORE navigation: interruption is always resumable.
        try:
            navigate(url)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            # Navigation timeout is not a CAPTCHA. First reread this same tab.
            try:
                data = read()
                expected = data.get('state') in {'NEEDS_HUMAN', kind, 'EMPTY_RESULT' if kind == 'LISTING' else 'DETAIL'}
                if expected:
                    return data
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
            save('BROWSER_ERROR', str(exc))
            raise
        return read()

    def accept_listing(data):
        if data['state'] == 'NEEDS_HUMAN':
            blocked(data)
            return False
        if data['state'] not in {'LISTING', 'EMPTY_RESULT'}:
            run['last_observation'] = data
            save('LISTING_UNVERIFIED', 'No confirmed listing or explicit empty-result message')
            return False
        if slice_identity(data['source_url']) != slice_identity(run['requested_url']):
            raise ValueError('Restored listing does not match the requested search')
        expected_url = run.get('pending_url') if run.get('pending_kind') == 'LISTING' else run['requested_url']
        if search_identity(data['source_url']) != search_identity(expected_url):
            save('LISTING_UNVERIFIED', 'Pagination arrived at a different page')
            return False
        previous = set(run['seen_ids'])
        ids = [str(x['id']) for x in data.get('items', [])]
        if run['pages'] and (data['source_url'] in run['visited_urls'] or (ids and not set(ids) - previous)):
            run['last_observation'] = data
            save('PAGINATION_LOOP', 'Repeated URL or page with no new vacancy IDs')
            return False
        if data['state'] == 'LISTING' and not ids:
            save('LISTING_UNVERIFIED', 'Listing shell has no readable vacancy IDs')
            return False
        if data['state'] == 'EMPTY_RESULT' and run['pages']:
            save('LISTING_UNVERIFIED', 'Empty page after nonempty pages is not proof of exhaustion')
            return False
        page_index = len(run['pages']) + 1
        atomic_json(out / f'listing_{page_index:04}.json', data)
        if not run['pages']:
            atomic_json(out / 'listing.json', data)
        run['pages'].append(data)
        run['visited_urls'].append(data['source_url'])
        run['listing'] = data
        for item in data.get('items', []):
            item = dict(item, id=str(item['id']))
            validate_url(item['url'])
            if item['id'] in previous:
                run.setdefault('duplicates', []).append({'id': item['id'], 'page': page_index})
                continue
            previous.add(item['id'])
            run['seen_ids'].append(item['id'])
            item['discovered_on'] = data['source_url']
            item['selected_controls'] = data.get('selected_controls', [])
            run['queue'].append(item)
        run.pop('pending_kind', None)
        run.pop('pending_url', None)
        run.pop('pending_id', None)
        save('COLLECTING')
        return True

    save()
    try:
        if run['listing'] is None or run.get('pending_kind') == 'LISTING':
            current = read()  # Read manually restored page before deciding to navigate.
            target = run.get('pending_url') or run['requested_url']
            if current.get('state') == 'NEEDS_HUMAN':
                return blocked(current)
            if current.get('state') in {'LISTING', 'EMPTY_RESULT'}:
                # Initial/restored search must prove its identity, never silently switch filters.
                if slice_identity(current['source_url']) != slice_identity(run['requested_url']):
                    raise ValueError('Restored listing does not match the requested search')
            if run.get('pending_kind') == 'LISTING' and current.get('source_url') != target:
                current = go(target, 'LISTING')
            if not accept_listing(current):
                return run
        while True:
            pending = [x for x in run['queue'] if x['id'] not in run['details_done']]
            if len(run['details_done']) >= run['limit']:
                return save('COMPLETE_SAMPLE', 'detail_limit_reached; not proof of end of results')
            if pending:
                item = pending[0]
                path = out / f'detail_{item["id"]}.json'
                data = None
                if path.exists():
                    saved = json.loads(path.read_text())
                    if valid_detail(saved, item['id']) and saved.get('collection_context', {}).get('requested_url') == run['requested_url']:
                        data = saved  # Recover crash between durable card write and manifest write.
                if data is None:
                    if run['details_done']:
                        time.sleep(args.delay)
                    if args.resume and str(run.get('pending_id')) == item['id']:
                        data = read()
                        if data['state'] not in {'DETAIL', 'NEEDS_HUMAN'}:
                            data = go(item['url'], 'DETAIL', item['id'])
                    else:
                        data = go(item['url'], 'DETAIL', item['id'])
                    if data['state'] == 'NEEDS_HUMAN':
                        return blocked(data)
                    if not valid_detail(data, item['id']):
                        run.update(pending_id=item['id'], pending_kind='DETAIL', pending_url=item['url'], last_observation=data)
                        return save('DETAIL_UNVERIFIED', 'ID or required content not confirmed')
                    data['collection_context'] = {'requested_url': run['requested_url'],
                        'discovered_on': item.get('discovered_on', run['requested_url']),
                        'selected_controls': item.get('selected_controls', []),
                        'browser_session': session, 'channel': 'gstack DOM', 'run': str(out)}
                    data['normalized'] = {'salary': normalize_salary(data.get('salary_raw'), data.get('fields', []))}
                    data['content_hash'] = content_hash(data)
                    atomic_json(path, data)
                run['details_done'].append(item['id'])
                run['conflicts'].extend(classify_conflicts(data))
                atomic_json(out / 'conflicts.json', {'coverage': 'limited_rule_candidates_only', 'items': run['conflicts']})
                run.pop('pending_id', None)
                run.pop('pending_kind', None)
                run.pop('pending_url', None)
                save('COLLECTING')
                continue
            page = run['listing']
            if page['state'] == 'EMPTY_RESULT':
                return save('EMPTY_RESULT', 'explicit_empty_result_message')
            paging = page.get('pagination', {})
            if paging.get('end_confirmed'):
                return save('EXHAUSTED', 'visible_pagination_end_confirmed')
            if len(run['pages']) >= run['max_pages']:
                return save('COMPLETE_SAMPLE', 'page_limit_reached; not proof of end of results')
            next_url = paging.get('next_url')
            if not next_url:
                return save('PAGINATION_UNVERIFIED', 'No observed next link and no explicit end evidence')
            if slice_identity(next_url) != slice_identity(run['requested_url']):
                return save('PAGINATION_UNVERIFIED', 'Next link changes search filters or region')
            if next_url in run['visited_urls']:
                return save('PAGINATION_LOOP', 'Next URL already visited')
            time.sleep(args.delay)
            if not accept_listing(go(next_url, 'LISTING')):
                return run
    except KeyboardInterrupt:
        save('INTERRUPTED', 'Stopped by operator; pending action and durable cards preserved')
        raise
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        save('BROWSER_ERROR', str(exc))
        raise
    except ValueError as exc:
        save('VALIDATION_ERROR', str(exc))
        raise


def main():
    global ISOLATED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'open', 'inspect', 'collect', 'filters', 'status', 'stop', 'catalog', 'recover'])
    parser.add_argument('--url', default='https://www.avito.ru/all/vakansii')
    parser.add_argument('--tab-id', type=int)
    parser.add_argument('--handoff', action='store_true')
    parser.add_argument('--output', default='Data/avito_runs/sample')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--max-pages', type=int, default=1)
    parser.add_argument('--extend', action='store_true', help='Explicitly replace saved budgets on resume')
    parser.add_argument('--data-root', default='Data/avito_runs', help='Offline catalog input tree')
    parser.add_argument('--delay', type=float, default=5)
    parser.add_argument('--recipe')
    parser.add_argument('--isolated', action='store_true', help='Dedicated persistent headed gstack session in Scraper/.gstack')
    args = parser.parse_args()
    ISOLATED = args.isolated
    validate_url(args.url)
    if args.limit < 1 or args.max_pages < 1 or args.delay < 3:
        parser.error('Positive limit/max-pages required; delay at least 3 seconds')
    if args.extend and not args.resume:
        parser.error('--extend requires --resume')
    with session_lock():
        if args.command in {'run', 'collect', 'filters', 'recover', 'catalog'}:
            with session_lock(Path(args.output).resolve() / '.collector.lock'):
                execute(args, parser)
        else:
            execute(args, parser)


def execute(args, parser):
    global BOUND_TAB
    BOUND_TAB = args.tab_id
    if args.command == 'catalog':
        result = build_catalog(args.data_root)
        atomic_json(Path(args.output) / 'catalog.json', result)
        print(json.dumps({'unique_ids': result['unique_ids'], 'detail_observations': result['detail_observations']}, ensure_ascii=False))
        return
    if args.command == 'run':
        if args.resume or args.tab_id is not None:
            parser.error('run creates a new job; use collect --resume --tab-id for existing work')
        if (Path(args.output) / 'manifest.json').exists():
            raise ValueError('Run already exists: use collect --resume or a fresh output directory')
        if args.recipe:
            result = apply_recipe(args.recipe, Path(args.output) / 'filters.json')
            if result['state'] != 'FILTERS_VERIFIED':
                print(json.dumps({'state': result['state'], 'tab_id': result['tab_id']}, ensure_ascii=False))
                raise SystemExit(2)
            args.tab_id = result['tab_id']
            args.url = result['observation']['source_url']
        else:
            tab = json.loads(browser('newtab', args.url, '--json'))
            args.tab_id = tab['tabId']
        BOUND_TAB = args.tab_id
    if args.command == 'stop':
        if not args.isolated:
            parser.error('stop requires --isolated; shared browser cannot be stopped by this collector')
        print(browser('stop'))
        return
    if args.command == 'status':
        print(browser('status'))
        return
    if args.command == 'filters':
        if not args.recipe:
            parser.error('--recipe required')
        result = apply_recipe(args.recipe, Path(args.output) / 'filters.json', args.tab_id)
        print(json.dumps({'state': result['state'], 'tab_id': result['tab_id'],
                          'url': result.get('observation', {}).get('source_url')}, ensure_ascii=False))
        if result['state'] != 'FILTERS_VERIFIED':
            raise SystemExit(2)
        return
    if args.command == 'open':
        result = json.loads(browser('newtab', args.url, '--json'))
        print(json.dumps(result))
        if args.handoff:
            print(browser('handoff', 'Откройте выдачу вручную; при проверке выполните её самостоятельно.'))
        return
    if args.tab_id is None:
        parser.error('--tab-id must identify an explicitly opened scraper tab')
    browser('tab', args.tab_id)
    if args.command == 'recover':
        if not ACCESS_STOP.exists():
            print(json.dumps({'state': 'NO_ACCESS_STOP'}))
            return
        restriction = json.loads(ACCESS_STOP.read_text())
        if restriction.get('channel') != 'gstack' or restriction.get('browser_session') != ('isolated' if ISOLATED else 'project'):
            raise ValueError('Recovery must use the original driver and profile; Aside requires reading its preserved tab')
        restored = settled_observation()
        if restored['state'] not in {'LISTING', 'DETAIL', 'EMPTY_RESULT'} or search_identity(restored['source_url']) != search_identity(restriction['url']):
            raise ValueError('Original blocked URL has not been restored; no navigation performed')
        atomic_json(Path(args.output) / 'recovery.json', {'restriction': restriction, 'restored': restored, 'new_tab_id': args.tab_id})
        ACCESS_STOP.unlink()
        print(json.dumps({'state': 'ACCESS_RESTORED', 'tab_id': args.tab_id}))
        return
    if args.command == 'inspect':
        print(json.dumps(settled_observation(), ensure_ascii=False, indent=2))
        return
    if not args.resume:
        # The tab must already be on the desired search. No silent filter loss.
        current = browser('url')
        if search_identity(current) != search_identity(args.url):
            raise ValueError('Current URL differs from --url; inspect or explicitly open the intended search first')
    run = collect(args)
    print(json.dumps({'state': run['state'], 'details_done': len(run['details_done']),
                      'manifest': str(Path(args.output).resolve() / 'manifest.json')}, ensure_ascii=False))
    if run['state'] not in {'COMPLETE_SAMPLE', 'EXHAUSTED', 'EMPTY_RESULT'}:
        raise SystemExit(2)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'state': 'ERROR', 'reason': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)

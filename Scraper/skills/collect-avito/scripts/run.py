"""Reusable Aside collection: freeze, capture/resume, finalize, publish. Run --help."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import select
import subprocess
import sys
import time
from urllib.parse import urlsplit

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))
from utils.build_dataset import build
from check_capture import check
from utils.dataset_pr import validate_automation_changes

VERSION = '1.5'
AUTOMATION_FILES = ['Scraper/extract.js', 'Scraper/skills/collect-avito/SKILL.md'] + [
    'Scraper/skills/collect-avito/scripts/' + name for name in ('run.py', 'browser_batch.js', 'check_capture.py')]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dirty_automation(root):
    dirty = []
    for file in AUTOMATION_FILES:
        result = subprocess.run(['git', 'show', 'HEAD:' + file], cwd=root, capture_output=True)
        if result.returncode or result.stdout != (root/file).read_bytes():
            dirty.append(file)
    return dirty


def norm(value):
    return ' '.join(value.split())


def paths(root, run_id):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,119}', run_id):
        raise ValueError('Use a simple unique run_id, without paths')
    out = root / 'Data/avito_runs' / run_id
    service = root / 'Scraper/runs' / run_id
    return out, service, service / 'diagnostics'


def avito_url(url, identity=None):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or not (host == 'avito.ru' or host.endswith('.avito.ru'))
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or '/vakansii' not in parsed.path):
        raise ValueError('Expected an observed HTTPS Avito vacancies URL')
    if identity and not parsed.path.endswith('_' + identity):
        raise ValueError('Queue URL and ID differ')
    return url


def queue_from(listing, limit):
    if listing.get('state') != 'LISTING' or limit < 1:
        raise ValueError('Expected a listing and positive limit')
    queue, seen = [], set()
    for item in listing.get('items', []):
        identity = item.get('id', '')
        if not isinstance(identity, str) or not identity.isdigit():
            raise ValueError('Invalid listing ID')
        avito_url(item['url'], identity)
        if identity in seen:
            continue
        seen.add(identity)
        queue.append(dict(item, listing_rank=len(queue) + 1))
        if len(queue) == limit:
            return queue
    raise ValueError(f'Only {len(queue)} unique rendered cards; requested {limit}; do not invent pagination')


def preflight():
    subprocess.run([sys.executable, str(SCRIPTS / 'preflight.py'), '--probe-aside'], check=True, cwd=ROOT)


def import_sandbox(sandbox, out, diag):
    """Copy even a stopped batch; never replace an earlier raw observation."""
    for name in ['listing.json', 'checkpoint.json', 'timing.json', 'problem.snapshot.txt']:
        source = sandbox / name
        if source.exists():
            if name == 'checkpoint.json' and (diag/name).exists():
                previous = read(diag/name)
                write(diag/name, dict(previous, **read(source)))
            else:
                shutil.copy2(source, diag / name)
    raw_dir = sandbox / 'raw'
    if raw_dir.exists():
        (out / 'raw').mkdir(parents=True, exist_ok=True)
        for source in raw_dir.glob('*.json'):
            if not source.stem.isdigit():
                raise ValueError('Unexpected raw filename')
            target = out / 'raw' / source.name
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise ValueError(f'Raw observation is immutable: {target.name}; use a new run')
            if not target.exists():
                temporary = target.with_name(target.name + '.tmp')
                shutil.copy2(source, temporary)
                temporary.replace(target)


def browser(job, out, diag):
    # One-shot Aside buffers stdout until completion. An interactive handshake
    # records its sandbox BEFORE collection, so interruption cannot orphan raw.
    process = subprocess.Popen(['aside', 'repl'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, cwd=ROOT)
    chunks = bytearray()
    def until(pattern, timeout, offset=0):
        deadline = time.monotonic() + timeout
        while True:
            output = chunks[offset:].decode('utf-8', errors='replace')
            found = re.search(pattern, output)
            if found:
                return found
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise RuntimeError('Aside timed out; sandbox pointer and partial raw preserved')
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError('Aside disconnected; partial raw preserved')
            chunks.extend(chunk)
    sandbox = None
    stamp = str(time.time_ns())
    started = time.perf_counter()
    try:
        process.stdin.write(b'console.log("TL_SESSION="+pwd)\n'); process.stdin.flush()
        sandbox = Path(until(r'TL_SESSION=([^\r\n]+)\r?\n', 20)[1].strip())
        write(diag / 'sandbox.json', {'path': str(sandbox)})
        # One REPL RPC per navigation avoids Aside's per-evaluation 120s budget
        # becoming a timeout on the entire queue. No model calls between cards.
        jobs = [dict(job, close_tab=True)] if job['mode'] == 'freeze' else [
            dict(job, queue=[item], close_tab=index == len(job['queue'])-1)
            for index,item in enumerate(job['queue'])]
        done = list(job['completed_ids'])
        for part in jobs:
            part['completed_ids'] = done
            script = (SCRIPTS / 'browser_batch.js').read_text()
            script = script.replace('__JOB__', json.dumps(part, ensure_ascii=False), 1)
            script = script.replace('__EXTRACTOR__', json.dumps((ROOT / 'Scraper/extract.js').read_text()), 1)
            expression = 'await eval(' + json.dumps('(async()=>{' + script + '})()') + ')\n'
            offset = len(chunks)
            process.stdin.write(expression.encode()); process.stdin.flush()
            until(r'TL_FINISHED\r?\n', 140, offset)
            import_sandbox(sandbox, out, diag)
            cp = read(diag/'checkpoint.json')
            cp['capture_seconds'] = job.get('previous_capture_seconds', 0) + time.perf_counter()-started
            write(diag/'checkpoint.json', cp)
            done = cp['completed_ids']
            if cp['state'] not in ('READY', 'CAPTURED'):
                break
    finally:
        if process.stdin:
            process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate(); process.wait(timeout=3)
        process.stdout.close()
        (diag / f'aside-{stamp}.local.log').write_text(chunks.decode('utf-8', errors='replace'))
        if sandbox:
            import_sandbox(sandbox, out, diag)
    return read(diag / 'checkpoint.json'), time.perf_counter()-started


def freeze(args):
    out, service, diag = paths(ROOT, args.run_id)
    existing = out.exists() or service.exists()
    if existing and not args.resume:
        raise ValueError('Run already exists; capture resumes it, new collection needs a new run_id')
    if (service / 'manifest.json').exists() or (service / 'plan.json').exists():
        raise ValueError('Queue already frozen; use capture, not freeze')
    avito_url(args.url)
    preflight()
    started = time.perf_counter()
    diag.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    request = dict(url=args.url, limit=args.limit, geography=args.geography,
                   profession=args.profession, segments=args.segments, filter_evidence=args.filter_evidence)
    if existing and (diag/'freeze_request.json').exists() and read(diag / 'freeze_request.json') != request:
        raise ValueError('Resume must preserve the original filter request')
    write(diag / 'freeze_request.json', request)
    if existing and (diag / 'sandbox.json').exists():
        import_sandbox(Path(read(diag / 'sandbox.json')['path']), out, diag)
    if (diag / 'checkpoint.json').exists() and read(diag/'checkpoint.json')['state'] == 'READY':
        cp = read(diag/'checkpoint.json')
    else:
        cp, _ = browser(dict(mode='freeze', url=args.url, completed_ids=[]), out, diag)
    if cp['state'] != 'READY':
        raise ValueError(f'Stopped: {cp["state"]}; inspect local diagnostics')
    listing = read(diag / 'listing.json')
    queue = queue_from(listing, args.limit)
    write(out / 'listing.json', listing)
    write(out / 'queue.json', queue)
    write(service / 'plan.json', dict(run_id=args.run_id, geography=args.geography,
        profession=args.profession, segments=args.segments, filter_evidence=args.filter_evidence,
        listing_url=listing['source_url'], selected_controls=listing.get('selected_controls', []),
        selection_mode='first_N_unique_on_frozen_listing', limit=args.limit,
        created_at=datetime.now(timezone.utc).isoformat(), instruction_version=VERSION,
        extractor_sha256=sha(ROOT / 'Scraper/extract.js'), instruction_sha256=sha(SCRIPTS.parent / 'SKILL.md'),
        code_git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        setup_seconds=time.perf_counter() - started, live_route_verified=False,
        execution_model=args.agent_model,
        automation_sha256={file: sha(ROOT/file) for file in AUTOMATION_FILES},
        uncommitted_automation_files=dirty_automation(ROOT)))
    cp.update(next_id=queue[0]['id'], capture_seconds=0)
    write(diag / 'checkpoint.json', cp)
    print(json.dumps({'state': 'READY', 'queued': len(queue), 'heading': listing.get('heading'),
                      'selected_controls': listing.get('selected_controls')}, ensure_ascii=False))


def capture(args):
    out, service, diag = paths(ROOT, args.run_id)
    if (service / 'manifest.json').exists():
        raise ValueError('Completed run is immutable')
    preflight()
    plan = read(service / 'plan.json')
    if any(sha(ROOT/file) != value for file,value in plan['automation_sha256'].items()):
        raise ValueError('Collector changed; preserve old run and start a new observation')
    cp, queue = read(diag / 'checkpoint.json'), read(out / 'queue.json')
    # Recover an interrupted transfer before considering another navigation.
    if (diag / 'sandbox.json').exists():
        import_sandbox(Path(read(diag / 'sandbox.json')['path']), out, diag)
    pending = []
    done = []
    for item in queue:
        path = out / 'raw' / (item['id'] + '.json')
        if path.exists():
            data = read(path)
            if not check(data, item['id'])['structural_check_passed'] or not data.get('parameter_audit', {}).get('rendered_complete') or not data.get('location_raw'):
                raise ValueError(f'Saved {item["id"]} needs review; never overwrite or silently skip it')
            done.append(item['id'])
        else:
            pending.append(item)
    if not pending:
        print(json.dumps({'state': 'CAPTURED', 'captured': len(done), 'remaining': 0}))
        return
    cp2, elapsed = browser(dict(mode='capture', queue=pending[:args.batch_size], completed_ids=done,
                               previous_capture_seconds=cp.get('capture_seconds', 0)), out, diag)
    cp2['capture_seconds'] = cp.get('capture_seconds', 0) + elapsed
    write(diag / 'checkpoint.json', cp2)
    print(json.dumps(dict(cp2, captured=len(cp2['completed_ids']), remaining=len(queue)-len(cp2['completed_ids']))))
    if cp2['state'] != 'CAPTURED':
        raise ValueError('Stopped; inspect diagnostics and repair the shared collector before resuming')


def placement(data, item, regions, override=None):
    location = norm('\n'.join(data.get('location_raw', [])))
    if not location:
        raise ValueError(f'Missing visible location: {item["id"]}')
    if override:
        city, region = override['city'], override['region']
        if norm(override.get('evidence', '')) not in location or not override.get('evidence'):
            raise ValueError('Placement override requires an exact quote from visible location')
        if not re.search(r'(?<!\w)' + re.escape(city) + r'(?!\w)', location):
            raise ValueError('Placement city must occur in visible location')
        return city, region, location
    # Match whole locality components only. Never match a city name inside a street/object.
    matches = []
    for component in re.split(r'[,;\n]', '\n'.join(data.get('location_raw', []))):
        component = norm(component)
        if re.search(r'(?:^|\s)(?:ул\.|улица|проспект|пр-т|шоссе|пер\.|переулок|дом)(?:\s|$)|^д\.\s*\d', component, re.I):
            break
        locality = re.sub(r'^(?:г\.|город |пос\.|посёлок |поселок |д\.|деревня |с\.|село |пгт\.?\s)\s*', '', component)
        if locality in regions:
            matches.append((locality, regions[locality]))
        elif locality != component:
            raise ValueError(f'Unknown locality {locality}; require a reviewed placement entry')
        elif (component and component not in regions.values() and
              not re.search(r'обл\.|область|край|республика|округ|район|р-н|мкр|микрорайон|муниципальн|городск|^м\.', component, re.I)):
            raise ValueError(f'Unknown address component {component}; require a reviewed placement entry')
    if not matches:
        raise ValueError(f'Unknown placement for {item["id"]}: add a reviewed --placements entry')
    city, region = matches[-1]
    if len({value for _, value in matches}) > 1:
        raise ValueError('Ambiguous placement regions; require a reviewed placement entry')
    return city, region, location


def reviewed_row(data, item, regions, plan, override=None):
    result = check(data, item['id'])
    if not result['structural_check_passed']:
        raise ValueError(result['errors'])
    if not data.get('parameter_audit', {}).get('rendered_complete'):
        raise ValueError('Template completeness not verified; patch collector, do not auto-approve')
    fields = {}
    for field in data['fields']:
        name, value = norm(field['name']), norm(field['value'])
        if not name or not value or name in fields:
            raise ValueError('Empty or duplicate parameter')
        fields[name] = value
    city, region, location = placement(data, item, regions, override)
    row = dict(id=data['id'], city=city, profession=plan.get('profession') or '',
        segments=plan.get('segments') or '', title=data['title'], salary_raw=data.get('salary_raw') or '',
        source_url=data['source_url'], observed_at=data['observed_at'], description=data['description'],
        location_observed=location, employer_name_observed='; '.join(dict.fromkeys(data.get('employer_raw', []))),
        date_raw=data.get('date_raw') or '', views_total=data.get('views_raw') or '', views_today=data.get('today_views_raw') or '',
        listing_rank=item['listing_rank'], listing_promoted=str(item['promoted']) if item.get('promoted') is not None else '',
        full_parameters_read='True', analysis_status='validated', export_eligibility='included',
        read_status_parameters='observed', read_status_description='observed', read_status_location='observed',
        read_limitations='rendered_DOM_only; contact_controls_not_opened; live_route_unverified',
        enrichment_status='not_run', enriched_fields='', enrichment_version='')
    # Parameter labels may be new, but cannot replace identity or provenance.
    if set(fields) & set(row):
        raise ValueError('Parameter collides with metadata')
    row.update(fields)
    review = dict(id=data['id'], structural_check=result, parameter_audit=data['parameter_audit'],
        review_method='deterministic_rendered_template_v1', semantic_review_performed=False,
        city=city, region=region, location_evidence=location, analysis_status='validated')
    return row, review, region


def finalize(args):
    out, service, diag = paths(ROOT, args.run_id)
    if (service / 'manifest.json').exists():
        raise ValueError('Finalized run exists; do not overwrite')
    started = time.perf_counter()
    queue, plan = read(out / 'queue.json'), read(service / 'plan.json')
    if any(sha(ROOT/file) != value for file,value in plan.get('automation_sha256', {}).items()):
        raise ValueError('Collector source changed after capture; use a new run')
    registry_path = ROOT / 'Data/dataset/sources.json'
    registry = read(registry_path)
    registered = [e for e in registry['exports'] if e['run_id'] == args.run_id]
    changes = read(diag / 'automation_changes.json') if (diag / 'automation_changes.json').exists() else []
    validate_automation_changes(changes, plan.get('uncommitted_automation_files', []))
    regions = {}
    for entry in registry['exports']:
        for city, region in entry['placement_regions'].items():
            if city in regions and regions[city] != region:
                raise ValueError(f'Ambiguous city {city}; require disambiguated placement mapping')
            regions[city] = region
    overrides = read(args.placements) if args.placements else {}
    rows, reviews, mapping = [], [], {}
    for item in queue:
        data = read(out / 'raw' / (item['id'] + '.json'))
        row, review, region = reviewed_row(data, item, regions, plan, overrides.get(item['id']))
        rows.append(row); reviews.append(review); mapping[row['city']] = region
    columns = list(dict.fromkeys(k for row in rows for k in row))
    exports = out / 'exports'
    exports.mkdir(exist_ok=True)  # unfinished drafts may be retried; completed runs cannot
    with (exports / 'vacancies_flat.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)
    write(exports / 'vacancies.json', rows)
    for review in reviews:
        write(out / 'reviewed' / (review['id'] + '.json'), review)
    write(exports / 'validation.json', dict(planned=len(queue), accepted=len(rows),
        unique_ids=len({r['id'] for r in rows}), errors=[], review_method='deterministic_rendered_template_v1'))
    entry = dict(run_id=args.run_id, source='avito',
        path=str((exports / 'vacancies_flat.csv').relative_to(ROOT)), sha256=sha(exports / 'vacancies_flat.csv'),
        placement_regions=mapping, enrichment_status='not_run')
    if registered and registered != [entry]:
        raise ValueError('Registered draft differs; preserve it and resolve collision')
    registry['exports'] = [e for e in registry['exports'] if e['run_id'] != args.run_id] + [entry]
    csv_bytes, manifest_bytes = build(ROOT, registry)  # validates before changing shared registry
    write(registry_path, registry)
    (ROOT / 'Data/dataset/vacancies.csv').write_bytes(csv_bytes)
    (ROOT / 'Data/dataset/manifest.json').write_bytes(manifest_bytes)
    manifest = dict(run_id=args.run_id, dataset_id=args.run_id, status='complete',
        schema=dict(columns=columns, unknown_policy='empty_not_zero'), collection=plan,
        coverage=dict(planned=len(queue), accepted=len(rows), unique_ids=[r['id'] for r in rows], errors=[],
            limitations=['Visible rendered parameters only; enrichment not run; placement on site; live route unverified']),
        timing=dict(setup_seconds=plan['setup_seconds'], capture_seconds=read(diag/'checkpoint.json').get('capture_seconds'),
            review_export_seconds=time.perf_counter()-started, publication_seconds=None),
        review_model=args.review_model, automation_changes=changes, files={})
    for path in sorted(list(out.rglob('*')) + [service / 'plan.json']):
        if path.is_file():
            manifest['files'][str(path.relative_to(ROOT))] = dict(bytes=path.stat().st_size, sha256=sha(path))
    catalog = ROOT / 'Data/README.md'
    link = f'\n- [{args.run_id}](avito_runs/{args.run_id}/exports/vacancies_flat.csv): {len(rows)} вакансий.\n'
    if link not in catalog.read_text():
        catalog.write_text(catalog.read_text() + link)
    dataset_doc = ROOT / 'Data/dataset/README.md'
    meta = json.loads(manifest_bytes)
    dataset_doc.write_text(re.sub(r'Текущий состав:.*?(?=\n)',
        f'Текущий состав: **{meta["rows"]} вакансий, {len(meta["columns"])} колонок из {len(registry["exports"])} выгрузок**.',
        dataset_doc.read_text(), count=1))
    write(service / 'manifest.json', manifest)  # completion marker is the last write
    print(json.dumps({'accepted': len(rows), 'batch_columns': len(columns),
                      'dataset_rows': meta['rows'], 'dataset_columns': len(meta['columns'])}))


def note_change(args):
    _, service, diag = paths(ROOT, args.run_id)
    if (service / 'manifest.json').exists():
        raise ValueError('Record automation fixes before finalizing')
    relative = Path(args.file)
    if relative.is_absolute() or '..' in relative.parts or not (ROOT / relative).is_file():
        raise ValueError('Change must reference a reusable file inside the repository')
    path = diag / 'automation_changes.json'
    changes = read(path) if path.exists() else []
    changes.append(dict(file=str(relative), reason=args.reason, validation=args.validation))
    write(path, changes)


def publish(args):
    out, service, diag = paths(ROOT, args.run_id)
    manifest = read(service / 'manifest.json')
    if manifest['status'] != 'complete':
        raise ValueError('Only completed exports can be published')
    def git(*argv):
        return subprocess.check_output(['git', *argv], cwd=ROOT, text=True).strip()
    if git('diff', '--cached', '--name-only'):
        raise ValueError('Stage is not empty; inspect unrelated changes first')
    git('fetch', 'origin', 'main')
    git('merge-base', '--is-ancestor', 'origin/main', 'HEAD')
    if git('branch', '--show-current') == 'main':
        raise ValueError('Publish from a codex/<run_id> branch')
    for path, expected in manifest['files'].items():
        if sha(ROOT / path) != expected['sha256']:
            raise ValueError(f'Published artifact changed: {path}')
    if any(sha(ROOT/file) != value for file,value in manifest['collection'].get('automation_sha256', {}).items()):
        raise ValueError('Collector source changed after collection; document and resolve before publication')
    # Existing fast batch validations suffice for data-only runs; changed code needs its own tests.
    subprocess.run([sys.executable, 'utils/build_dataset.py', '--check'], check=True, cwd=ROOT)
    files = [str(out.relative_to(ROOT)), str((service/'plan.json').relative_to(ROOT)),
        str((service/'manifest.json').relative_to(ROOT)), 'Data/dataset/sources.json',
        'Data/dataset/vacancies.csv', 'Data/dataset/manifest.json', 'Data/README.md', 'Data/dataset/README.md']
    files += [change['file'] for change in manifest.get('automation_changes', [])]
    git('add', '--', *files)
    body = diag / 'pr.md'
    subprocess.run([sys.executable, 'utils/dataset_pr.py', '--run-id', args.run_id,
        '--summary', args.summary, '--output', str(body)], check=True, cwd=ROOT)
    git('diff', '--cached', '--check')
    if args.prepare_only:
        print(f'Staged explicit run files; review body at {body}')
        return
    git('commit', '-m', f'data: {args.run_id} ({manifest["coverage"]["accepted"]} вакансий)')
    git('push', '-u', 'origin', 'HEAD')
    result = subprocess.check_output(['gh', 'pr', 'create', '--base', 'main', '--title', args.title,
                                      '--body-file', str(body)], cwd=ROOT, text=True)
    print(result.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['freeze', 'capture', 'finalize', 'publish', 'note-change']:
        command = commands.add_parser(name)
        command.add_argument('--run-id', required=True)
        if name == 'freeze':
            command.add_argument('--resume', action='store_true', help='Recover an interrupted freeze with identical inputs')
            command.add_argument('--agent-model', default=None, help='Actual browser agent model, not a model switch')
            command.add_argument('--url', required=True, help='Observed listing URL after filter confirmation')
            command.add_argument('--limit', type=int, required=True)
            command.add_argument('--geography', required=True)
            command.add_argument('--profession', default='')
            command.add_argument('--segments', default='')
            command.add_argument('--filter-evidence', required=True, help='What was visibly confirmed in the listing')
        elif name == 'capture':
            command.add_argument('--batch-size', type=int, default=30)
        elif name == 'finalize':
            command.add_argument('--review-model', default=None, help='Actual reviewer model, not a model switch')
            command.add_argument('--placements', type=Path, help='Reviewed ID -> city,region,evidence JSON for unknown places')
        elif name == 'publish':
            command.add_argument('--summary', required=True)
            command.add_argument('--title', required=True)
            command.add_argument('--prepare-only', action='store_true')
        else:
            for flag in ['file', 'reason', 'validation']:
                command.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    if args.command == 'capture' and args.batch_size < 1:
        parser.error('batch-size must be positive')
    try:
        globals()[args.command.replace('-', '_')](args)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()

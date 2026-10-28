"""Pagination contract with browser doubles; never accesses Avito."""
import argparse
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from Scraper.test_aside_runner import runner, item, courier_controls


class PaginationTests(unittest.TestCase):
    def simulate(self, count=5000, all_pages=False, interrupt=None, scope=None, cycle=False, blocked=False, requested=None, repeat_items=False):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            for file in runner.AUTOMATION_FILES:
                target = root/file; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((runner.ROOT/file).read_bytes())
            first = 'https://www.avito.ru/moskva/vakansii?f=observed&p=1&localPriority=0'
            calls = []; failed = False
            def browser(job, out, diag):
                nonlocal failed
                page = int(job['url'].split('p=')[1].split('&')[0]); calls.append(page)
                if page == interrupt and not failed:
                    failed = True
                    raise RuntimeError('interrupted')
                url = job['url'].replace('&localPriority=0', '')
                start = (page-1)*100+1
                # Ads repeated across pages must keep their first listing rank.
                items = [item(str(i)) for i in range(max(1, start-5), min(start+100, count+1))]
                next_url = f'https://www.avito.ru/moskva/vakansii?f=observed&p={page+1}' if start+100 <= count else None
                if cycle: next_url = first
                if repeat_items and page == 2:
                    items = [item(str(i)) for i in range(1, 101)]
                listing = dict(state='LISTING', source_url=url, heading='Вакансии',
                    selected_controls=courier_controls() if page == 1 else [],
                    items=items, pagination=dict(next_url=next_url, end_confirmed=False))
                if blocked and page == 2:
                    cp = dict(state='NEEDS_HUMAN', completed_ids=[])
                else:
                    cp = dict(state='READY', completed_ids=[])
                runner.write(diag/'listing.json', listing); runner.write(diag/'checkpoint.json', cp)
                return cp, 0
            args = argparse.Namespace(run_id='pagination', resume=False, url=first,
                limit=None if all_pages else (requested or count), all_pages=all_pages, geography='Москва',
                profession='', profession_scope=scope, segments='', filter_evidence='Applied UI selection',
                agent_model='gpt-6-luna', candidate_limit=None)
            stack.enter_context(patch.object(runner, 'ROOT', root))
            stack.enter_context(patch.object(runner, 'preflight'))
            stack.enter_context(patch.object(runner, 'browser', side_effect=browser))
            stack.enter_context(patch.object(runner, 'dirty_automation', return_value=[]))
            stack.enter_context(patch.object(runner.subprocess, 'check_output', return_value='abc'))
            stack.enter_context(redirect_stdout(io.StringIO()))
            if blocked:
                with self.assertRaisesRegex(ValueError, 'NEEDS_HUMAN'):
                    runner.freeze(args)
                self.assertEqual(calls, [1, 2])
                out, service, _ = runner.paths(root, args.run_id)
                self.assertFalse((service/'plan.json').exists())
                self.assertTrue((out/'listing_pages/0001.json').exists())
                return
            if interrupt:
                with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                    runner.freeze(args)
                args.resume = True
            runner.freeze(args)
            out, service, _ = runner.paths(root, args.run_id)
            plan = runner.read(service/'plan.json'); queue = runner.read(out/'queue.json')
            available = 100 if cycle or repeat_items else count
            self.assertEqual([q['id'] for q in queue], [str(i) for i in range(1, available+1)])
            self.assertEqual([q['listing_rank'] for q in queue], list(range(1, available+1)))
            self.assertEqual(plan['limit'], available)
            self.assertEqual(plan['requested_limit'], None if all_pages else (requested or count))
            self.assertEqual(plan['available_unique'], available)
            if requested or all_pages or cycle or repeat_items:
                self.assertTrue(plan['available_listing_exhausted'])
                self.assertEqual(plan['listing_end_reason'], 'repeated_next_link' if cycle else 'no_new_ids' if repeat_items else 'no_next_link')
            self.assertFalse((runner.paths(root, args.run_id)[2]/'failures.json').exists())
            self.assertFalse((runner.paths(root, args.run_id)[2]/'pending_listing.json').exists())
            if repeat_items:
                self.assertEqual(len(plan['listing_pages']), 2)
                self.assertTrue((out/plan['listing_pages'][-1]['file']).is_file())
            runner.validate_plan_profession(plan, out/'listing.json')
            if scope == 'top20':
                self.assertEqual(len(plan['profession'].split('; ')), 20)
            self.assertEqual(calls.count(1), 1)
            self.assertEqual(len(calls), 1 if cycle else 2 if repeat_items else (count+99)//100 + bool(interrupt))
            if len(plan['listing_pages']) > 1:
                path = out / plan['listing_pages'][1]['file']
                runner.write(path, dict(runner.read(path), heading='tampered'))
                with self.assertRaisesRegex(ValueError, 'page changed'):
                    runner.validate_plan_profession(plan, out/'listing.json')

    def test_five_thousand_without_per_page_filter_checks(self):
        self.simulate(scope='top20')

    def test_all_pages_stops_at_end(self):
        self.simulate(count=251, all_pages=True, scope='all')

    def test_resume_uses_saved_pages(self):
        self.simulate(count=301, interrupt=3)

    def test_next_cycle_and_access_block_preserve_progress(self):
        self.simulate(count=301, cycle=True)
        self.simulate(count=301, blocked=True)

    def test_requested_ten_thousand_ends_normally_on_page_thirty(self):
        self.simulate(count=2920, requested=10000, scope='all')

    def test_requested_ten_thousand_ends_normally_before_page_thirty(self):
        self.simulate(count=251, requested=10000, scope='all')

    def test_next_page_with_no_new_ids_ends_normally(self):
        self.simulate(count=301, repeat_items=True)

    def test_extractor_observes_next_and_end(self):
        script = r'''
global.location={href:'https://www.avito.ru/moskva/vakansii?f=actual',origin:'https://www.avito.ru'};
global.getComputedStyle=()=>({visibility:'visible'});
let disabled=false;
const next={innerText:'',offsetWidth:1,getClientRects:()=>[1],hasAttribute:(key)=>key==='disabled'&&disabled,
 getAttribute:(key)=>({rel:'next',href:'?f=actual&p=2','aria-label':'Следующая страница'}[key]||null)};
const a={href:'https://www.avito.ru/moskva/vakansii/test_123',pathname:'/moskva/vakansii/test_123',origin:'https://www.avito.ru',innerText:'Курьер'};
const card={innerText:'Курьер',querySelector:(s)=>s==='[data-marker="item-title"]'?a:null};
let showNext=true;
global.document={readyState:'complete',body:{innerText:'Вакансии'},querySelector:()=>null,
 querySelectorAll:(s)=>s==='[data-marker="item"]'?[card]:s==='a,[role="link"],button'&&showNext?[next]:[]};
const src=require('fs').readFileSync(process.argv[1],'utf8');
let result=eval(src);
if(result.pagination.next_url!=='https://www.avito.ru/moskva/vakansii?f=actual&p=2'||result.pagination.end_confirmed)process.exit(1);
showNext=false;result=eval(src);
if(result.pagination.next_url||result.pagination.end_confirmed)process.exit(2);
showNext=true;disabled=true;result=eval(src);
if(result.pagination.next_url||!result.pagination.end_confirmed)process.exit(3);
card.querySelector=()=>null;result=eval(src);
if(result.items.length||result.listing_item_audit.malformed!==1)process.exit(4);
'''
        subprocess.run(['node','-e',script,str(runner.ROOT/'Scraper/extract.js')],check=True)

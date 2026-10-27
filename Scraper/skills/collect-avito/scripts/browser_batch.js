// Invoked by run.py through gstack /browse's Aside REPL, never a separate browser.
const job = __JOB__;
const extractor = __EXTRACTOR__;
console.log('TL_SANDBOX=' + pwd);
await fs.mkdir('raw', {recursive: true});
let p = globalThis.tlCollectorPage || null;
const done = [...job.completed_ids];
const save = async (name, data) => {
  await fs.writeFile(name + '.tmp', JSON.stringify(data));
  await fs.rename(name + '.tmp', name);
};
const checkpoint = async (state, next, reason = null) => save('checkpoint.json',
  {state, completed_ids: done, next_id: next, stop_reason: reason});
const started = Date.now();
try {
  if (job.mode === 'freeze') {
    p = await openTab(job.url);
    const snap = await snapshot(p, {interactive: true});
    const listing = await p.evaluate(extractor);
    await save('listing.json', listing);
    if (listing.state !== 'LISTING') {
      console.log(snap.tree);
      await checkpoint(listing.state === 'NEEDS_HUMAN' ? 'NEEDS_HUMAN' : 'REVIEW_REQUIRED', null, listing.state);
    } else {
      await checkpoint('READY', null);
      console.log('TL_LISTING=' + JSON.stringify({heading: listing.heading,
        controls: listing.selected_controls, items: listing.items.length}));
    }
  } else {
    for (const item of job.queue) {
      await checkpoint('CAPTURING', item.id);
      if (!p) p = await openTab(item.url); else await p.goto(item.url);
      const snap = await snapshot(p, {interactive: true});
      const data = await p.evaluate(extractor);
      // Preserve the observation before deciding whether it can enter the dataset.
      await save('raw/' + item.id + '.json', data);
      const ready = data.state === 'DETAIL' && data.id === item.id &&
        data.parameter_audit?.rendered_complete && data.location_raw?.length &&
        data.description?.trim() && data.title?.trim();
      if (!ready) {
        await fs.writeFile('problem.snapshot.txt', snap.tree);
        console.log(snap.tree);
        await checkpoint(data.state === 'NEEDS_HUMAN' ? 'NEEDS_HUMAN' : 'REVIEW_REQUIRED',
          item.id, data.reason || 'identity_or_template_review');
        break;
      }
      done.push(item.id);
      await checkpoint('CAPTURED', null);
      console.log('TL_CARD=' + JSON.stringify({id: item.id, rank: item.listing_rank,
        fields: data.fields.length, description_characters: data.description.length}));
    }
  }
} catch (error) {
  await checkpoint('BROWSER_ERROR', job.queue?.find(i => !done.includes(i.id))?.id || null, String(error));
  console.log('TL_ERROR=' + String(error));
} finally {
  await save('timing.json', {capture_seconds: (Date.now() - started) / 1000});
  const cp = JSON.parse(await fs.readFile('checkpoint.json', 'utf8'));
  if (p && (job.close_tab || cp.state === 'NEEDS_HUMAN' || cp.state === 'REVIEW_REQUIRED' || cp.state === 'BROWSER_ERROR')) {
    await closeTab(p); p = null;
  }
  globalThis.tlCollectorPage = p;
  console.log('TL_FINISHED');
}

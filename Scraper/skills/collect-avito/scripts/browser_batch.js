// Invoked by run.py through gstack /browse's Aside REPL, never a separate browser.
const job = __JOB__;
const extractor = __EXTRACTOR__;
console.log('TL_SANDBOX=' + pwd);
await fs.mkdir('raw', {recursive: true});
let p = globalThis.tlCollectorPage || null;
let external = Boolean(job.tab_id);
let targetId = job.tab_id || null;
const safeUrl = value => { try { const u = new URL(value); return u.origin + u.pathname; } catch (_) { return null; } };
const saveTab = async () => save('browser-tab.json', {target_id: targetId, ownership: external ? 'external' : 'collector', url: p ? safeUrl(p.url()) : safeUrl(job.url || job.queue?.[0]?.url)});
const done = [...job.completed_ids];
const save = async (name, data) => {
  await fs.writeFile(name + '.tmp', JSON.stringify(data));
  await fs.rename(name + '.tmp', name);
};
const checkpoint = async (state, next, reason = null) => save('checkpoint.json',
  {state, completed_ids: done, next_id: next, stop_reason: reason});
const started = Date.now();
try {
  if (!p && targetId) p = await attachBrowserTab(targetId);
  if (job.mode === 'freeze') {
    if (!p) {
      const before = new Set((await listBrowserTabs()).map(t => t.targetId));
      p = await openTab(job.url);
      const added = (await listBrowserTabs()).filter(t => !before.has(t.targetId) && t.url === p.url());
      if (added.length === 1) targetId = added[0].targetId;
    } else if (job.allow_navigation && p.url() !== job.url) await p.goto(job.url);
    await saveTab();
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
      if (!p) {
        const before = new Set((await listBrowserTabs()).map(t => t.targetId));
        p = await openTab(item.url);
        const added = (await listBrowserTabs()).filter(t => !before.has(t.targetId) && t.url === p.url());
        if (added.length === 1) targetId = added[0].targetId;
      } else await p.goto(item.url);
      await saveTab();
      const snap = await snapshot(p, {interactive: true});
      const data = await p.evaluate(extractor);
      // Preserve the observation before deciding whether it can enter the dataset.
      await save('raw/' + item.id + '.json', data);
      const validFields = Array.isArray(data.fields) && data.fields.length > 0 &&
        data.fields.every(f => f && typeof f.name === 'string' && f.name.trim() &&
          typeof f.value === 'string' && f.value.trim());
      const validDescription = data.description == null || typeof data.description === 'string';
      const ready = data.state === 'DETAIL' && data.id === item.id &&
        data.parameter_audit?.rendered_complete && typeof data.title === 'string' &&
        data.title.trim() && validFields && validDescription;
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
        fields: data.fields.length, description_characters: (data.description || '').length}));
    }
  }
} catch (error) {
  await checkpoint('BROWSER_ERROR', job.queue?.find(i => !done.includes(i.id))?.id || null, String(error));
  console.log('TL_ERROR=' + String(error));
} finally {
  await saveTab();
  await save('timing.json', {capture_seconds: (Date.now() - started) / 1000});
  const cp = JSON.parse(await fs.readFile('checkpoint.json', 'utf8'));
  if (p && !external && job.close_tab && (cp.state === 'READY' || cp.state === 'CAPTURED')) {
    await closeTab(p); p = null;
  }
  globalThis.tlCollectorPage = p;
  globalThis.tlCollectorExternal = external;
  console.log('TL_FINISHED');
}

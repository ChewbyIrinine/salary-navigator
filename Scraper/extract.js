(() => {
  const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  const text = e => e?.innerText?.trim() || null;
  const get = marker => text(document.querySelector(`[data-marker="${marker}"]`));
  const redact = value => value?.replace(/(?:\+?7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}/g, '[phone removed]').replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[email removed]') || null;
  const headings = [...document.querySelectorAll('h1,h2,[role="dialog"]')].filter(visible).map(text).filter(Boolean);
  const body = document.body?.innerText || '';
  const cards = [...document.querySelectorAll('[data-marker="item"]')];
  const title = get('item-view/title-info');
  const blockingText = /Доступ ограничен|проблема с IP|подтвердите.{0,30}(человек|робот)|проверка безопасности/i;
  const blocked = body.includes('Доступ ограничен: проблема с IP') || headings.some(s => blockingText.test(s)) ||
    ((!cards.length && !title) && blockingText.test(body.slice(0, 5000))) ||
    [...document.querySelectorAll('[role="dialog"],iframe[src*="captcha"]')].some(e => visible(e) && (e.tagName === 'IFRAME' || /капч|captcha|Доступ ограничен/i.test(e.innerText)));
  const base = {source_url: location.href, observed_at: new Date().toISOString(),
    channel: 'gstack DOM', schema_version: 4,
    state: blocked ? 'NEEDS_HUMAN' : title ? 'DETAIL' : cards.length ? 'LISTING' : 'UNKNOWN_PAGE'};
  // Do not extract even underlying DOM when a blocking overlay is present.
  if (blocked) return {...base, reason: 'access_restriction_or_captcha', items: []};
  if (base.state === 'UNKNOWN_PAGE') {
    const emptyHeading = headings.find(s => /^(?:По вашему запросу (?:ничего не найдено|нет объявлений)|Ничего не найдено)[.!]?$/i.test(s));
    return {...base, state: emptyHeading ? 'EMPTY_RESULT' : 'UNKNOWN_PAGE',
      empty_evidence: emptyHeading || null, heading: text(document.querySelector('h1')), items: []};
  }
  const cleanURL = a => a ? new URL(a.pathname, a.origin).href : null;
  const fields = blocks => blocks.flatMap(block => block.split('\n').filter(s => s.includes(':')).map(s => {
    const i = s.search(/:\s/); return i < 0 ? null : {name: s.slice(0, i).trim(), value: s.slice(i + 1).trim(), values: s.slice(i + 1).trim().split(/,\s+/), values_parse: 'comma_space_tokens_from_raw_no_inferred_values', evidence_raw: s};
  })).filter(Boolean);
  if (base.state === 'DETAIL') {
    const blocks = [...document.querySelectorAll('[data-marker="item-view/item-params"]')].map(text).filter(Boolean);
    const descriptionRaw = get('item-view/item-description');
    // Candidate public blocks are explicitly marked as unverified until live-template QA.
    const publicBlock = pattern => [...document.querySelectorAll('[data-marker]')]
      .filter(e => visible(e) && pattern.test(e.getAttribute('data-marker')))
      .map(e => ({marker: e.getAttribute('data-marker'), text: text(e)})).filter(x => x.text);
    const employers = publicBlock(/^(?:seller-info\/name|seller-link\/link)$/);
    let locations = publicBlock(/^(?:item-view\/item-address|item-view\/address|item-view\/location)$/);
    // Current job templates have no address marker. Read only the visible block
    // immediately after the location heading, never the description, map or URL.
    if (!locations.length) {
      const locationHeading = [...document.querySelectorAll('h2,h3')]
        .find(e => visible(e) && /^Расположение$/.test(text(e)));
      const block = locationHeading?.nextElementSibling;
      if (block && !block.matches('h1,h2,h3') &&
          !block.querySelector('h1,h2,h3,[data-marker="item-view/item-description"],[data-marker="item-view/item-params"]') &&
          !block.matches('[data-marker="item-view/item-description"],[data-marker="item-view/item-params"]')) {
        const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
        const parts = [];
        while (walker.nextNode()) {
          const node = walker.currentNode, parent = node.parentElement;
          if (parent && visible(parent) && !parent.closest(
            'button,[role="button"],[data-marker^="item-map"],ymaps,script,style'
          ) && node.textContent.trim()) parts.push(node.textContent.trim());
        }
        if (parts.length) locations = [{marker: null, method: 'visible_location_heading',
          heading: 'Расположение', text: parts.join('\n')}];
      }
    }
    const sections = [...document.querySelectorAll('[data-marker="item-view/item-params"],[data-marker="item-view/item-description"]')]
      .filter(visible).map(e => ({marker: e.getAttribute('data-marker'), text: text(e)}));
    return {...base, id: location.pathname.match(/_(\d+)$/)?.[1] || get('item-view/item-id')?.match(/\d+/)?.[0],
      title, salary_raw: get('item-view/item-price-container') || get('item-view/item-price'),
      params_blocks: blocks, fields: fields(blocks), fields_parse_version: 3, description: descriptionRaw, description_redacted: redact(descriptionRaw),
      employer_raw: employers.map(x => x.text), employer_evidence: employers,
      location_raw: locations.map(x => x.text), location_evidence: locations,
      source_sections: sections,
      read_status: {parameters: blocks.length ? 'observed' : 'unread',
        description: descriptionRaw ? 'observed' : 'unread',
        employer: employers.length ? 'observed' : 'unread', location: locations.length ? 'observed' : 'unread'},
      full_parameters_read: false,
      read_limitations: ['rendered_DOM_only', 'expanded_sections_and_template_completeness_not_live_verified',
        'employer_template_completeness_not_live_verified',
        ...(locations.length ? ['placement_city_and_region_require_review'] : ['location_not_observed'])],
      raw_policy: 'Public description preserved; derived redacted copy separate; contact controls not opened',
      date_raw: get('item-view/item-date'), views_raw: get('item-view/total-views'), today_views_raw: get('item-view/today-views')};
  }
  // Do not infer opaque URL filter encodings. Preserve URL plus visible UI state.
  const controls = [...document.querySelectorAll('input[type="checkbox"]:checked,input[type="radio"]:checked,[role="checkbox"][aria-checked="true"],[role="radio"][aria-checked="true"],[role="switch"][aria-checked="true"]')].map(e => ({
    label: e.getAttribute('aria-label') || text(e.closest('label')) || text(e.parentElement),
    marker: e.getAttribute('data-marker'), value: e.value || null
  }));
  const items = cards.map(r => {
    const read = m => text(r.querySelector(`[data-marker="${m}"]`));
    const a = r.querySelector('[data-marker="item-title"]');
    return {id: a?.pathname.match(/_(\d+)$/)?.[1] || null, url: cleanURL(a), title: read('item-title'),
      salary_raw: read('item-price'), params_raw: read('item-specific-params'), location_raw: read('item-location'),
      promoted: /Продвинуто/.test(r.innerText)};
  }).filter(x => x.id && x.url && x.title);
  const next = [...document.querySelectorAll('a,[role="link"],button')].filter(visible).find(e =>
    e.getAttribute('rel') === 'next' || e.getAttribute('aria-label') === 'Следующая страница' ||
    text(e) === 'Следующая страница');
  const disabled = next && (next.hasAttribute('disabled') || next.getAttribute('aria-disabled') === 'true');
  const nextHref = next?.getAttribute('href');
  const nextURL = nextHref ? new URL(nextHref, location.href).href : null;
  const pagination = {next_url: !disabled ? nextURL : null, end_confirmed: !!disabled,
    evidence: next ? {text: text(next), label: next.getAttribute('aria-label'), href: nextHref, disabled: !!disabled} : null,
    limitation: 'No next control is not proof of end; infinite loading not verified'};
  return {...base, heading: text(document.querySelector('h1')), selected_controls: controls, items, pagination};
})()

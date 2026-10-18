(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => (n == null ? null : String(+Number(n).toFixed(2)));

  const results = $('#results');
  const status = $('#status');
  let activeTab = 'vehicle';
  let reqId = 0;

  // ---- tabs
  const tabs = $$('.tabs [role="tab"]');
  function selectTab(tab, focus) {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute('aria-selected', on);
      t.tabIndex = on ? 0 : -1;
      $('#' + t.getAttribute('aria-controls')).hidden = !on;
    });
    activeTab = tab.id.replace('tab-', '');
    results.innerHTML = '';
    status.textContent = '';
    if (focus) tab.focus();
    const input = $('#panel-' + activeTab + ' input');
    if (input && !focus) input.focus();
    if (activeTab !== 'specs') runTextSearch(activeTab);
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => selectTab(t));
    t.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); selectTab(tabs[(i + d + tabs.length) % tabs.length], true); }
    });
  });

  // ---- rendering
  const placeholder = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="6" y="5" width="12" height="15" rx="2"/><path d="M8 3h8M9 9v7M12 9v7M15 9v7"/></svg>';

  function card(f) {
    const dims = [['Height', f.height_mm], ['Diameter', f.diameter_mm], ['Opening', f.mouth_mm]];
    const hasDims = dims.some((d) => d[1] != null);
    const facts = [];
    if (f.thread) facts.push(['Thread', f.thread]);
    if (f.flange_od_mm != null) facts.push(['Flange outer diameter', num(f.flange_od_mm) + ' mm']);
    if (f.flange_holes != null) facts.push(['Flange holes', f.flange_holes]);
    if (f.equivalents.length) facts.push(['Equivalents', f.equivalents.map(esc).join(', ')]);
    if (f.notes) facts.push(['Notes', f.notes]);
    const machines = f.machines.length
      ? `<details><summary>Fits ${f.machines.length} ${f.machines.length === 1 ? 'machine' : 'machines'}</summary><ul>${f.machines.map((m) => `<li>${esc(m)}</li>`).join('')}</ul></details>`
      : '';
    const pic = f.image_url
      ? `<button type="button" data-zoom="${esc(f.image_url)}" data-alt="${esc(f.part_number)}" aria-label="Enlarge image of ${esc(f.part_number)}"><img loading="lazy" src="${esc(f.image_url)}" alt="${esc(f.part_number)}"></button>`
      : placeholder;
    return `<article class="card">
      <div class="pic">${pic}</div>
      <div class="body">
        <div class="head"><span class="pn">${esc(f.part_number)}</span>${f.brand ? `<span class="brand-tag">${esc(f.brand)}</span>` : ''}</div>
        <span class="chip">${esc(f.type_label)}</span>
        ${f.match_note ? `<p class="match">${esc(f.match_note)}</p>` : ''}
        ${hasDims ? `<dl class="dims">${dims.map(([l, v]) => `<div><dt>${l}</dt><dd>${v == null ? '–' : `${num(v)} <small>mm</small>`}</dd></div>`).join('')}</dl>` : ''}
        <dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${k === 'Equivalents' ? v : esc(v)}</dd>`).join('')}</dl>
        ${machines}
      </div>
    </article>`;
  }

  function show(list, emptyMsg) {
    if (!list.length) {
      results.innerHTML = `<div class="empty">${emptyMsg}</div>`;
      status.textContent = '';
      return;
    }
    results.innerHTML = list.map(card).join('');
    status.textContent = `${list.length} ${list.length === 1 ? 'filter' : 'filters'} found`;
  }

  async function fetchJson(url) {
    const id = ++reqId;
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error();
      const data = await r.json();
      return id === reqId ? data : null; // ignore out-of-date responses
    } catch {
      if (id === reqId) {
        results.innerHTML = '<div class="empty"><p>Search is not available right now.</p><p>Check your connection and try again.</p></div>';
        status.textContent = '';
      }
      return null;
    }
  }

  // ---- vehicle and part number searches (as you type)
  const inputs = { vehicle: $('#q-vehicle'), part: $('#q-part') };
  const emptyText = {
    vehicle: '<p>No filters found for that machine.</p><p>Try just the make or the model number.</p>',
    part: '<p>No filters found for that part number.</p><p>Try fewer characters.</p>',
  };
  async function runTextSearch(kind) {
    const q = inputs[kind].value.trim();
    if (!q) { results.innerHTML = ''; status.textContent = ''; return; }
    const data = await fetchJson(`/api/search/${kind}?q=${encodeURIComponent(q)}`);
    if (data) show(data.results, emptyText[kind]);
  }
  Object.entries(inputs).forEach(([kind, el]) => {
    let timer;
    el.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => runTextSearch(kind), 250); });
  });

  // ---- specification search
  const form = $('#spec-form');
  async function runSpecSearch() {
    const p = new URLSearchParams();
    new FormData(form).forEach((v, k) => { if (String(v).trim()) p.set(k, v); });
    p.delete('tol'); // re-added below, only when a size is entered
    const sizes = ['height', 'diameter', 'mouth', 'flange_od'].some((k) => p.has(k));
    if (sizes) p.set('tol', $('#s-tol').value);
    if (!['type', 'thread', 'height', 'diameter', 'mouth', 'flange_od', 'flange_holes'].some((k) => p.has(k))) {
      results.innerHTML = '';
      status.textContent = 'Choose at least one specification.';
      return;
    }
    const data = await fetchJson('/api/search/specs?' + p);
    if (data) show(data.results, '<p>No filters match these specifications.</p><p>Try a larger allowed difference, or remove one field.</p>');
  }
  form.addEventListener('submit', (e) => { e.preventDefault(); runSpecSearch(); });
  form.addEventListener('reset', () => { setTimeout(() => { toggleSeparator(); results.innerHTML = ''; status.textContent = ''; }); });
  function toggleSeparator() {
    const on = $('#s-type').value === 'separator';
    $$('.sep-only', form).forEach((el) => { el.hidden = !on; if (!on) $('input', el).value = ''; });
  }
  $('#s-type').addEventListener('change', toggleSeparator);

  // ---- image zoom
  const box = $('#lightbox');
  results.addEventListener('click', (e) => {
    const b = e.target.closest('[data-zoom]');
    if (!b) return;
    $('#lightbox-img').src = b.dataset.zoom;
    $('#lightbox-img').alt = b.dataset.alt;
    box.showModal();
  });
  box.addEventListener('click', (e) => { if (e.target === box) box.close(); });

  // ---- load dropdown options
  fetch('/api/meta').then((r) => r.json()).then((m) => {
    $('#s-type').insertAdjacentHTML('beforeend', m.types.map((t) => `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join(''));
    $('#s-thread').insertAdjacentHTML('beforeend', m.threads.map((t) => `<option>${esc(t)}</option>`).join(''));
  });

  inputs.vehicle.focus();
})();

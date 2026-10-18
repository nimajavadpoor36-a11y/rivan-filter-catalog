(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => (n == null ? '–' : String(+Number(n).toFixed(2)));

  let csrf = '';
  let types = [];
  let commonThreads = [];
  let editingId = null;
  let currentImage = null;

  const loginView = $('#login-view');
  const appView = $('#app-view');

  async function api(url, opts = {}) {
    opts.headers = { ...(opts.headers || {}), 'X-CSRF-Token': csrf };
    const r = await fetch(url, opts);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) { showLogin(); throw new Error(data.error || 'Please sign in.'); }
    if (!r.ok) throw new Error(data.error || 'Something went wrong.');
    return data;
  }

  // ---- sign in / out
  function showLogin() {
    appView.hidden = true;
    $('#logout').hidden = true;
    loginView.hidden = false;
    $('#password').focus();
  }
  async function showApp(s) {
    csrf = s.csrf;
    commonThreads = s.threads;
    loginView.hidden = true;
    appView.hidden = false;
    $('#logout').hidden = false;
    $('#set-name').value = s.site_name;
    if (!types.length) {
      types = (await (await fetch('/api/meta')).json()).types;
      $('#f-type').insertAdjacentHTML('beforeend', types.map((t) => `<option value="${t.key}">${esc(t.label)}</option>`).join(''));
    }
    loadList();
  }
  $('#login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('#login-error').textContent = '';
    const r = await fetch('/api/admin/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: $('#password').value }),
    });
    const data = await r.json();
    if (!r.ok) { $('#login-error').textContent = data.error; return; }
    $('#password').value = '';
    showApp(await (await fetch('/api/admin/session')).json());
  });
  $('#logout').addEventListener('click', async () => {
    await api('/api/admin/logout', { method: 'POST' }).catch(() => {});
    showLogin();
  });

  // ---- tabs
  const tabs = $$('.tabs [role="tab"]');
  tabs.forEach((t, i) => {
    const select = (tab, focus) => {
      tabs.forEach((x) => {
        const on = x === tab;
        x.setAttribute('aria-selected', on);
        x.tabIndex = on ? 0 : -1;
        $('#' + x.getAttribute('aria-controls')).hidden = !on;
      });
      if (focus) tab.focus();
    };
    t.addEventListener('click', () => select(t));
    t.addEventListener('keydown', (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) { e.preventDefault(); select(tabs[(i + d + tabs.length) % tabs.length], true); }
    });
  });

  // ---- list
  const rows = $('#admin-rows');
  const status = $('#admin-status');
  let list = [];
  async function loadList() {
    const q = $('#admin-q').value.trim();
    const data = await api('/api/admin/filters?q=' + encodeURIComponent(q));
    list = data.filters;
    status.textContent = list.length
      ? `${data.total} ${data.total === 1 ? 'filter' : 'filters'}${data.total > list.length ? `, showing the latest ${list.length}` : ''}`
      : (q ? 'No filters match your search.' : 'No filters yet. Add the first one.');
    rows.innerHTML = list.map((f) => `<tr>
      <td>${f.image_url ? `<img src="${esc(f.image_url)}" alt="" loading="lazy">` : ''}</td>
      <td><strong>${esc(f.part_number)}</strong></td>
      <td>${esc(f.type_label)}</td>
      <td>${esc(f.brand)}</td>
      <td class="num">${num(f.height_mm)} × ${num(f.diameter_mm)} × ${num(f.mouth_mm)}</td>
      <td>${esc(f.thread)}</td>
      <td><div class="row-actions">
        <button class="btn small" data-edit="${f.id}">Edit</button>
        <button class="btn small danger" data-del="${f.id}">Delete</button>
      </div></td></tr>`).join('');
  }
  let t;
  $('#admin-q').addEventListener('input', () => { clearTimeout(t); t = setTimeout(loadList, 250); });
  rows.addEventListener('click', async (e) => {
    const ed = e.target.closest('[data-edit]');
    const del = e.target.closest('[data-del]');
    if (ed) openEditor(list.find((f) => f.id === +ed.dataset.edit));
    if (del) {
      const f = list.find((x) => x.id === +del.dataset.del);
      if (!confirm(`Delete ${f.part_number}${f.brand ? ' (' + f.brand + ')' : ''}? This cannot be undone.`)) return;
      try { await api('/api/admin/filters/' + f.id, { method: 'DELETE' }); loadList(); }
      catch (err) { alert(err.message); }
    }
  });

  // ---- editor dialog
  const dlg = $('#filter-dialog');
  const form = $('#filter-form');
  const threadSel = $('#f-thread');
  const threadOther = $('#f-thread-other');

  function fillThreads(selected) {
    const all = [...new Set([...commonThreads, ...(selected ? [selected] : [])])];
    threadSel.innerHTML = '<option value="">No thread</option>' +
      all.map((x) => `<option>${esc(x)}</option>`).join('') + '<option value="__other">Other…</option>';
    threadSel.value = selected || '';
    threadOther.hidden = true;
    threadOther.value = '';
  }
  threadSel.addEventListener('change', () => {
    threadOther.hidden = threadSel.value !== '__other';
    if (!threadOther.hidden) threadOther.focus();
  });
  function toggleSeparator() {
    const on = $('#f-type').value === 'separator';
    $$('.sep-only', form).forEach((el) => { el.hidden = !on; });
  }
  $('#f-type').addEventListener('change', toggleSeparator);

  function setPreview(src) {
    const img = $('#f-preview');
    img.hidden = !src;
    if (src) img.src = src;
  }
  $('#f-image').addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) { setPreview(URL.createObjectURL(file)); $('#f-remove-image').checked = false; }
    else setPreview(currentImage);
  });

  function openEditor(f) {
    editingId = f ? f.id : null;
    currentImage = f ? f.image_url : null;
    form.reset();
    $('#dialog-title').textContent = f ? 'Edit filter' : 'Add filter';
    $('#form-error').textContent = '';
    fillThreads(f ? f.thread : '');
    if (f) {
      $('#f-type').value = f.type;
      $('#f-part').value = f.part_number;
      $('#f-brand').value = f.brand;
      $('#f-height').value = f.height_mm ?? '';
      $('#f-diameter').value = f.diameter_mm ?? '';
      $('#f-mouth').value = f.mouth_mm ?? '';
      $('#f-flange-od').value = f.flange_od_mm ?? '';
      $('#f-flange-holes').value = f.flange_holes ?? '';
      $('#f-equiv').value = f.equivalents.join('\n');
      $('#f-machines').value = f.machines.join('\n');
      $('#f-notes').value = f.notes;
    }
    setPreview(currentImage);
    $('#remove-row').hidden = !currentImage;
    toggleSeparator();
    dlg.showModal();
    $('#f-type').focus();
  }
  $('#add-filter').addEventListener('click', () => openEditor(null));
  $('#cancel-dialog').addEventListener('click', () => dlg.close());

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData();
    $$('input[name], select[name], textarea[name]', form).forEach((el) => {
      if (el.type === 'file' || el.name.startsWith('thread_')) return;
      fd.set(el.name, el.value);
    });
    fd.set('thread', threadSel.value === '__other' ? threadOther.value.trim() : threadSel.value);
    const file = $('#f-image').files[0];
    if (file) fd.set('image', file);
    if ($('#f-remove-image').checked) fd.set('remove_image', '1');
    const btn = $('button[type="submit"]', form);
    btn.disabled = true;
    try {
      await api(editingId ? '/api/admin/filters/' + editingId : '/api/admin/filters',
        { method: editingId ? 'PUT' : 'POST', body: fd });
      dlg.close();
      loadList();
    } catch (err) {
      $('#form-error').textContent = err.message;
    } finally {
      btn.disabled = false;
    }
  });

  // ---- settings
  $('#settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.set('site_name', $('#set-name').value);
    const file = $('#set-logo').files[0];
    if (file) fd.set('logo', file);
    const msg = $('#settings-msg');
    try {
      const d = await api('/api/admin/settings', { method: 'POST', body: fd });
      msg.style.color = 'var(--petrol)';
      msg.textContent = 'Settings saved.';
      $('#hdr-name').textContent = d.site_name;
      $('#hdr-logo').src = '/logo?v=' + Date.now();
      $('#set-logo').value = '';
    } catch (err) { msg.style.color = ''; msg.textContent = err.message; }
  });
  $('#password-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const msg = $('#password-msg');
    try {
      await api('/api/admin/password', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current: $('#pw-current').value, new: $('#pw-new').value }),
      });
      msg.style.color = 'var(--petrol)';
      msg.textContent = 'Password changed.';
      e.target.reset();
    } catch (err) { msg.style.color = ''; msg.textContent = err.message; }
  });

  // ---- start
  fetch('/api/admin/session').then((r) => r.json()).then((s) => (s.admin ? showApp(s) : showLogin()));
})();

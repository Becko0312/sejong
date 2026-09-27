'use strict';
// Book2Course homepage: auth + role-based views (admin builds courses, clients study them).
const $ = (s) => document.querySelector(s);
const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let me = null, authMode = 'login', pollTimer = null, chosenFile = null;
const enrichEst = {};
const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

async function api(url, options = {}) {
  const res = await fetch(url, options);
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'Request failed. Please try again.');
  return data;
}

async function boot() {
  me = await fetch('/api/me').then((r) => r.json()).catch(() => ({ authenticated: false }));
  render();
}

function show(view) {
  for (const v of document.querySelectorAll('.view')) v.hidden = v.id !== view;
}

function render() {
  clearTimeout(pollTimer);
  const box = $('#userbox');
  if (me && me.authenticated) {
    box.innerHTML = `<span class="role">${escape(me.role)}</span><span>${escape(me.username)}</span><button class="ghost" id="logout">Log out</button>`;
    $('#logout').addEventListener('click', logout);
    if (me.role === 'admin') { show('admin-view'); fillLanguages(); loadAdmin(); } else { show('client-view'); loadCatalog(); }
  } else {
    box.innerHTML = '';
    show('auth-view');
  }
}

/* ---------- Auth ---------- */
function setAuthMode(mode) {
  authMode = mode;
  $('#tab-login').classList.toggle('active', mode === 'login');
  $('#tab-register').classList.toggle('active', mode === 'register');
  $('#auth-submit').textContent = mode === 'login' ? 'Log in' : 'Create account';
  $('#password').autocomplete = mode === 'login' ? 'current-password' : 'new-password';
  $('#auth-hint').style.display = mode === 'login' ? 'block' : 'none';
  $('#auth-msg').textContent = '';
}
$('#tab-login').addEventListener('click', () => setAuthMode('login'));
$('#tab-register').addEventListener('click', () => setAuthMode('register'));
$('#switch-register').addEventListener('click', (e) => { e.preventDefault(); setAuthMode('register'); });

$('#auth-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = $('#username').value.trim(), password = $('#password').value;
  $('#auth-submit').disabled = true; $('#auth-msg').className = 'msg'; $('#auth-msg').textContent = 'Please wait…';
  try {
    await api(`/api/${authMode === 'login' ? 'login' : 'register'}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }),
    });
    $('#password').value = '';
    await boot();
  } catch (err) { $('#auth-msg').className = 'msg err'; $('#auth-msg').textContent = err.message; }
  finally { $('#auth-submit').disabled = false; }
});

// Course languages come from the server's supported list (see app/languages.py).
function fillLanguages() {
  for (const sel of [$('#c-source'), $('#c-target')]) {
    if (sel.options.length) continue;
    for (const name of me.languages || []) { const d = name === sel.dataset.default; sel.add(new Option(name, name, d, d)); }
  }
}

async function logout() { try { await api('/api/logout', { method: 'POST' }); } catch {} me = null; await boot(); }

/* ---------- Client catalog ---------- */
async function loadCatalog() {
  const grid = $('#catalog');
  try {
    const courses = await api('/api/catalog');
    grid.innerHTML = courses.length ? courses.map((c) => {
      const langs = [c.source_lang, c.target_lang].filter(Boolean).join(' → ');
      return `<a class="course-card" href="/course/${encodeURIComponent(c.id)}">${langs ? `<span class="langs">${escape(langs)}</span>` : ''}<h3>${escape(c.title)}</h3>${c.description ? `<p>${escape(c.description)}</p>` : ''}<span class="go">Study with AI tutor →</span></a>`;
    }).join('') : '<div class="empty">No courses are available yet. Please check back soon.</div>';
  } catch (err) { grid.innerHTML = `<div class="empty">${escape(err.message)}</div>`; }
}
$('#catalog-refresh').addEventListener('click', loadCatalog);

/* ---------- Admin ---------- */
const drop = $('#drop');
$('#c-file').addEventListener('change', (e) => { chosenFile = e.target.files[0]; $('#c-filename').textContent = chosenFile ? `${chosenFile.name} · ${(chosenFile.size / 1048576).toFixed(1)} MB` : 'Choose a PDF textbook'; });
for (const ev of ['dragenter', 'dragover']) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); $('#c-file').files = e.dataTransfer.files; chosenFile = e.dataTransfer.files[0]; if (chosenFile) $('#c-filename').textContent = `${chosenFile.name} · ${(chosenFile.size / 1048576).toFixed(1)} MB`; });

$('#add-form').addEventListener('submit', (e) => {
  e.preventDefault();
  if (!chosenFile || !me) return;
  const params = new URLSearchParams({
    name: chosenFile.name, ocr: $('#c-ocr').checked,
    title: $('#c-title').value.trim(), source_lang: $('#c-source').value,
    target_lang: $('#c-target').value, description: $('#c-desc').value.trim(),
  });
  const req = new XMLHttpRequest();
  req.open('POST', `/api/jobs?${params}`);
  req.setRequestHeader('Content-Type', 'application/pdf');
  req.setRequestHeader('X-CSRF-Token', me.csrf);
  $('#add-submit').disabled = true;
  req.upload.onprogress = (ev) => { $('#add-msg').className = 'msg'; $('#add-msg').textContent = ev.lengthComputable ? `Uploading ${Math.round(ev.loaded / ev.total * 100)}%…` : 'Uploading…'; };
  req.onload = () => {
    $('#add-submit').disabled = false;
    if (req.status === 202) {
      $('#add-msg').className = 'msg ok'; $('#add-msg').textContent = 'Uploaded. Converting in the background…';
      $('#add-form').reset(); chosenFile = null; $('#c-filename').textContent = 'Choose a PDF textbook';
      loadAdmin();
    } else {
      let d = {}; try { d = JSON.parse(req.responseText); } catch {}
      $('#add-msg').className = 'msg err'; $('#add-msg').textContent = d.detail || 'Upload failed.';
    }
  };
  req.onerror = () => { $('#add-submit').disabled = false; $('#add-msg').className = 'msg err'; $('#add-msg').textContent = 'Upload interrupted. Please try again.'; };
  req.send(chosenFile);
});

async function loadAdmin() {
  const list = $('#admin-list');
  try {
    const courses = await api('/api/admin/courses');
    clearTimeout(pollTimer);
    if (courses.some((c) => ['uploading', 'queued', 'processing'].includes(c.status)
        || (c.enrich && c.enrich.status && c.enrich.status.state === 'building')
        || (c.summary && c.summary.status && c.summary.status.state === 'building'))) pollTimer = setTimeout(loadAdmin, 5000);
    $('#course-count').textContent = courses.length;
    list.innerHTML = courses.length ? courses.map(adminRow).join('') : '<div class="empty">No courses yet. Add one on the left.</div>';
  } catch (err) { list.innerHTML = `<div class="empty">${escape(err.message)}</div>`; }
}

function adminRow(c) {
  const langs = [c.source_lang, c.target_lang].filter(Boolean).join(' → ');
  const done = c.status === 'completed';
  if (c.enrich) enrichEst[c.id] = c.enrich;
  const actions = [];
  if (done) actions.push(`<a class="open" href="/course/${encodeURIComponent(c.id)}">Open ↗</a>`);
  if (done) actions.push(`<button class="ghost" data-toggle="${c.id}" data-available="${c.available ? 1 : 0}">${c.available ? 'Hide from students' : 'Publish to students'}</button>`);
  if (c.status === 'failed' && (c.attempts || 0) < 3) actions.push(`<button class="ghost" data-retry="${c.id}">Retry</button>`);
  actions.push(`<button class="danger" data-delete="${c.id}">Delete</button>`);
  const meta = done ? `${langs ? escape(langs) + ' · ' : ''}${c.available ? 'Visible to students' : 'Hidden'}` : `${langs ? escape(langs) + ' · ' : ''}${c.done || 0}/${c.total || '…'} pages`;
  let ix = '';
  if (done && c.enrich) {
    const e = c.enrich;
    if (!e.enabled) {
      ix = '<p class="meta">✨ Interactive pages: no AI key configured on this server.</p>';
    } else {
      const st = e.status || {};
      const prog = st.state === 'building' ? ` · building ${st.done || 0}/${st.total || e.pages}…`
        : (st.state && st.state !== 'idle' && st.state !== 'done' ? ` · ${st.state}${st.error ? `: ${st.error}` : ''}` : '');
      ix = `<p class="meta">✨ Interactive: ${e.built}/${e.pages} pages built · estimate ≈${fmtK(e.input_tokens)} in + ${fmtK(e.output_tokens)} out tokens on ${escape(e.model)} (≈ $${e.cost_usd.toFixed(2)})${prog}</p>`
        + `<div class="row-actions"><button class="ghost" data-enrich="${c.id}">${e.built ? 'Rebuild' : 'Build'} interactive pages</button></div>`;
    }
  }
  if (done && c.summary && c.summary.enabled) {
    const sm = c.summary, st = sm.status || {};
    const prog = st.state === 'building' ? ` · building ${st.done || 0}/${st.total || sm.planned}…`
      : (st.state && st.state !== 'idle' && st.state !== 'done' ? ` · ${st.state}${st.error ? `: ${st.error}` : ''}` : '');
    ix += `<p class="meta">📝 Summary: ${sm.planned ? `${sm.built}/${sm.planned} lessons built` : 'lesson pages not set yet'}${escape(prog)}</p>`
      + `<div class="row-actions"><button class="ghost" data-plan="${c.id}" data-title="${escape(c.title)}">Build summary pages</button></div>`;
  }
  return `<div class="course-row"><div class="top"><h3>${escape(c.title)}</h3><span class="status ${escape(c.status)}">${escape(c.status)}</span></div>`
    + `<p class="meta">${meta}${c.error ? ` · <span style="color:#a5462f">${escape(c.error)}</span>` : ''}</p>`
    + ix
    + (['uploading', 'queued', 'processing'].includes(c.status) ? `<progress value="${c.done || 0}" max="${c.total || 1}"></progress>` : '')
    + `<div class="row-actions">${actions.join('')}</div></div>`;
}

$('#admin-refresh').addEventListener('click', loadAdmin);
$('#admin-list').addEventListener('click', async (e) => {
  const d = e.target.dataset; if (!me) return;
  try {
    if (d.toggle) await api(`/api/jobs/${d.toggle}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf }, body: JSON.stringify({ available: d.available !== '1' }) });
    else if (d.retry) await api(`/api/jobs/${d.retry}/retry`, { method: 'POST', headers: { 'X-CSRF-Token': me.csrf } });
    else if (d.enrich) {
      const e = enrichEst[d.enrich];
      const msg = e ? `Build interactive pages for all ${e.pages} text pages?\nEstimated ${e.input_tokens.toLocaleString()} input + ${e.output_tokens.toLocaleString()} output tokens on ${e.model} (about $${e.cost_usd.toFixed(2)}).` : 'Build interactive pages?';
      if (!confirm(msg)) return;
      await api(`/api/admin/courses/${d.enrich}/enrich`, { method: 'POST', headers: { 'X-CSRF-Token': me.csrf } });
    }
    else if (d.plan) { openPlan(d.plan, d.title); return; }
    else if (d.delete) { if (!confirm('Delete this course? This cannot be undone.')) return; await api(`/api/jobs/${d.delete}`, { method: 'DELETE', headers: { 'X-CSRF-Token': me.csrf } }); }
    else return;
    await loadAdmin();
  } catch (err) { $('#add-msg').className = 'msg err'; $('#add-msg').textContent = err.message; }
});

/* ---------- Summary pages: lesson page-number plan ---------- */
const plan = { job: null, pageCount: 0 };
const planMsg = (text, cls = '') => { $('#plan-msg').className = `msg ${cls}`; $('#plan-msg').textContent = text; };

async function openPlan(jobId, title) {
  plan.job = jobId;
  $('#plan-title').textContent = `Summary pages · ${title}`;
  $('#plan-rows').innerHTML = '';
  $('#plan-estimate').textContent = '';
  planMsg('Loading…');
  $('#plan-dialog').showModal();
  try {
    const info = await api(`/api/admin/courses/${jobId}/lessons`);
    plan.pageCount = info.page_count;
    $('#plan-offset').value = info.plan.offset;
    for (const l of info.plan.lessons) addPlanRow(l);
    if (!info.plan.lessons.length) addPlanRow();
    showEstimate(info.estimate);
    planMsg(info.saved ? `Saved plan · ${info.built}/${info.plan.lessons.length} lessons built.`
      : 'Suggested from the contents page — not saved yet. Check every row.');
    refreshPdfPages();
  } catch (err) { planMsg(err.message, 'err'); }
}

function addPlanRow(l) {
  const rows = $('#plan-rows').children;
  const prev = rows.length ? readRow(rows[rows.length - 1]) : null;
  const lesson = l || { index: prev ? prev.index + 1 : 1, title: '', start: prev && prev.end ? prev.end + 1 : '', end: '' };
  const tr = document.createElement('tr');
  tr.innerHTML = `<td><input class="p-index" type="number" min="1" max="999" required></td>`
    + `<td><input class="p-title" maxlength="200" placeholder="Lesson title"></td>`
    + `<td><input class="p-start" type="number" required></td><td><input class="p-end" type="number" required></td>`
    + `<td class="p-pdf"></td><td><button type="button" class="p-del" aria-label="Remove lesson" title="Remove lesson">✕</button></td>`;
  tr.querySelector('.p-index').value = lesson.index;
  tr.querySelector('.p-title').value = lesson.title || '';
  tr.querySelector('.p-start').value = lesson.start;
  tr.querySelector('.p-end').value = lesson.end;
  $('#plan-rows').appendChild(tr);
  refreshPdfPages();
}

function readRow(tr) {
  const num = (sel) => parseInt(tr.querySelector(sel).value, 10);
  return { index: num('.p-index'), title: tr.querySelector('.p-title').value.trim(), start: num('.p-start'), end: num('.p-end') };
}

function readPlan() {
  return { offset: parseInt($('#plan-offset').value, 10) || 0, lessons: [...$('#plan-rows').children].map(readRow) };
}

// Show which PDF pages each printed range maps to, so the admin can check it in the reader.
function refreshPdfPages() {
  const offset = parseInt($('#plan-offset').value, 10) || 0;
  $('#plan-offset-hint').textContent = `PDF page = printed page ${offset >= 0 ? '+' : '−'} ${Math.abs(offset)} · the book has ${plan.pageCount} PDF pages`;
  for (const tr of $('#plan-rows').children) {
    const r = readRow(tr), cell = tr.querySelector('.p-pdf');
    const ok = Number.isInteger(r.start) && Number.isInteger(r.end);
    cell.textContent = ok ? `${r.start + offset}–${r.end + offset}` : '—';
    cell.classList.toggle('bad', ok && (r.start > r.end || r.start + offset < 1 || r.end + offset > plan.pageCount));
  }
}

function showEstimate(e) {
  $('#plan-estimate').textContent = e && e.lessons
    ? `Estimate for ${e.lessons} lessons: ≈${fmtK(e.input_tokens)} in + ${fmtK(e.output_tokens)} out tokens on ${e.model} (≈ $${e.cost_usd.toFixed(2)}).` : '';
}

async function savePlan() {
  const body = readPlan();
  const blank = body.lessons.findIndex((l) => ![l.index, l.start, l.end].every(Number.isInteger));
  if (blank >= 0) throw new Error(`Row ${blank + 1}: fill in the lesson number and both page numbers (or remove the row).`);
  const saved = await api(`/api/admin/courses/${plan.job}/lessons`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf }, body: JSON.stringify(body),
  });
  showEstimate(saved.estimate);
  return saved;
}

$('#plan-add').addEventListener('click', () => addPlanRow());
$('#plan-rows').addEventListener('input', refreshPdfPages);
$('#plan-offset').addEventListener('input', refreshPdfPages);
$('#plan-rows').addEventListener('click', (e) => { if (e.target.classList.contains('p-del')) { e.target.closest('tr').remove(); refreshPdfPages(); } });
$('#plan-close').addEventListener('click', () => $('#plan-dialog').close());
$('#plan-dialog').addEventListener('close', loadAdmin);
$('#plan-save').addEventListener('click', async () => {
  try { await savePlan(); planMsg('Saved. The course sidebar now uses these lessons.', 'ok'); } catch (err) { planMsg(err.message, 'err'); }
});
$('#plan-build').addEventListener('click', async () => {
  try {
    const saved = await savePlan();
    const e = saved.estimate;
    if (!confirm(`Build a summary and a test for all ${e.lessons} lessons?\nEstimated ${e.input_tokens.toLocaleString()} input + ${e.output_tokens.toLocaleString()} output tokens on ${e.model} (about $${e.cost_usd.toFixed(2)}).`)) return;
    await api(`/api/admin/courses/${plan.job}/summary`, { method: 'POST', headers: { 'X-CSRF-Token': me.csrf } });
    $('#plan-dialog').close();
  } catch (err) { planMsg(err.message, 'err'); }
});

document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') clearTimeout(pollTimer); else if (me && me.role === 'admin') loadAdmin(); });
setAuthMode('login');
boot();

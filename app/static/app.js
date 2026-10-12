'use strict';
// Book2Course homepage: auth + role-based views (admin builds courses, clients study them).
const $ = (s) => document.querySelector(s);
const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let me = null, authMode = 'login', pollTimer = null, costTimer = null, chosenFile = null;
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
  clearTimeout(pollTimer); clearTimeout(costTimer);
  const box = $('#userbox');
  if (me && me.authenticated) {
    box.innerHTML = `<span class="role">${escape(me.role)}</span><span>${escape(me.username)}</span><button class="ghost" id="logout">Log out</button>`;
    $('#logout').addEventListener('click', logout);
    if (me.role === 'admin') { show('admin-view'); fillLanguages(); loadAdmin(); loadCosts(); loadStudents(); } else { show('client-view'); loadCatalog(); }
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

/* ---------- Usage & costs ---------- */
const usd = (n, digits = 2) => (n == null ? '–' : `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits && Math.abs(n) < 1 ? 4 : digits })}`);
const fmtDate = (iso) => new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });

// AI spend is metered live by the server; Azure numbers are cached there for 10 minutes.
async function loadCosts(refresh = false) {
  clearTimeout(costTimer);
  const msg = $('#costs-msg');
  if ($('#t-month').textContent === '–') { msg.className = 'msg'; msg.textContent = 'Loading costs from Azure…'; }
  try {
    const data = await api(`/api/admin/costs${refresh ? '?refresh=1' : ''}`);
    renderCosts(data);
    msg.className = 'msg'; msg.textContent = (data.azure.errors || []).map((e) => `Azure — ${e}`).join(' ');
  } catch (err) { msg.className = 'msg err'; msg.textContent = err.message; }
  if (me && me.role === 'admin' && !$('#admin-view').hidden) costTimer = setTimeout(loadCosts, 60000);
}

function renderCosts(data) {
  const { ai, azure } = data, costs = azure.costs, credit = azure.credit;
  $('#t-month').textContent = usd(data.month_total_usd);
  $('#t-azure').textContent = costs ? usd(costs.month_usd) : 'n/a';
  $('#t-ai').textContent = usd(ai.totals.month_usd);
  $('#t-today').textContent = usd(ai.totals.today_usd);
  $('#costs-updated').textContent = `Updated ${new Date().toLocaleTimeString()} · Azure checked ${new Date(azure.checked * 1000).toLocaleTimeString()}`;

  $('#credit').hidden = !credit;
  if (credit) {
    const total = credit.original_usd || 100, left = credit.balance_usd ?? 0, after = credit.estimated_usd ?? left;
    const meter = $('#credit-meter');
    $('#credit-left').textContent = `${usd(left)} left of ${usd(total, 0)}`;
    $('#credit-fill').style.width = `${Math.max(0, Math.min(100, (after / total) * 100))}%`;
    $('#credit-pending').style.left = $('#credit-fill').style.width;
    $('#credit-pending').style.width = `${Math.max(0, Math.min(100, ((left - after) / total) * 100))}%`;
    meter.classList.toggle('low', after / total < 0.2);
    meter.setAttribute('aria-valuemax', total); meter.setAttribute('aria-valuenow', after);
    // Only Azure usage draws down this credit (Gemini is billed by Google), so project from Azure's pace.
    const recent = (costs ? costs.daily : []).slice(-8, -1);           // last 7 complete days
    const pace = recent.length ? recent.reduce((a, d) => a + d.usd, 0) / recent.length : 0;
    let line = `≈ ${usd(after)} once ${usd(credit.pending_usd)} of usage Azure is still processing is charged (striped part).`;
    if (pace > 0) {
      const runOut = new Date(Date.now() + (after / pace) * 86400000);
      const expires = credit.expires ? new Date(credit.expires) : null;
      line += ` At the last 7 days' pace (~${usd(pace)}/day) it runs out around ${fmtDate(runOut)}`
        + (expires && runOut > expires ? `, after it expires on ${fmtDate(expires)}.` : '.');
    }
    if (credit.expires) line += ` Credit expires ${fmtDate(credit.expires)}.`;
    $('#credit-detail').textContent = line;
  }

  // Daily stacked bars: Azure + AI per day for the last 30 days.
  const days = {};
  const today = new Date();
  for (let i = 29; i >= 0; i--) { const d = new Date(today); d.setUTCDate(d.getUTCDate() - i); days[d.toISOString().slice(0, 10)] = { azure: 0, ai: 0 }; }
  for (const d of (costs ? costs.daily : [])) if (days[d.date]) days[d.date].azure = d.usd;
  for (const d of ai.daily) if (days[d.date]) days[d.date].ai = d.usd;
  const entries = Object.entries(days), max = Math.max(...entries.map(([, v]) => v.azure + v.ai), 0);
  const chart = $('#cost-chart');
  chart.innerHTML = max > 0 ? entries.map(([date, v]) => {
    const h = (x) => (x > 0 ? Math.max(2, (x / max) * 100) : 0);
    return `<div class="bar" data-date="${date}" data-azure="${v.azure}" data-ai="${v.ai}">`
      + `${v.azure ? `<span class="azure" data-h="${h(v.azure)}"></span>` : ''}${v.ai ? `<span class="ai" data-h="${h(v.ai)}"></span>` : ''}</div>`;
  }).join('') : '<p class="chart-empty">No costs recorded yet.</p>';
  // The CSP blocks inline style attributes, so heights are set through the DOM.
  for (const seg of chart.querySelectorAll('[data-h]')) seg.style.height = `${seg.dataset.h}%`;

  $('#azure-rows').innerHTML = costs && costs.services.length
    ? costs.services.map((s) => `<tr><td>${escape(s.service)}</td><td>${usd(s.usd)}</td></tr>`).join('')
    : `<tr><td colspan="2">${costs ? 'No Azure costs yet this month.' : 'Azure costs are not available.'}</td></tr>`;
  $('#ai-rows').innerHTML = ai.features.map((f) => `<tr><td>${escape(f.label)}</td><td>${f.calls}</td>`
    + `<td>${fmtK(f.input_tokens)} / ${fmtK(f.output_tokens)}</td><td>${usd(f.month_usd)}</td></tr>`).join('')
    + `<tr><td colspan="4" class="costs-note">${escape(ai.note)}</td></tr>`;
}

$('#costs-refresh').addEventListener('click', () => loadCosts(true));
$('#cost-chart').addEventListener('mousemove', (e) => {
  const bar = e.target.closest('.bar'), tip = $('#chart-tip');
  if (!bar) { tip.hidden = true; return; }
  const az = Number(bar.dataset.azure), ai = Number(bar.dataset.ai);
  tip.innerHTML = `<strong>${fmtDate(bar.dataset.date + 'T00:00:00Z')}</strong><br>Azure ${usd(az)}<br>AI ${usd(ai)}<br>Total ${usd(az + ai)}`;
  tip.hidden = false;
  const wrap = $('.chart-wrap').getBoundingClientRect(), r = bar.getBoundingClientRect();
  tip.style.left = `${Math.min(wrap.width - tip.offsetWidth - 8, Math.max(8, r.left - wrap.left - tip.offsetWidth / 2))}px`;
  tip.style.top = '34px';
});
$('#cost-chart').addEventListener('mouseleave', () => { $('#chart-tip').hidden = true; });

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
// Android app download box: shown when an APK is published, never inside the app itself.
async function loadAppDownload() {
  if (/Book2CourseApp\//.test(navigator.userAgent)) return;
  const info = await fetch('/api/app-download').then((r) => r.json()).catch(() => ({}));
  const apk = info.android;
  if (!apk) return;
  $('#apk-link').href = apk.url;
  $('#apk-meta').textContent = [apk.version && `Version ${apk.version}`, `${apk.size_mb} MB`].filter(Boolean).join(' · ');
  $('#app-download').hidden = false;
}

// ---- Admin: give students extra free Live minutes ----
let students = [];
const fmtMin = (sec) => `${Math.floor(sec / 60)} min`;
const fmtDay = (t) => (t ? new Date(t * 1000).toLocaleDateString() : '—');

async function loadStudents() {
  try {
    const data = await api('/api/admin/users');
    students = data.users;
    $('#students-note').textContent = `Every new student starts with ${data.free_minutes} free minutes. Add more here — they are added to what the student has left.`;
    renderStudents();
  } catch (err) { $('#students-msg').textContent = err.message; $('#students-msg').className = 'msg err'; }
}

function renderStudents() {
  const q = $('#student-search').value.trim().toLowerCase();
  const shown = students.filter((u) => u.username.toLowerCase().includes(q));
  $('#student-count').textContent = String(students.length);
  $('#student-rows').innerHTML = shown.length ? shown.map((u) => `
    <tr>
      <td class="who">${escape(u.username)}</td>
      <td>${fmtDay(u.created)}</td>
      <td class="${u.seconds <= 0 ? 'zero' : ''}">${fmtMin(u.seconds)}</td>
      <td>${fmtMin(u.used_seconds)}</td>
      <td><form class="grant" data-user="${escape(u.username)}">
        <input type="number" min="1" max="10000" step="1" value="10" aria-label="Minutes to add for ${escape(u.username)}" required>
        <button type="submit">+ Add</button>
      </form></td>
    </tr>`).join('') : `<tr><td colspan="5" class="empty">${students.length ? 'No student matches that search.' : 'No students yet.'}</td></tr>`;
}

$('#student-search').addEventListener('input', renderStudents);
$('#students-refresh').addEventListener('click', loadStudents);
$('#student-rows').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target, username = form.dataset.user, minutes = Number(form.querySelector('input').value);
  const msg = $('#students-msg');
  if (!Number.isInteger(minutes) || minutes < 1 || minutes > 10000) { msg.textContent = 'Enter a whole number of minutes (1–10000).'; msg.className = 'msg err'; return; }
  form.querySelector('button').disabled = true;
  try {
    const res = await api('/api/admin/live-credit', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf }, body: JSON.stringify({ username, minutes }) });
    const u = students.find((x) => x.username === username);
    if (u) u.seconds = res.seconds;
    renderStudents();
    msg.textContent = `Added ${minutes} min to ${username} — now ${fmtMin(res.seconds)} left.`; msg.className = 'msg ok';
  } catch (err) { msg.textContent = err.message; msg.className = 'msg err'; form.querySelector('button').disabled = false; }
});

boot();
loadAppDownload();

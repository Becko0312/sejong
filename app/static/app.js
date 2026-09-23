'use strict';
// Book2Course homepage: auth + role-based views (admin builds courses, clients study them).
const $ = (s) => document.querySelector(s);
const escape = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let me = null, authMode = 'login', pollTimer = null, chosenFile = null;

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
    if (me.role === 'admin') { show('admin-view'); loadAdmin(); } else { show('client-view'); loadCatalog(); }
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
    name: chosenFile.name, ocr: $('#c-ocr').checked, languages: $('#c-langs').value,
    title: $('#c-title').value.trim(), source_lang: $('#c-source').value.trim(),
    target_lang: $('#c-target').value.trim(), description: $('#c-desc').value.trim(),
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
    if (courses.some((c) => ['uploading', 'queued', 'processing'].includes(c.status))) pollTimer = setTimeout(loadAdmin, 5000);
    $('#course-count').textContent = courses.length;
    list.innerHTML = courses.length ? courses.map(adminRow).join('') : '<div class="empty">No courses yet. Add one on the left.</div>';
  } catch (err) { list.innerHTML = `<div class="empty">${escape(err.message)}</div>`; }
}

function adminRow(c) {
  const langs = [c.source_lang, c.target_lang].filter(Boolean).join(' → ');
  const done = c.status === 'completed';
  const actions = [];
  if (done) actions.push(`<a class="open" href="/course/${encodeURIComponent(c.id)}">Open ↗</a>`);
  if (done) actions.push(`<button class="ghost" data-toggle="${c.id}" data-available="${c.available ? 1 : 0}">${c.available ? 'Hide from students' : 'Publish to students'}</button>`);
  if (c.status === 'failed' && (c.attempts || 0) < 3) actions.push(`<button class="ghost" data-retry="${c.id}">Retry</button>`);
  actions.push(`<button class="danger" data-delete="${c.id}">Delete</button>`);
  const meta = done ? `${langs ? escape(langs) + ' · ' : ''}${c.available ? 'Visible to students' : 'Hidden'}` : `${langs ? escape(langs) + ' · ' : ''}${c.done || 0}/${c.total || '…'} pages`;
  return `<div class="course-row"><div class="top"><h3>${escape(c.title)}</h3><span class="status ${escape(c.status)}">${escape(c.status)}</span></div>`
    + `<p class="meta">${meta}${c.error ? ` · <span style="color:#a5462f">${escape(c.error)}</span>` : ''}</p>`
    + (['uploading', 'queued', 'processing'].includes(c.status) ? `<progress value="${c.done || 0}" max="${c.total || 1}"></progress>` : '')
    + `<div class="row-actions">${actions.join('')}</div></div>`;
}

$('#admin-refresh').addEventListener('click', loadAdmin);
$('#admin-list').addEventListener('click', async (e) => {
  const d = e.target.dataset; if (!me) return;
  try {
    if (d.toggle) await api(`/api/jobs/${d.toggle}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf }, body: JSON.stringify({ available: d.available !== '1' }) });
    else if (d.retry) await api(`/api/jobs/${d.retry}/retry`, { method: 'POST', headers: { 'X-CSRF-Token': me.csrf } });
    else if (d.delete) { if (!confirm('Delete this course? This cannot be undone.')) return; await api(`/api/jobs/${d.delete}`, { method: 'DELETE', headers: { 'X-CSRF-Token': me.csrf } }); }
    else return;
    await loadAdmin();
  } catch (err) { $('#add-msg').className = 'msg err'; $('#add-msg').textContent = err.message; }
});

document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') clearTimeout(pollTimer); else if (me && me.role === 'admin') loadAdmin(); });
setAuthMode('login');
boot();

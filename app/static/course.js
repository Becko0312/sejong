'use strict';
// Book2Course reader: renders a converted book as a lesson-structured course + AI tutor.
const app = document.getElementById('app');
const jobId = decodeURIComponent(location.pathname.replace(/^\/course\//, '').replace(/\/$/, ''));
const API = { course: `/api/courses/${jobId}`, image: (n) => `/books/${jobId}/page-${n}.png`, tutor: `/api/tutor/${jobId}` };
const state = { csrf: '', tutor: { enabled: false }, course: null, pages: [], lessons: [], title: '', current: 0, history: [], busy: false, taught: new Set() };
const MODE_LABELS = { intro: 'Start teaching this page', explain: '📖 Explain this page', vocab: '🔤 Teach the vocabulary', quiz: '✍️ Quiz me on this page', practice: '🗣️ Practice speaking' };

const el = {
  title: document.getElementById('book-title'), sub: document.getElementById('book-sub'),
  courseHome: document.getElementById('course-home'), tocList: document.getElementById('toc-list'),
  pageLabel: document.getElementById('page-label'), lessonLabel: document.getElementById('lesson-label'),
  image: document.getElementById('page-image'), text: document.getElementById('page-text'),
  flag: document.getElementById('page-flag'), prev: document.getElementById('prev'), next: document.getElementById('next'),
  homeBtn: document.getElementById('home-btn'), home: document.getElementById('home'), stage: document.getElementById('stage'),
  homeTitle: document.getElementById('home-title'), homeMeta: document.getElementById('home-meta'),
  homeEyebrow: document.getElementById('home-eyebrow'), lessonGrid: document.getElementById('lesson-grid'),
  chat: document.getElementById('chat'), composer: document.getElementById('composer'),
  question: document.getElementById('question'), send: document.getElementById('send'),
  mic: document.getElementById('mic'),
  micLang: document.getElementById('mic-lang'), status: document.getElementById('tutor-status'),
  tutorPanel: document.getElementById('tutor'), tutorToggle: document.getElementById('tutor-toggle'),
  teachActions: document.getElementById('teach-actions'),
  liveBtn: document.getElementById('live-btn'), voice: document.getElementById('voice'),
  orb: document.getElementById('orb'), voiceStatus: document.getElementById('voice-status'),
  voiceTranscript: document.getElementById('voice-transcript'), voiceEnd: document.getElementById('voice-end'),
};
const LANG = { korean: 'Korean', japanese: 'Japanese', chinese: 'Chinese', english: 'English' };

async function boot() {
  try {
    const account = await fetch('/api/me').then((r) => r.json());
    if (!account.authenticated) { location.href = '/'; return; }
    state.csrf = account.csrf;
    const course = await fetch(API.course).then((r) => {
      if (r.status === 401) { location.href = '/'; throw new Error('Please sign in.'); }
      if (!r.ok) throw new Error(r.status === 404 ? 'This course is not available.' : 'Could not load this course.');
      return r.json();
    });
    state.course = course;
    state.pages = course.pages || [];
    state.lessons = course.lessons || [];
    state.title = course.title || 'Course';
    state.tutor = course.tutor || session.tutor || { enabled: false };
    if (!state.pages.length) throw new Error('This course has no pages.');
    el.title.textContent = state.title;
    const langBit = course.language ? `${LANG[course.language] || course.language} · ` : '';
    el.sub.textContent = `${langBit}${state.pages.length} pages${state.lessons.length ? ` · ${state.lessons.length} lessons` : ''}`;
    document.title = `${state.title} · Book2Course`;
    buildToc();
    buildHome();
    setupTutor();
    const fromHash = parseInt(location.hash.replace('#page-', ''), 10);
    if (Number.isInteger(fromHash)) show(fromHash); else showHome();
    app.classList.remove('loading');
  } catch (err) {
    app.classList.remove('loading');
    el.title.textContent = 'Unavailable';
    el.sub.textContent = err.message;
    showHome();
    el.lessonGrid.innerHTML = '';
    el.homeTitle.textContent = 'Unavailable';
    el.homeMeta.textContent = err.message;
  }
}

/* ---------- Table of contents ---------- */
function pageButton(number, label) {
  const b = document.createElement('button');
  b.className = 'toc-item';
  b.dataset.page = number;
  const span = document.createElement('span');
  span.textContent = label || `Page ${number}`;
  b.appendChild(span);
  const page = state.pages.find((p) => p.number === number);
  if (page && page.needs_review) {
    const rev = document.createElement('span');
    rev.className = 'rev';
    rev.textContent = 'REVIEW';
    b.appendChild(rev);
  }
  b.addEventListener('click', () => show(number));
  return b;
}

function buildToc() {
  el.tocList.innerHTML = '';
  if (!state.lessons.length) {
    for (const page of state.pages) el.tocList.appendChild(pageButton(page.number));
    return;
  }
  const front = (state.course.front_pages || []);
  if (front.length) el.tocList.appendChild(pageGroup('Front matter', front));
  for (const lesson of state.lessons) {
    const pages = [];
    for (let n = lesson.start_page; n <= lesson.end_page; n++) pages.push(n);
    el.tocList.appendChild(pageGroup(`${lesson.index}. ${lesson.title || 'Lesson ' + lesson.index}`, pages, lesson.index));
  }
}

function pageGroup(label, pageNumbers, lessonIndex) {
  const details = document.createElement('details');
  details.className = 'toc-group';
  if (lessonIndex != null) details.dataset.lesson = lessonIndex;
  const summary = document.createElement('summary');
  summary.textContent = label;
  details.appendChild(summary);
  for (const n of pageNumbers) details.appendChild(pageButton(n));
  return details;
}

/* ---------- Landing / overview ---------- */
function buildHome() {
  el.homeTitle.textContent = state.title;
  el.homeEyebrow.textContent = state.course.language ? `${LANG[state.course.language] || state.course.language} course`.toUpperCase() : 'COURSE';
  el.homeMeta.textContent = `${state.pages.length} pages${state.lessons.length ? ` · ${state.lessons.length} lessons` : ''} · Tutor ${state.tutor.enabled ? 'on' : 'off'}`;
  el.lessonGrid.innerHTML = '';
  if (state.lessons.length) {
    for (const lesson of state.lessons) {
      const card = document.createElement('button');
      card.className = 'lesson-card';
      card.innerHTML = `<span class="lesson-num">Lesson ${lesson.index}</span>`;
      const h = document.createElement('strong');
      h.textContent = lesson.title || `Lesson ${lesson.index}`;
      card.appendChild(h);
      const meta = document.createElement('small');
      meta.textContent = lesson.start_page === lesson.end_page ? `Page ${lesson.start_page}` : `Pages ${lesson.start_page}–${lesson.end_page}`;
      card.appendChild(meta);
      card.addEventListener('click', () => show(lesson.start_page));
      el.lessonGrid.appendChild(card);
    }
  } else {
    const card = document.createElement('button');
    card.className = 'lesson-card wide';
    card.innerHTML = '<strong>Start reading</strong><small>This book has no detected lessons — open it page by page.</small>';
    card.addEventListener('click', () => show(1));
    el.lessonGrid.appendChild(card);
  }
}

/* ---------- Views ---------- */
function showHome() {
  state.current = 0;
  location.hash = '';
  el.home.hidden = false;
  el.stage.hidden = true;
  el.teachActions.hidden = true;
  el.lessonLabel.textContent = '';
  el.pageLabel.textContent = `${state.pages.length} pages`;
  el.prev.disabled = el.next.disabled = false;
  for (const item of el.tocList.querySelectorAll('.toc-item')) item.classList.remove('current');
}

function show(number) {
  const page = state.pages.find((p) => p.number === number);
  if (!page) return;
  state.current = number;
  location.hash = `page-${number}`;
  el.home.hidden = true;
  el.stage.hidden = false;
  el.pageLabel.textContent = `Page ${number} of ${state.pages.length}`;
  el.image.src = API.image(number);
  el.image.alt = `Page ${number}`;
  const text = (page.text || '').trim();
  el.text.textContent = text || 'No text was detected on this page.';
  el.flag.textContent = page.text_source === 'ocr' ? 'Machine-recognized (OCR) text — may contain errors.'
    : (page.needs_ocr ? 'No embedded text on this page.' : '');
  const lesson = state.lessons.find((l) => l.index === page.lesson);
  el.lessonLabel.textContent = lesson ? `${lesson.index}. ${lesson.title || 'Lesson ' + lesson.index}` : '';
  el.prev.disabled = number <= 1;
  el.next.disabled = number >= state.pages.length;
  for (const item of el.tocList.querySelectorAll('.toc-item')) item.classList.toggle('current', Number(item.dataset.page) === number);
  if (lesson) { const g = el.tocList.querySelector(`.toc-group[data-lesson="${lesson.index}"]`); if (g) g.open = true; }
  const cur = el.tocList.querySelector('.toc-item.current');
  if (cur) cur.scrollIntoView({ block: 'nearest' });
  el.stage.scrollTop = 0;
  if (state.tutor.enabled) el.teachActions.hidden = false;
  maybeTeachLesson(page);
}

/* ---------- Tutor ---------- */
function setupTutor() {
  if (state.tutor.enabled) {
    el.status.textContent = 'Online';
    el.status.className = 'tutor-status on';
    addBot(`👋 Hi! I'm your tutor. Open a lesson and I'll start teaching it — or use the buttons below to have me explain the page, teach the vocabulary, quiz you, or practice speaking. You can also type or tap 🎤 to ask anything.`);
  } else {
    el.status.textContent = 'Offline';
    el.status.className = 'tutor-status off';
    addNote('The AI tutor is not switched on for this server. You can still read every page of the course.');
    el.question.disabled = el.send.disabled = el.mic.disabled = true;
  }
  // Live voice needs the Gemini provider (bidirectional audio); hide it otherwise.
  if (!(state.tutor.enabled && state.tutor.provider === 'gemini')) el.liveBtn.hidden = true;
  setupMic();
}

// Shared tutor turn for both typed questions and one-tap teaching actions.
async function runTutor({ question = '', mode = null, label }) {
  if (state.busy || !state.tutor.enabled) return;
  const page = state.current || 1;
  addUser(label || question);
  state.busy = true;
  el.send.disabled = true;
  el.teachActions.classList.add('busy');
  const typing = addTyping();
  try {
    const res = await fetch(API.tutor, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
      body: JSON.stringify({ question, mode, page, history: state.history.slice(-10) }),
    });
    const data = await res.json().catch(() => ({}));
    typing.remove();
    if (!res.ok) { addErr(data.detail || 'The tutor could not respond. Please try again.'); return; }
    addBot(data.reply);
    state.history.push({ role: 'user', content: label || question }, { role: 'assistant', content: data.reply });
  } catch (err) {
    typing.remove();
    addErr('Network error reaching the tutor. Please try again.');
  } finally {
    state.busy = false;
    el.send.disabled = false;
    el.teachActions.classList.remove('busy');
    el.question.focus();
  }
}

el.composer.addEventListener('submit', (event) => {
  event.preventDefault();
  const question = el.question.value.trim();
  if (!question) return;
  el.question.value = '';
  el.question.style.height = 'auto';
  runTutor({ question });
});

function sendTeach(mode) { runTutor({ mode, label: MODE_LABELS[mode] || 'Teach me' }); }
for (const chip of el.teachActions.querySelectorAll('.chip')) {
  chip.addEventListener('click', () => sendTeach(chip.dataset.mode));
}

// Proactive: when a lesson is opened for the first time, the tutor starts teaching it.
function maybeTeachLesson(page) {
  if (!state.tutor.enabled || state.busy) return;
  const lesson = page && page.lesson;
  if (lesson == null || state.taught.has(lesson)) return;
  state.taught.add(lesson);
  sendTeach('intro');
}

el.question.addEventListener('input', () => {
  el.question.style.height = 'auto';
  el.question.style.height = Math.min(el.question.scrollHeight, 120) + 'px';
});
el.question.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); el.composer.requestSubmit(); }
});

function bubble(cls, textContent) {
  const div = document.createElement('div');
  div.className = `msg ${cls}`;
  div.textContent = textContent;
  el.chat.appendChild(div);
  el.chat.scrollTop = el.chat.scrollHeight;
  return div;
}
const addUser = (t) => bubble('user', t);
const addBot = (t) => bubble('bot', t);
const addErr = (t) => bubble('err', t);
const addNote = (t) => bubble('note', t);
const addTyping = () => bubble('bot typing', 'Tutor is thinking…');

/* ---------- Voice ---------- */
function setupMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !state.tutor.enabled) { el.mic.disabled = true; el.mic.title = 'Voice input is not available in this browser'; return; }
  let recognizer = null, recording = false;
  el.mic.addEventListener('click', () => {
    if (recording) { recognizer && recognizer.stop(); return; }
    recognizer = new Recognition();
    recognizer.lang = el.micLang.value;
    recognizer.interimResults = false;
    recognizer.maxAlternatives = 1;
    recognizer.onstart = () => { recording = true; el.mic.classList.add('recording'); };
    recognizer.onerror = () => { recording = false; el.mic.classList.remove('recording'); };
    recognizer.onend = () => { recording = false; el.mic.classList.remove('recording'); };
    recognizer.onresult = (event) => {
      const said = Array.from(event.results).map((r) => r[0].transcript).join(' ').trim();
      if (said) { el.question.value = (el.question.value ? el.question.value + ' ' : '') + said; el.question.dispatchEvent(new Event('input')); el.question.focus(); }
    };
    try { recognizer.start(); } catch (_) { /* already started */ }
  });
}

/* ---------- Live voice mode (Gemini Live via server proxy) ---------- */
const b64ToInt16 = (b64) => { const bin = atob(b64); const bytes = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); return new Int16Array(bytes.buffer); };
const int16ToB64 = (buf) => { const bytes = new Uint8Array(buf); let s = ''; for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return btoa(s); };

const live = { active: false, ws: null, capCtx: null, playCtx: null, stream: null, playNode: null, lastRole: null };

function vStatus(text, cls) { el.voiceStatus.textContent = text; el.voice.className = 'voice-overlay' + (cls ? ' ' + cls : ''); }
function vLine(role, text) {
  if (role === live.lastRole && el.voiceTranscript.lastChild) { el.voiceTranscript.lastChild.textContent += text; }
  else { const d = document.createElement('div'); d.className = 'vt ' + (role === 'you' ? 'you' : 'tutor'); d.textContent = text; el.voiceTranscript.appendChild(d); live.lastRole = role; }
  el.voiceTranscript.scrollTop = el.voiceTranscript.scrollHeight;
}

async function startLive() {
  if (live.active || !state.tutor.enabled) return;
  live.active = true; live.lastRole = null;
  el.voice.hidden = false; el.voiceTranscript.innerHTML = ''; vStatus('Connecting…');
  el.liveBtn.classList.add('active');
  try {
    live.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    live.playCtx = new AudioContext({ sampleRate: 24000 });
    await live.playCtx.audioWorklet.addModule('/static/live-playback.worklet.js');
    live.playNode = new AudioWorkletNode(live.playCtx, 'playback');
    live.playNode.connect(live.playCtx.destination);
    live.playNode.port.onmessage = (e) => { if (live.active) vStatus(e.data === 'speaking' ? 'Tutor is speaking…' : 'Listening — go ahead', e.data === 'speaking' ? 'speaking' : 'listening'); };

    live.capCtx = new AudioContext({ sampleRate: 16000 });
    await live.capCtx.audioWorklet.addModule('/static/live-capture.worklet.js');
    const src = live.capCtx.createMediaStreamSource(live.stream);
    const capNode = new AudioWorkletNode(live.capCtx, 'capture');
    capNode.port.onmessage = (e) => { if (live.ws && live.ws.readyState === 1) live.ws.send(JSON.stringify({ type: 'audio', data: int16ToB64(e.data) })); };
    src.connect(capNode);

    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    live.ws = new WebSocket(`${scheme}://${location.host}/api/live/${jobId}`);
    live.ws.onopen = () => live.ws.send(JSON.stringify({ type: 'start', page: state.current || 1 }));
    live.ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === 'ready') vStatus('Listening — go ahead', 'listening');
      else if (m.type === 'audio') { const pcm = b64ToInt16(m.data); const f = new Float32Array(pcm.length); for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768; live.playNode.port.postMessage(f); }
      else if (m.type === 'interrupted') live.playNode.port.postMessage('clear');
      else if (m.type === 'user_text') vLine('you', m.text);
      else if (m.type === 'tutor_text') vLine('tutor', m.text);
      else if (m.type === 'turn_end') live.lastRole = null;
      else if (m.type === 'error') { vStatus(m.detail || 'The tutor is unavailable.'); setTimeout(stopLive, 2500); }
      else if (m.type === 'end') stopLive();
    };
    live.ws.onerror = () => vStatus('Connection error.');
    live.ws.onclose = () => { if (live.active) stopLive(); };
  } catch (err) {
    vStatus(err && err.name === 'NotAllowedError' ? 'Microphone permission is needed for voice mode.' : 'Could not start voice mode.');
    setTimeout(stopLive, 2500);
  }
}

function stopLive() {
  live.active = false;
  el.voice.hidden = true;
  el.liveBtn.classList.remove('active');
  try { live.ws && live.ws.close(); } catch (_) {}
  try { live.stream && live.stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
  try { live.capCtx && live.capCtx.close(); } catch (_) {}
  try { live.playCtx && live.playCtx.close(); } catch (_) {}
  live.ws = live.stream = live.capCtx = live.playCtx = live.playNode = null;
}

el.liveBtn.addEventListener('click', () => { if (live.active) stopLive(); else startLive(); });
el.voiceEnd.addEventListener('click', stopLive);

/* ---------- Navigation & layout ---------- */
el.homeBtn.addEventListener('click', showHome);
el.courseHome.addEventListener('click', showHome);
el.prev.addEventListener('click', () => show((state.current || 1) - 1));
el.next.addEventListener('click', () => show((state.current || 0) + 1));
document.addEventListener('keydown', (event) => {
  if (document.activeElement === el.question) return;
  if (event.key === 'ArrowLeft' && state.current) show(state.current - 1);
  if (event.key === 'ArrowRight') show((state.current || 0) + 1);
});
el.tutorToggle.addEventListener('click', () => {
  const hidden = el.tutorPanel.hasAttribute('hidden');
  el.tutorPanel.toggleAttribute('hidden', !hidden);
  app.classList.toggle('no-tutor', !hidden);
  el.tutorToggle.setAttribute('aria-expanded', String(hidden));
});

boot();

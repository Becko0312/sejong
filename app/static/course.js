'use strict';
// Book2Course reader: renders a converted book as a lesson-structured course + AI tutor.
const app = document.getElementById('app');
const jobId = decodeURIComponent(location.pathname.replace(/^\/course\//, '').replace(/\/$/, ''));
const API = { course: `/api/courses/${jobId}`, image: (n) => `/books/${jobId}/page-${n}.png`, tutor: `/api/tutor/${jobId}`, tts: '/api/tts', interactive: (n) => `/api/courses/${jobId}/pages/${n}/interactive` };
const state = { csrf: '', role: '', tutor: { enabled: false }, tts: { enabled: false }, enrich: { enabled: false }, view: 'scan', viewLoad: 0, course: null, pages: [], lessons: [], title: '', current: 0, history: [], busy: false, taught: new Set() };
const MODE_LABELS = { intro: 'Start teaching this page', explain: '📖 Explain this page', vocab: '🔤 Teach the vocabulary', quiz: '✍️ Quiz me on this page', practice: '🗣️ Practice speaking' };

const el = {
  title: document.getElementById('book-title'), sub: document.getElementById('book-sub'),
  courseHome: document.getElementById('course-home'), tocList: document.getElementById('toc-list'),
  pageLabel: document.getElementById('page-label'), lessonLabel: document.getElementById('lesson-label'),
  image: document.getElementById('page-image'), text: document.getElementById('page-text'),
  frame: document.getElementById('page-frame'), textBox: document.getElementById('page-text-box'),
  interactive: document.getElementById('interactive'), viewSwitch: document.getElementById('view-switch'),
  viewScan: document.getElementById('view-scan'), viewInteractive: document.getElementById('view-interactive'),
  flag: document.getElementById('page-flag'), prev: document.getElementById('prev'), next: document.getElementById('next'),
  homeBtn: document.getElementById('home-btn'), home: document.getElementById('home'), stage: document.getElementById('stage'),
  homeTitle: document.getElementById('home-title'), homeMeta: document.getElementById('home-meta'),
  homeEyebrow: document.getElementById('home-eyebrow'), lessonGrid: document.getElementById('lesson-grid'),
  chat: document.getElementById('chat'), composer: document.getElementById('composer'),
  question: document.getElementById('question'), send: document.getElementById('send'),
  mic: document.getElementById('mic'),
  liveBtn: document.getElementById('live-btn'), voice: document.getElementById('voice'),
  orb: document.getElementById('orb'), voiceStatus: document.getElementById('voice-status'),
  voiceTranscript: document.getElementById('voice-transcript'), voiceEnd: document.getElementById('voice-end'),
  voiceTimer: document.getElementById('voice-timer'), paywall: document.getElementById('paywall'),
  paywallClose: document.getElementById('paywall-close'), paywallTitle: document.getElementById('paywall-title'),
  paywallSub: document.getElementById('paywall-sub'), paywallPackages: document.getElementById('paywall-packages'),
  paywallPay: document.getElementById('paywall-pay'), paywallStatus: document.getElementById('paywall-status'),
  micLang: document.getElementById('mic-lang'), status: document.getElementById('tutor-status'),
  tutorPanel: document.getElementById('tutor'), tutorToggle: document.getElementById('tutor-toggle'),
  teachActions: document.getElementById('teach-actions'),
  zoomCtl: document.getElementById('zoom-ctl'), zoomIn: document.getElementById('zoom-in'),
  zoomOut: document.getElementById('zoom-out'), zoomReset: document.getElementById('zoom-reset'),
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
    state.tts = course.tts || { enabled: false };
    state.enrich = course.enrich || { enabled: false };
    state.role = account.role;
    // The switch appears once interactive pages exist, or for admins who can build them.
    el.viewSwitch.hidden = !(state.enrich.enabled && ((state.enrich.built || 0) > 0 || account.role === 'admin'));
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
  el.zoomCtl.hidden = true;
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
  applyView();
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
  else loadBilling();
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

const SPEAK_SCRIPTS = [
  { re: /[\u1100-\u11FF\u3130-\u318F\uAC00-\uD7A3\uA960-\uD7FF]/, lang: 'ko-KR' },
  { re: /[\u0400-\u04FF]/, lang: 'mn-MN' },
  { re: /[\u3040-\u30FF\u31F0-\u31FF]/, lang: 'ja-JP' },
  { re: /[\u3400-\u4DBF\u4E00-\u9FFF]/, lang: 'zh-CN' },
];
const SPEAK_VOICE_FALLBACK = { 'mn-MN': ['ru'] };
// Parenthesized text in these scripts is real content (e.g. a Mongolian gloss);
// anything else in parens is on-screen romanization like "(itta, eoptta)".
const SPEAK_KEEP_PARENS = new RegExp(SPEAK_SCRIPTS.map((s) => s.re.source).join('|'));

function speakScriptLang(ch) {
  for (const s of SPEAK_SCRIPTS) if (s.re.test(ch)) return s.lang;
  return null;
}

function speakSegments(text) {
  const segments = [];
  let lead = '';
  for (const ch of text) {
    const lang = speakScriptLang(ch);
    if (!lang) {
      if (segments.length) segments[segments.length - 1].text += ch;
      else lead += ch;
      continue;
    }
    if (!segments.length || segments[segments.length - 1].lang !== lang) segments.push({ lang, text: lead + ch });
    else segments[segments.length - 1].text += ch;
    lead = '';
  }
  if (!segments.length && lead.trim()) segments.push({ lang: null, text: lead });
  return segments;
}

function speakVoice(lang) {
  const voices = window.speechSynthesis.getVoices();
  const norm = (lang || '').toLowerCase();
  let voice = voices.find((v) => v.lang && v.lang.toLowerCase() === norm);
  if (!voice && norm) voice = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(norm.split('-')[0]));
  for (const fb of (!voice && SPEAK_VOICE_FALLBACK[lang]) || []) {
    voice = voices.find((v) => v.lang && v.lang.toLowerCase().startsWith(fb));
    if (voice) break;
  }
  return voice || null;
}

const ttsState = { token: 0, audio: null };

function setReading(on) {
  if (el.stopSpeak) el.stopSpeak.disabled = !on;
}

function stopSpeaking() {
  ttsState.token += 1;
  setReading(false);
  if (ttsState.audio) { ttsState.audio.pause(); ttsState.audio = null; }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
}

function playAudio(audio) {
  return new Promise((resolve, reject) => {
    const settle = (fn, arg) => { audio.onended = audio.onpause = audio.onerror = null; fn(arg); };
    audio.onended = () => settle(resolve);
    audio.onpause = () => settle(resolve); // stopSpeaking() pauses: treat as end of segment
    audio.onerror = () => settle(reject, new Error('playback failed'));
    audio.play().catch((err) => {
      if (err && err.name === 'NotAllowedError') {
        // Autoplay blocked (common on phones): resume on the next tap instead
        // of losing the speech, unless this segment was cancelled meanwhile.
        document.addEventListener('pointerdown', () => {
          if (ttsState.audio !== audio) { settle(resolve); return; }
          audio.play().catch((e) => settle(reject, e));
        }, { once: true });
        return;
      }
      settle(reject, err);
    });
  });
}

async function speakViaServer(text, lang) {
  const res = await fetch(API.tts, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
    body: JSON.stringify({ text, lang }),
  });
  if (!res.ok) throw new Error(`tts ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  const audio = new Audio(url);
  ttsState.audio = audio;
  try {
    await playAudio(audio);
  } finally {
    if (ttsState.audio === audio) ttsState.audio = null;
    URL.revokeObjectURL(url);
  }
}

function speakLocal(text, lang) {
  if (!('speechSynthesis' in window)) return null;
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = lang;
  const voice = speakVoice(lang);
  if (voice) utter.voice = voice;
  window.speechSynthesis.speak(utter);
  return utter;
}

function speakableText(text) {
  // Drop markdown noise and romanizations in parens: reading aids, not speech.
  return text.replace(/[*`]/g, '').replace(/\([^()]*\)/g, (m) => (SPEAK_KEEP_PARENS.test(m) ? m : ''));
}

async function speak(text) {
  const segments = speakSegments(speakableText(text));
  if (!segments.length) return;
  stopSpeaking();
  const token = ttsState.token;
  setReading(true);
  const pref = { korean: 'ko', japanese: 'ja', chinese: 'zh' }[state.course && state.course.language] || 'ko';
  const fallbackLang = (el.micLang && el.micLang.value) || pref + '-' + pref.toUpperCase();
  // Prefer server-side Azure neural voices (the only way to get mn-MN);
  // degrade to browser voices for the rest of this reply if it fails.
  let useServer = Boolean(state.tts && state.tts.enabled);
  let lastUtter = null;
  for (const seg of segments) {
    if (token !== ttsState.token) return;
    const lang = seg.lang || fallbackLang;
    if (useServer) {
      try { await speakViaServer(seg.text, lang); continue; } catch (_) { useServer = false; }
      if (token !== ttsState.token) return;
    }
    lastUtter = speakLocal(seg.text, lang) || lastUtter;
  }
  if (token !== ttsState.token) return;
  if (lastUtter) {
    // Browser voices run asynchronously; keep Stop armed until the last one ends.
    const finish = () => { if (token === ttsState.token) setReading(false); };
    lastUtter.addEventListener('end', finish);
    lastUtter.addEventListener('error', finish);
  } else {
    setReading(false);
  }
}


/* ---------- Interactive page view ---------- */
function applyView() {
  const interactive = state.view === 'interactive' && state.enrich.enabled;
  el.viewScan.classList.toggle('current', !interactive);
  el.viewInteractive.classList.toggle('current', interactive);
  el.frame.hidden = interactive;
  el.textBox.hidden = interactive;
  el.interactive.hidden = !interactive;
  el.zoomCtl.hidden = interactive;
  if (interactive && state.current) loadInteractive(state.current);
}

el.viewScan.addEventListener('click', () => { state.view = 'scan'; applyView(); });
el.viewInteractive.addEventListener('click', () => { state.view = 'interactive'; applyView(); });

async function loadInteractive(number) {
  const token = ++state.viewLoad;
  el.interactive.innerHTML = '';
  el.interactive.appendChild(ixNote('Reorganizing this page with AI…'));
  try {
    const res = await fetch(API.interactive(number));
    const data = await res.json().catch(() => ({}));
    if (token !== state.viewLoad || state.view !== 'interactive' || state.current !== number) return;
    el.interactive.innerHTML = '';
    if (!res.ok) throw new Error(data.detail || 'Could not build the interactive page.');
    const sentences = data.sentences || [];
    if (!sentences.length) { el.interactive.appendChild(ixNote('No Korean sentences were detected on this page.')); return; }
    for (const s of sentences) el.interactive.appendChild(sentenceBlock(s));
  } catch (err) {
    if (token !== state.viewLoad) return;
    el.interactive.innerHTML = '';
    el.interactive.appendChild(ixNote(err.message || 'Could not load the interactive page.'));
  }
}

function ixNote(text) {
  const p = document.createElement('p');
  p.className = 'ix-note';
  p.textContent = text;
  return p;
}

function sentenceBlock(s) {
  const wrap = document.createElement('div');
  wrap.className = 'sentence';
  const line = document.createElement('div');
  line.className = 'sentence-ko';
  const ko = document.createElement('span');
  ko.textContent = s.ko;
  line.appendChild(ko);
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'speak-btn';
  btn.title = 'Listen to this sentence';
  btn.setAttribute('aria-label', 'Listen to this sentence');
  btn.textContent = '🔊';
  btn.addEventListener('click', () => speakSentence(s.ko));
  line.appendChild(btn);
  wrap.appendChild(line);
  if (s.rom) { const rom = document.createElement('div'); rom.className = 'sentence-rom'; rom.textContent = s.rom; wrap.appendChild(rom); }
  if (s.tr) { const tr = document.createElement('div'); tr.className = 'sentence-tr'; tr.textContent = s.tr; wrap.appendChild(tr); }
  return wrap;
}

async function speakSentence(text) {
  stopSpeaking();
  const token = ttsState.token;
  setReading(true);
  try {
    await speakViaServer(text, 'ko-KR');
  } catch (_) {
    if (token === ttsState.token) speakLocal(text, 'ko-KR');
  }
  if (token === ttsState.token) setReading(false);
}

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

/* ---------- Book zoom (Live voice split view) ---------- */
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];
const ZOOM_FIT = ZOOM_STEPS.indexOf(1);
let zoomIdx = ZOOM_FIT;

// 100% is the CSS whole-page fit; other levels size the image in px relative to that fit.
function applyZoom() {
  const z = ZOOM_STEPS[zoomIdx];
  el.zoomReset.textContent = `${Math.round(z * 100)}%`;
  el.zoomOut.disabled = zoomIdx === 0;
  el.zoomIn.disabled = zoomIdx === ZOOM_STEPS.length - 1;
  const img = el.image;
  const zoomed = z !== 1 && app.classList.contains('live-active') && img.naturalWidth > 0;
  if (!zoomed) { el.stage.classList.remove('zoomed'); img.style.width = ''; return; }
  if (!el.stage.offsetWidth) return;
  // offset* includes scrollbars, so each zoom step stays an exact multiple of the fit size.
  const cs = getComputedStyle(el.stage);
  const availW = el.stage.offsetWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 2;
  const availH = el.stage.offsetHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 2;
  const fit = Math.min(1, availW / img.naturalWidth, availH / img.naturalHeight);
  el.stage.classList.add('zoomed');
  img.style.width = `${Math.round(img.naturalWidth * fit * z)}px`;
}

function setZoom(idx) {
  const st = el.stage;
  const cx = (st.scrollLeft + st.clientWidth / 2) / st.scrollWidth;
  const cy = (st.scrollTop + st.clientHeight / 2) / st.scrollHeight;
  zoomIdx = Math.max(0, Math.min(ZOOM_STEPS.length - 1, idx));
  applyZoom();
  st.scrollLeft = cx * st.scrollWidth - st.clientWidth / 2;
  st.scrollTop = cy * st.scrollHeight - st.clientHeight / 2;
}

el.zoomIn.addEventListener('click', () => setZoom(zoomIdx + 1));
el.zoomOut.addEventListener('click', () => setZoom(zoomIdx - 1));
el.zoomReset.addEventListener('click', () => setZoom(ZOOM_FIT));
el.image.addEventListener('load', applyZoom);
window.addEventListener('resize', applyZoom);

/* ---------- Live voice mode (Gemini Live via server proxy) ---------- */
const b64ToInt16 = (b64) => { const bin = atob(b64); const bytes = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); return new Int16Array(bytes.buffer); };
const int16ToB64 = (buf) => { const bytes = new Uint8Array(buf); let s = ''; for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]); return btoa(s); };

const live = { active: false, timer: null, ws: null, capCtx: null, playCtx: null, stream: null, playNode: null, lastRole: null };

function vStatus(text, cls) { el.voiceStatus.textContent = text; el.voice.className = 'voice-overlay' + (cls ? ' ' + cls : ''); }
function vLine(role, text) {
  if (role === live.lastRole && el.voiceTranscript.lastChild) { el.voiceTranscript.lastChild.textContent += text; }
  else { const d = document.createElement('div'); d.className = 'vt ' + (role === 'you' ? 'you' : 'tutor'); d.textContent = text; el.voiceTranscript.appendChild(d); live.lastRole = role; }
  el.voiceTranscript.scrollTop = el.voiceTranscript.scrollHeight;
}

async function startLive() {
  if (live.active || !state.tutor.enabled) return;
  if (state.billing && !state.billing.unlimited && state.billing.seconds <= 0) { openPaywall(true); return; }
  live.active = true; live.lastRole = null;
  el.voice.hidden = false; el.voiceTranscript.innerHTML = ''; vStatus('Connecting…');
  document.getElementById('app').classList.add('live-active');
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
      else if (m.type === 'credit') startTimer(m);
      else if (m.type === 'no_credit') { stopLive(); setBalance(0); openPaywall(true); }
      else if (m.type === 'audio') { const pcm = b64ToInt16(m.data); const f = new Float32Array(pcm.length); for (let i = 0; i < pcm.length; i++) f[i] = pcm[i] / 32768; live.playNode.port.postMessage(f); }
      else if (m.type === 'interrupted') live.playNode.port.postMessage('clear');
      else if (m.type === 'user_text') vLine('you', m.text);
      else if (m.type === 'tutor_text') vLine('tutor', m.text);
      else if (m.type === 'turn_end') live.lastRole = null;
      else if (m.type === 'error') { vStatus(m.detail || 'The tutor is unavailable.'); setTimeout(stopLive, 2500); }
      else if (m.type === 'end') {
        stopLive();
        if (m.balance != null) setBalance(m.balance);
        if (m.reason === 'credit') openPaywall(true);
        else if (m.reason === 'session') addNote('The voice session reached its time limit. Tap “Talk with tutor” to continue.');
      }
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
  clearInterval(live.timer); live.timer = null; el.voiceTimer.hidden = true;
  el.voice.hidden = true;
  document.getElementById('app').classList.remove('live-active');
  zoomIdx = ZOOM_FIT; applyZoom();
  el.liveBtn.classList.remove('active');
  try { live.ws && live.ws.close(); } catch (_) {}
  try { live.stream && live.stream.getTracks().forEach((t) => t.stop()); } catch (_) {}
  try { live.capCtx && live.capCtx.close(); } catch (_) {}
  try { live.playCtx && live.playCtx.close(); } catch (_) {}
  live.ws = live.stream = live.capCtx = live.playCtx = live.playNode = null;
}

/* ---------- Live minutes: countdown + PayLink top-up ---------- */
const fmtClock = (sec) => `${Math.floor(sec / 60)}:${String(Math.max(0, sec) % 60).padStart(2, '0')}`;
const fmtMnt = (n) => '₮' + n.toLocaleString('en-US');

function setBalance(seconds) {
  if (!state.billing) return;
  state.billing.seconds = Math.max(0, seconds);
  renderLiveLabel();
}

function renderLiveLabel() {
  const b = state.billing;
  const extra = !b || b.unlimited ? '' : b.seconds > 0 ? ` (${Math.ceil(b.seconds / 60)} min left)` : ' (top up)';
  el.liveBtn.textContent = '🎙️ Talk with tutor — Live voice';
  if (extra) { const span = document.createElement('span'); span.className = 'live-credit'; span.textContent = extra; el.liveBtn.appendChild(span); }
}

async function loadBilling() {
  try { const res = await fetch('/api/billing'); if (res.ok) { state.billing = await res.json(); renderLiveLabel(); } } catch (_) {}
}

// Counts the student's whole remaining balance down; the server enforces the same limit.
function startTimer(m) {
  clearInterval(live.timer);
  if (m.unlimited) { el.voiceTimer.hidden = true; return; }
  const endsAt = Date.now() + m.balance * 1000;
  const tick = () => {
    const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
    el.voiceTimer.hidden = false;
    el.voiceTimer.textContent = `⏱ ${fmtClock(left)} left`;
    el.voiceTimer.classList.toggle('low', left <= 60);
    setBalance(left);
    if (left <= 0) { stopLive(); openPaywall(true); }
  };
  tick();
  live.timer = setInterval(tick, 1000);
}

const pay = { invid: null, poll: null };

function openPaywall(outOfMinutes) {
  const b = state.billing || { packages: [], paylink: false, free_minutes: 10 };
  el.paywallTitle.textContent = outOfMinutes ? 'Your Live voice minutes are used up' : 'Buy Live voice minutes';
  el.paywallSub.textContent = outOfMinutes
    ? `You have used your ${b.free_minutes} free minutes. Top up to keep talking with the tutor — minutes never expire.`
    : 'Top up to keep talking with the tutor — minutes never expire.';
  el.paywallPackages.innerHTML = '';
  for (const p of b.packages) {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'paywall-pkg'; btn.disabled = !b.paylink;
    const left = document.createElement('span');
    const name = document.createElement('strong'); name.textContent = `${p.minutes} minutes`;
    const per = document.createElement('div'); per.className = 'per'; per.textContent = `${fmtMnt(Math.round(p.price / p.minutes))} / min`;
    left.append(name, per);
    const price = document.createElement('strong'); price.textContent = fmtMnt(p.price);
    btn.append(left, price);
    btn.addEventListener('click', () => checkout(p));
    el.paywallPackages.appendChild(btn);
  }
  el.paywallPay.hidden = true;
  el.paywallStatus.textContent = b.paylink ? 'You will pay on the secure PayLink page (QPay, bank apps, cards).' : 'Online payment is not switched on yet — please contact the administrator to add minutes.';
  el.paywall.hidden = false;
}

function closePaywall() { el.paywall.hidden = true; clearInterval(pay.poll); pay.poll = null; }

async function checkout(p) {
  el.paywallStatus.textContent = 'Creating your invoice…';
  el.paywallPackages.querySelectorAll('button').forEach((b) => { b.disabled = true; });
  try {
    const res = await fetch('/api/billing/checkout', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf }, body: JSON.stringify({ package: p.id }) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || 'Could not create the invoice.');
    pay.invid = data.invid;
    el.paywallPay.href = data.payment_link; el.paywallPay.hidden = false;
    el.paywallPay.textContent = `Pay ${fmtMnt(p.price)} on PayLink ↗`;
    el.paywallStatus.textContent = 'Waiting for payment… this window updates automatically once you have paid.';
    clearInterval(pay.poll);
    const started = Date.now();
    pay.poll = setInterval(() => checkInvoice(started), 4000);
  } catch (err) {
    el.paywallStatus.textContent = err.message;
    el.paywallPackages.querySelectorAll('button').forEach((b) => { b.disabled = false; });
  }
}

async function checkInvoice(started) {
  if (Date.now() - started > 20 * 60 * 1000) { clearInterval(pay.poll); el.paywallStatus.textContent = 'Still waiting. If you paid, your minutes will appear shortly — refresh the page.'; return; }
  try {
    const res = await fetch(`/api/billing/invoices/${encodeURIComponent(pay.invid)}/check`, { method: 'POST', headers: { 'X-CSRF-Token': state.csrf } });
    if (!res.ok) return;
    const data = await res.json();
    if (data.status === 'paid') {
      clearInterval(pay.poll); pay.poll = null;
      state.billing = data; renderLiveLabel();
      el.paywallPay.hidden = true;
      el.paywallPackages.innerHTML = '';
      el.paywallTitle.textContent = 'Payment received — thank you!';
      el.paywallSub.textContent = `You now have ${Math.floor(data.seconds / 60)} minutes of Live voice.`;
      const go = document.createElement('button');
      go.type = 'button'; go.className = 'paywall-pay'; go.textContent = '🎙️ Continue talking';
      go.addEventListener('click', () => { closePaywall(); startLive(); });
      el.paywallPackages.appendChild(go);
      el.paywallStatus.textContent = '';
    } else if (data.status === 'canceled' || data.status === 'cancelled' || data.status === 'expired') {
      clearInterval(pay.poll); pay.poll = null;
      el.paywallStatus.textContent = `The invoice was ${data.status}. Choose a package to try again.`;
      el.paywallPay.hidden = true;
      el.paywallPackages.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    }
  } catch (_) {}
}

el.paywallClose.addEventListener('click', closePaywall);
el.liveBtn.addEventListener('click', () => { if (live.active) stopLive(); else startLive(); });
el.voiceEnd.addEventListener('click', stopLive);

boot();

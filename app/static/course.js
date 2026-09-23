'use strict';
// Book2Course reader: renders a converted book as a course and drives the AI tutor.
const app = document.getElementById('app');
const jobId = decodeURIComponent(location.pathname.replace(/^\/course\//, '').replace(/\/$/, ''));

const state = { csrf: '', tutor: { enabled: false }, pages: [], title: '', current: 1, history: [], busy: false };

const el = {
  title: document.getElementById('book-title'), sub: document.getElementById('book-sub'),
  tocList: document.getElementById('toc-list'), pageLabel: document.getElementById('page-label'),
  image: document.getElementById('page-image'), text: document.getElementById('page-text'),
  flag: document.getElementById('page-flag'), textBox: document.getElementById('page-text-box'),
  prev: document.getElementById('prev'), next: document.getElementById('next'),
  chat: document.getElementById('chat'), composer: document.getElementById('composer'),
  question: document.getElementById('question'), send: document.getElementById('send'),
  mic: document.getElementById('mic'), speak: document.getElementById('speak'),
  micLang: document.getElementById('mic-lang'), status: document.getElementById('tutor-status'),
  tutorPanel: document.getElementById('tutor'), tutorToggle: document.getElementById('tutor-toggle'),
};

async function boot() {
  try {
    const session = await fetch('/api/session').then((r) => r.json());
    state.csrf = session.csrf;
    state.tutor = (session.tutor) || { enabled: false };
    const manifest = await fetch(`/books/${jobId}/manifest.json`).then((r) => {
      if (!r.ok) throw new Error(r.status === 404 ? 'This course was not found, or its 24-hour access has expired.' : 'Could not load this course.');
      return r.json();
    });
    state.title = manifest.title || 'Course';
    state.pages = manifest.pages || [];
    if (!state.pages.length) throw new Error('This course has no pages.');
    el.title.textContent = state.title;
    el.sub.textContent = `${state.pages.length} pages${manifest.review_required_pages ? ` · ${manifest.review_required_pages} need review` : ''}`;
    document.title = `${state.title} · Book2Course`;
    buildToc();
    setupTutor();
    const fromHash = parseInt(location.hash.replace('#page-', ''), 10);
    show(Number.isInteger(fromHash) ? fromHash : 1);
    app.classList.remove('loading');
  } catch (err) {
    app.classList.remove('loading');
    el.title.textContent = 'Unavailable';
    el.sub.textContent = err.message;
    el.chat.innerHTML = '';
    addNote(err.message);
  }
}

function buildToc() {
  const frag = document.createDocumentFragment();
  for (const page of state.pages) {
    const b = document.createElement('button');
    b.className = 'toc-item';
    b.dataset.page = page.number;
    const label = document.createElement('span');
    label.textContent = `Page ${page.number}`;
    b.appendChild(label);
    if (page.needs_review) {
      const rev = document.createElement('span');
      rev.className = 'rev';
      rev.textContent = 'REVIEW';
      b.appendChild(rev);
    }
    b.addEventListener('click', () => show(page.number));
    frag.appendChild(b);
  }
  el.tocList.appendChild(frag);
}

function show(number) {
  const page = state.pages.find((p) => p.number === number);
  if (!page) return;
  state.current = number;
  location.hash = `page-${number}`;
  el.pageLabel.textContent = `Page ${number} of ${state.pages.length}`;
  el.image.src = `/books/${jobId}/page-${number}.png`;
  el.image.alt = `Page ${number}`;
  const text = (page.text || '').trim();
  el.text.textContent = text || 'No text was detected on this page.';
  el.flag.textContent = page.text_source === 'ocr' ? 'Machine-recognized (OCR) text — may contain errors.'
    : (page.needs_ocr ? 'No embedded text on this page.' : '');
  el.prev.disabled = number <= 1;
  el.next.disabled = number >= state.pages.length;
  for (const item of el.tocList.children) item.classList.toggle('current', Number(item.dataset.page) === number);
  const cur = el.tocList.querySelector('.toc-item.current');
  if (cur) cur.scrollIntoView({ block: 'nearest' });
  document.querySelector('.stage').scrollTop = 0;
}

/* ---------- Tutor ---------- */
function setupTutor() {
  if (state.tutor.enabled) {
    el.status.textContent = 'Online';
    el.status.className = 'tutor-status on';
    addBot(`Сайн байна уу! 👋 I'm your Korean tutor. Open any page and ask me about the words, grammar, or how to say something. You can type or tap 🎤 to speak.`);
  } else {
    el.status.textContent = 'Offline';
    el.status.className = 'tutor-status off';
    addNote('The AI tutor is not switched on for this server. You can still read every page of the course.');
    el.question.disabled = true;
    el.send.disabled = true;
    el.mic.disabled = true;
  }
  setupMic();
}

el.composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  const question = el.question.value.trim();
  if (!question || state.busy || !state.tutor.enabled) return;
  addUser(question);
  el.question.value = '';
  el.question.style.height = 'auto';
  state.busy = true;
  el.send.disabled = true;
  const typing = addTyping();
  try {
    const res = await fetch(`/api/tutor/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf },
      body: JSON.stringify({ question, page: state.current, history: state.history.slice(-10) }),
    });
    const data = await res.json().catch(() => ({}));
    typing.remove();
    if (!res.ok) { addErr(data.detail || 'The tutor could not respond. Please try again.'); return; }
    addBot(data.reply);
    state.history.push({ role: 'user', content: question }, { role: 'assistant', content: data.reply });
    if (el.speak.checked) speak(data.reply);
  } catch (err) {
    typing.remove();
    addErr('Network error reaching the tutor. Please try again.');
  } finally {
    state.busy = false;
    el.send.disabled = false;
    el.question.focus();
  }
});

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
function addTyping() { const d = bubble('bot typing', 'Tutor is thinking…'); return d; }

/* ---------- Voice ---------- */
function setupMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition || !state.tutor.enabled) { el.mic.disabled = true; el.mic.title = 'Voice input is not available in this browser'; return; }
  let recognizer = null;
  let recording = false;
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
      if (said) {
        el.question.value = (el.question.value ? el.question.value + ' ' : '') + said;
        el.question.dispatchEvent(new Event('input'));
        el.question.focus();
      }
    };
    try { recognizer.start(); } catch (_) { /* already started */ }
  });
}

function speak(text) {
  if (!('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  // Replies mix Mongolian and Korean; a Korean voice reads the 한글 examples best.
  utter.lang = 'ko-KR';
  const voice = window.speechSynthesis.getVoices().find((v) => v.lang && v.lang.startsWith('ko'));
  if (voice) utter.voice = voice;
  window.speechSynthesis.speak(utter);
}

/* ---------- Navigation & layout ---------- */
el.prev.addEventListener('click', () => show(state.current - 1));
el.next.addEventListener('click', () => show(state.current + 1));
document.addEventListener('keydown', (event) => {
  if (document.activeElement === el.question) return;
  if (event.key === 'ArrowLeft') show(state.current - 1);
  if (event.key === 'ArrowRight') show(state.current + 1);
});
el.tutorToggle.addEventListener('click', () => {
  const hidden = el.tutorPanel.hasAttribute('hidden');
  el.tutorPanel.toggleAttribute('hidden', !hidden);
  app.classList.toggle('no-tutor', !hidden);
  el.tutorToggle.setAttribute('aria-expanded', String(hidden));
});

boot();

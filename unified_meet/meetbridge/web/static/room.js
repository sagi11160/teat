import {
  api, ApiError, applyFieldErrors, copyText, el, errorText, EVENT_KINDS, formatTime, LINK_STATES,
  PROVIDERS, providerChip, showAlert, STATE_DETAILS, withBusy,
} from './common.js';

const $ = (id) => document.getElementById(id);
const roomId = decodeURIComponent(window.location.pathname.split('/').filter(Boolean).pop() || '');
let room = null;
let oldestEventId = null;
let stream = null;

function goHome() { window.location.href = '/'; }

function handleAuthError(err) {
  if (err instanceof ApiError && err.status === 401) { goHome(); return true; }
  if (err instanceof ApiError && err.status === 404 && err.code === 'room_not_found') {
    $('room').hidden = true;
    $('loading').hidden = true;
    showAlert($('page-alert'), 'error', errorText(err));
    return true;
  }
  return false;
}

function stateDetailText(link) {
  const d = link.state_detail;
  if (!d) {
    if (link.state === 'waiting_admission') return 'אשרו את כניסת המשתתף-גשר מהלובי של הפגישה.';
    return '';
  }
  let text = STATE_DETAILS[d] || d;
  if (link.state === 'retrying' && link.retry_at) text += ` ניסיון נוסף ב-${formatTime(link.retry_at)}.`;
  if (link.state === 'failed' && link.attempts > 0 && !STATE_DETAILS[d]) text = `נכשל: ${d}`;
  return text;
}

function linkItem(link) {
  const running = link.desired_state === 'running';
  const toggle = el('button', { class: running ? 'secondary' : '', 'data-action': running ? 'stop' : 'start' },
    running ? 'עצירת הגשר' : 'הפעלת הגשר');
  toggle.addEventListener('click', () => withBusy(toggle, () => linkAction(link, running ? 'stop' : 'start')));
  const remove = el('button', { class: 'ghost', 'aria-label': `הסרת הפגישה ${PROVIDERS[link.provider] || ''}` }, 'הסרה');
  remove.addEventListener('click', () => withBusy(remove, () => removeLink(link)));
  const detail = stateDetailText(link);
  return el('li', { class: 'link-item', id: `link-${link.id}`, 'data-state': link.state },
    el('div', { class: 'row between' },
      el('div', { class: 'row' }, providerChip(link.provider), link.label ? el('strong', {}, link.label) : null),
      el('span', { class: `state ${link.state}`, role: 'status' }, LINK_STATES[link.state] || link.state)),
    el('div', { class: 'url ltr' }, link.url),
    link.has_passcode ? el('div', { class: 'hint' }, 'קוד גישה שמור') : null,
    detail ? el('div', { class: `hint ${link.state === 'failed' ? 'field-error' : ''}` }, detail) : null,
    el('div', { class: 'row' }, toggle, remove));
}

function renderLinks() {
  $('links').replaceChildren(...room.links.map(linkItem));
  $('links-empty').hidden = room.links.length > 0;
}

function renderParticipants(list) {
  const items = list.map((p) => el('li', {},
    providerChip(p.source),
    el('bdi', {}, p.name),
    p.audio ? null : el('span', { class: 'hint' }, '(מושתק)')));
  $('participants').replaceChildren(...items);
  $('participants-empty').hidden = list.length > 0;
  $('people-count').textContent = String(list.length);
}

function describeEvent(e) {
  let text = EVENT_KINDS[e.kind] || e.kind;
  const d = e.detail || {};
  if (d.provider) text += ` · ${PROVIDERS[d.provider] || d.provider}`;
  if (e.kind === 'link_state') {
    const link = room && room.links.find((l) => l.id === e.link_id);
    const who = link ? (PROVIDERS[link.provider] || link.provider) : 'גשר';
    text = `${who}: ${LINK_STATES[d.state] || d.state}`;
    if (d.detail) text += ` — ${STATE_DETAILS[d.detail] || d.detail}`;
  }
  if (e.kind === 'room_updated' && d.title) text += ` · ${d.title}`;
  return text;
}

function eventItem(e) {
  return el('li', {}, el('time', { datetime: e.created_at }, formatTime(e.created_at)), el('span', {}, describeEvent(e)));
}

async function loadEvents(more = false) {
  const qs = more && oldestEventId ? `?limit=50&before=${oldestEventId}` : '?limit=50';
  try {
    const events = await api('GET', `/api/rooms/${roomId}/events${qs}`);
    const items = events.map(eventItem);
    if (more) $('events').append(...items); else $('events').replaceChildren(...items);
    if (events.length) oldestEventId = events[events.length - 1].id;
    $('more-events').hidden = events.length < 50;
    $('events-empty').hidden = $('events').children.length > 0;
  } catch (err) {
    if (!handleAuthError(err)) showAlert($('page-alert'), 'error', errorText(err));
  }
}

function renderRoom() {
  document.title = `${room.title} — MeetBridge`;
  $('room-title').textContent = room.title;
  $('invite-url').value = room.invite_url;
  $('open-call').href = room.call_url;
  const form = $('settings-form');
  if (!form.contains(document.activeElement)) {
    $('set-title').value = room.title;
    $('set-bot').value = room.bot_display_name;
  }
  renderLinks();
  renderParticipants(room.participants);
}

async function loadRoom() {
  try {
    room = await api('GET', `/api/rooms/${roomId}`);
    renderRoom();
    $('loading').hidden = true;
    $('room').hidden = false;
    return true;
  } catch (err) {
    if (!handleAuthError(err)) {
      $('loading').hidden = true;
      showAlert($('page-alert'), 'error', errorText(err));
    }
    return false;
  }
}

function upsertLink(link) {
  const i = room.links.findIndex((l) => l.id === link.id);
  if (i === -1) room.links.push(link); else room.links[i] = link;
  renderLinks();
}

async function linkAction(link, action) {
  try {
    upsertLink(await api('POST', `/api/rooms/${roomId}/links/${link.id}/${action}`));
    loadEvents();
  } catch (err) {
    if (!handleAuthError(err)) showAlert($('link-alert'), 'error', errorText(err));
  }
}

async function removeLink(link) {
  const name = PROVIDERS[link.provider] || link.provider;
  if (!window.confirm(`להסיר את פגישת ה-${name} מהחדר? אם הגשר פעיל הוא ינותק.`)) return;
  try {
    await api('DELETE', `/api/rooms/${roomId}/links/${link.id}`);
    room.links = room.links.filter((l) => l.id !== link.id);
    renderLinks();
    loadEvents();
  } catch (err) {
    if (!handleAuthError(err)) showAlert($('link-alert'), 'error', errorText(err));
  }
}

function connectStream() {
  if (stream) stream.close();
  stream = new EventSource(`/api/rooms/${roomId}/stream`);
  stream.onopen = () => { $('live-status').textContent = 'עדכונים חיים פעילים'; };
  stream.onerror = () => {
    $('live-status').textContent = 'החיבור לעדכונים חיים נותק — מתחבר מחדש…';
    if (stream.readyState === EventSource.CLOSED) setTimeout(async () => { if (await loadRoom()) connectStream(); }, 5000);
  };
  stream.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'participants') {
      room.participants = msg.participants;
      renderParticipants(msg.participants);
    } else if (msg.type === 'link' && room.links.some((l) => l.id === msg.link.id)) {
      upsertLink(msg.link);
      loadEvents();
    }
  };
}

$('copy-invite').addEventListener('click', () => copyText($('invite-url').value, $('copy-status')));
$('invite-url').addEventListener('focus', (ev) => ev.target.select());

$('rotate-invite').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
  if (!window.confirm('להחליף את קישור ההזמנה? הקישור הישן יפסיק לעבוד מיד (מי שכבר בשיחה יישאר).')) return;
  try {
    room = await api('POST', `/api/rooms/${roomId}/rotate-invite`);
    renderRoom();
    $('copy-status').textContent = 'נוצר קישור הזמנה חדש.';
    loadEvents();
  } catch (err) {
    if (!handleAuthError(err)) showAlert($('page-alert'), 'error', errorText(err));
  }
}));

$('link-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.currentTarget;
  await withBusy(form.querySelector('button[type=submit]'), async () => {
    const data = Object.fromEntries(new FormData(form).entries());
    if (!data.passcode) delete data.passcode;
    try {
      const link = await api('POST', `/api/rooms/${roomId}/links`, data);
      form.reset();
      applyFieldErrors(form, null);
      showAlert($('link-alert'), 'ok', `פגישת ${PROVIDERS[link.provider] || link.provider} חוברה. לחצו "הפעלת הגשר" כדי לחבר אותה לשיחה.`);
      upsertLink(link);
      loadEvents();
    } catch (err) {
      if (handleAuthError(err)) return;
      applyFieldErrors(form, err);
      if (err instanceof ApiError && err.status === 422 && !err.fields.length) {
        $('link-url').setAttribute('aria-invalid', 'true');
      }
      showAlert($('link-alert'), 'error', errorText(err));
      $('link-url').focus();
    }
  });
});

$('settings-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.currentTarget;
  await withBusy(form.querySelector('button[type=submit]'), async () => {
    try {
      room = await api('PATCH', `/api/rooms/${roomId}`, Object.fromEntries(new FormData(form).entries()));
      applyFieldErrors(form, null);
      document.activeElement.blur();
      renderRoom();
      showAlert($('settings-alert'), 'ok', 'נשמר.');
      loadEvents();
    } catch (err) {
      if (handleAuthError(err)) return;
      applyFieldErrors(form, err);
      showAlert($('settings-alert'), 'error', errorText(err));
    }
  });
});

$('refresh-events').addEventListener('click', () => loadEvents());
$('more-events').addEventListener('click', (ev) => withBusy(ev.currentTarget, () => loadEvents(true)));

$('delete-room').addEventListener('click', (ev) => withBusy(ev.currentTarget, async () => {
  if (!window.confirm(`למחוק לצמיתות את החדר "${room.title}"?`)) return;
  try {
    await api('DELETE', `/api/rooms/${roomId}`);
    goHome();
  } catch (err) {
    if (!handleAuthError(err)) showAlert($('page-alert'), 'error', errorText(err));
  }
}));

window.addEventListener('beforeunload', () => { if (stream) stream.close(); });

(async () => {
  if (await loadRoom()) {
    connectStream();
    loadEvents();
  }
})();

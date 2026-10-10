import { api, ApiError, applyFieldErrors, el, errorText, providerChip, showAlert, withBusy } from './common.js';

const $ = (id) => document.getElementById(id);

function selectTab(which) {
  const login = which === 'login';
  $('tab-login').setAttribute('aria-selected', String(login));
  $('tab-register').setAttribute('aria-selected', String(!login));
  $('tab-login').tabIndex = login ? 0 : -1;
  $('tab-register').tabIndex = login ? -1 : 0;
  $('login-form').hidden = !login;
  $('register-form').hidden = login;
  showAlert($('auth-alert'), 'error', '');
  (login ? $('login-email') : $('reg-name')).focus();
}

function showAuth() {
  $('loading').hidden = true;
  $('dashboard').hidden = true;
  $('who').hidden = true;
  $('logout').hidden = true;
  $('auth').hidden = false;
}

async function showDashboard(user) {
  $('loading').hidden = true;
  $('auth').hidden = true;
  $('dashboard').hidden = false;
  $('who').hidden = false;
  $('who').textContent = `מחובר/ת: ${user.display_name}`;
  $('logout').hidden = false;
  await loadRooms();
}

function roomSummary(room) {
  const live = room.links.filter((l) => l.state === 'live').length;
  const chips = room.links.map((l) => providerChip(l.provider));
  const status = room.links.length
    ? `${live} מתוך ${room.links.length} פגישות מגושרות כעת`
    : 'עדיין לא חוברו פגישות';
  return el('li', {},
    el('a', { href: `/rooms/${room.id}` },
      el('div', {},
        el('div', { class: 'title' }, room.title),
        el('div', { class: 'hint' }, status, room.participants.length ? ` · ${room.participants.length} בחדר` : '')),
      el('div', { class: 'row' }, chips)));
}

async function loadRooms() {
  $('rooms-loading').hidden = false;
  showAlert($('rooms-alert'), 'error', '');
  try {
    const rooms = await api('GET', '/api/rooms');
    $('rooms').replaceChildren(...rooms.map(roomSummary));
    $('rooms-empty').hidden = rooms.length > 0;
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return showAuth();
    showAlert($('rooms-alert'), 'error', errorText(err));
  } finally {
    $('rooms-loading').hidden = true;
  }
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

$('tab-login').addEventListener('click', () => selectTab('login'));
$('tab-register').addEventListener('click', () => selectTab('register'));
document.querySelector('[role=tablist]').addEventListener('keydown', (ev) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(ev.key)) return;
  ev.preventDefault();
  const next = $('tab-login').getAttribute('aria-selected') === 'true' ? 'register' : 'login';
  const target = ev.key === 'Home' ? 'login' : ev.key === 'End' ? 'register' : next;
  selectTab(target);
  $(`tab-${target}`).focus();
});

$('login-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.currentTarget;
  await withBusy(form.querySelector('button[type=submit]'), async () => {
    try {
      const user = await api('POST', '/api/auth/login', formData(form));
      form.reset();
      await showDashboard(user);
    } catch (err) {
      applyFieldErrors(form, err);
      showAlert($('auth-alert'), 'error', errorText(err));
    }
  });
});

$('register-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.currentTarget;
  await withBusy(form.querySelector('button[type=submit]'), async () => {
    try {
      const user = await api('POST', '/api/auth/register', formData(form));
      form.reset();
      await showDashboard(user);
    } catch (err) {
      applyFieldErrors(form, err);
      showAlert($('auth-alert'), 'error', errorText(err));
    }
  });
});

$('create-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const form = ev.currentTarget;
  await withBusy(form.querySelector('button[type=submit]'), async () => {
    try {
      const room = await api('POST', '/api/rooms', formData(form));
      window.location.href = `/rooms/${room.id}`;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) return showAuth();
      applyFieldErrors(form, err);
      showAlert($('create-alert'), 'error', errorText(err));
    }
  });
});

$('logout').addEventListener('click', async (ev) => {
  await withBusy(ev.currentTarget, async () => {
    try { await api('POST', '/api/auth/logout'); } catch (e) { /* session already gone */ }
    $('rooms').replaceChildren();
    showAuth();
    selectTab('login');
  });
});

(async () => {
  try {
    const user = await api('GET', '/api/auth/me');
    await showDashboard(user);
  } catch (err) {
    showAuth();
    if (!(err instanceof ApiError) || err.status !== 401) showAlert($('auth-alert'), 'error', errorText(err));
  }
})();

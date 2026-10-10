import { api, el, errorText, showAlert } from './common.js';

const $ = (id) => document.getElementById(id);
const token = decodeURIComponent(window.location.pathname.split('/').filter(Boolean).pop() || '');

function option(o) {
  const children = [
    el('span', { class: 'name' }, o.provider_name),
    o.label ? el('span', {}, o.label) : null,
    el('span', { class: 'hint' }, o.bridged ? 'מחובר לשיחה המשותפת' : 'הגשר לפגישה הזו כבוי כרגע'),
  ];
  if (o.passcode) children.push(el('span', { class: 'hint' }, 'קוד גישה: ', el('bdi', { class: 'ltr' }, o.passcode)));
  return el('a', {
    class: `join-option ${o.provider}`, href: o.join_url, target: '_blank', rel: 'noopener noreferrer',
    'aria-label': `הצטרפות דרך ${o.provider_name}${o.label ? ` (${o.label})` : ''}`,
  }, ...children);
}

(async () => {
  try {
    const info = await api('GET', `/api/invite/${encodeURIComponent(token)}`);
    document.title = `${info.title} — הזמנה לשיחה`;
    $('title').textContent = info.title;
    $('participants').textContent = info.participants ? `${info.participants} משתתפים בשיחה כרגע` : 'עדיין אין אף אחד בשיחה.';
    const web = el('a', { class: 'join-option web', href: info.call_url, 'aria-label': 'הצטרפות ישירות מהדפדפן' },
      el('span', { class: 'name' }, 'מהדפדפן'),
      el('span', {}, 'בלי להתקין כלום'),
      el('span', { class: 'hint' }, 'מתאים למחשב ולטלפון'));
    $('options').replaceChildren(...info.options.map(option), web);
    $('no-bridge').hidden = info.options.every((o) => o.bridged);
    $('invite').hidden = false;
  } catch (err) {
    showAlert($('page-alert'), 'error', errorText(err));
  } finally {
    $('loading').hidden = true;
  }
})();

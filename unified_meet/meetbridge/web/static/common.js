// Shared helpers: API client, Hebrew messages, small DOM utilities.
export const PROVIDERS = {
  zoom: 'Zoom',
  google_meet: 'Google Meet',
  teams: 'Microsoft Teams',
  mock: 'MockMeet (בדיקות)',
  web: 'דפדפן',
};

const ERRORS = {
  network: 'אין חיבור לשרת. בדקו את החיבור לאינטרנט ונסו שוב.',
  not_authenticated: 'יש להתחבר כדי להמשיך.',
  invalid_credentials: 'האימייל או הסיסמה שגויים.',
  email_taken: 'כבר קיים חשבון עם האימייל הזה. אפשר להתחבר במקום להירשם.',
  too_many_attempts: 'יותר מדי ניסיונות כושלים. נסו שוב בעוד כמה דקות.',
  registration_closed: 'ההרשמה סגורה בשרת הזה. פנו למנהל המערכת.',
  validation_error: 'חלק מהשדות אינם תקינים.',
  csrf_header_missing: 'הבקשה נחסמה מטעמי אבטחה. רעננו את הדף ונסו שוב.',
  room_not_found: 'החדר לא נמצא, או שאין לכם הרשאה אליו.',
  link_not_found: 'הקישור לפגישה לא נמצא.',
  too_many_links: 'הגעתם למספר הפגישות המרבי שאפשר לחבר לחדר אחד.',
  link_exists: 'הפגישה הזו כבר מחוברת לחדר.',
  title_required: 'יש לתת שם לחדר.',
  bot_name_required: 'יש לתת שם תצוגה לגשר.',
  empty_url: 'יש להדביק קישור לפגישה.',
  url_too_long: 'הקישור ארוך מדי.',
  unsupported_provider: 'אפשר לחבר רק קישורים של Zoom, Google Meet או Microsoft Teams.',
  zoom_personal_link: 'קישור אישי של Zoom (zoom.us/my/…) לא נתמך. העתיקו את קישור הפגישה המספרי (zoom.us/j/…).',
  zoom_no_meeting_id: 'בקישור ה-Zoom חסר מספר פגישה (zoom.us/j/123456789).',
  meet_no_code: 'קישור Google Meet צריך להיראות כך: meet.google.com/abc-defg-hij',
  teams_no_meeting: 'זה לא קישור הצטרפות לפגישת Teams. העתיקו את הקישור "Join the meeting".',
  teams_passcode_required: 'לקישור ה-Teams הזה חסר קוד הגישה (החלק ‎?p=…‎). הוסיפו אותו בשדה קוד הגישה.',
  mock_no_code: 'קישור MockMeet לא תקין.',
  invite_not_found: 'קישור ההזמנה אינו תקף (ייתכן שהמארגן החליף אותו).',
  name_required: 'יש להזין שם.',
  room_full: 'החדר מלא כרגע. נסו שוב מאוחר יותר.',
  internal_error: 'משהו השתבש אצלנו.',
};

export const LINK_STATES = {
  idle: 'לא מחובר',
  starting: 'מתחיל…',
  joining: 'מצטרף לפגישה…',
  waiting_admission: 'ממתין לאישור בלובי',
  live: 'מחובר ומגשר',
  retrying: 'ינסה שוב בקרוב',
  leaving: 'מתנתק…',
  stopped: 'מנותק',
  failed: 'נכשל',
};

export const STATE_DETAILS = {
  meeting_ended: 'הפגישה הסתיימה.',
  admission_denied: 'המארח דחה את הבקשה להצטרף, או הוציא את הגשר מהפגישה.',
  meeting_not_found: 'הפגישה לא נמצאה, או שקוד הגישה שגוי.',
  platform_blocked_bot: 'הפלטפורמה לא מאפשרת הצטרפות אורחים לפגישה הזו (נדרשת התחברות או אימות). שנו את הגדרות הפגישה כך שאורחים יוכלו להצטרף.',
  join_timeout: 'ההצטרפות לפגישה ארכה יותר מדי זמן.',
  admission_timeout: 'אף אחד לא אישר את כניסת הגשר מהלובי.',
  dropped_from_call: 'הגשר התנתק מהשיחה.',
  bot_crashed: 'אירעה תקלה פנימית בגשר.',
  browser_page_closed: 'דפדפן הגשר נסגר באופן לא צפוי.',
  resumed_after_restart: 'מתחבר מחדש אחרי הפעלה מחדש של השרת.',
  server_restart: 'נותק בגלל הפעלה מחדש של השרת.',
  waiting_for_capacity: 'ממתין למקום פנוי — כל הגשרים בשרת תפוסים כרגע.',
};

export const EVENT_KINDS = {
  room_created: 'החדר נוצר',
  room_updated: 'פרטי החדר עודכנו',
  invite_rotated: 'קישור ההזמנה הוחלף',
  link_added: 'פגישה חוברה לחדר',
  link_removed: 'פגישה הוסרה מהחדר',
  bridge_start_requested: 'התבקשה הפעלת גשר',
  bridge_stop_requested: 'התבקשה עצירת גשר',
  link_state: 'מצב גשר השתנה',
};

export class ApiError extends Error {
  constructor(status, code, message, fields, requestId) {
    super(message);
    this.status = status;
    this.code = code;
    this.fields = fields || [];
    this.requestId = requestId;
  }
}

export function errorText(err) {
  if (!(err instanceof ApiError)) return ERRORS.network;
  let text = ERRORS[err.code] || err.message || ERRORS.internal_error;
  if (err.code === 'internal_error' && err.requestId) text += ` (קוד תקלה: ${err.requestId})`;
  return text;
}

export async function api(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-MeetBridge': '1' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, 'network', ERRORS.network);
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const err = (data && data.error) || {};
    throw new ApiError(res.status, err.code || 'internal_error', err.message || '', err.fields, err.request_id);
  }
  return data;
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function providerChip(provider) {
  return el('span', { class: `chip ${provider}` }, PROVIDERS[provider] || provider);
}

export function formatTime(iso) {
  try {
    return new Intl.DateTimeFormat('he-IL', { dateStyle: 'short', timeStyle: 'medium' }).format(new Date(iso));
  } catch (e) { return iso; }
}

export async function withBusy(button, fn) {
  if (button.getAttribute('aria-busy') === 'true') return undefined;
  button.setAttribute('aria-busy', 'true');
  button.disabled = true;
  try { return await fn(); } finally {
    button.removeAttribute('aria-busy');
    button.disabled = false;
  }
}

export function showAlert(container, kind, text) {
  container.replaceChildren();
  if (!text) { container.hidden = true; return; }
  container.hidden = false;
  container.className = `alert ${kind}`;
  container.setAttribute('role', kind === 'error' ? 'alert' : 'status');
  container.textContent = text;
}

export function applyFieldErrors(form, err) {
  form.querySelectorAll('.field-error').forEach((n) => n.remove());
  form.querySelectorAll('[aria-invalid]').forEach((n) => n.removeAttribute('aria-invalid'));
  if (!(err instanceof ApiError)) return;
  for (const f of err.fields) {
    const input = form.querySelector(`[name="${CSS.escape(f.field)}"]`);
    if (!input) continue;
    input.setAttribute('aria-invalid', 'true');
    const id = `${input.id}-error`;
    const msg = el('div', { class: 'field-error', id }, fieldMessage(f));
    input.setAttribute('aria-describedby', id);
    input.after(msg);
  }
}

function fieldMessage(f) {
  const m = (f.message || '').toLowerCase();
  if (m.includes('at least 10')) return 'לפחות 10 תווים.';
  if (m.includes('email')) return 'כתובת אימייל לא תקינה.';
  if (m.includes('at least 1') || m.includes('required')) return 'שדה חובה.';
  if (m.includes('at most')) return 'הערך ארוך מדי.';
  return 'ערך לא תקין.';
}

export async function copyText(text, statusNode) {
  try {
    await navigator.clipboard.writeText(text);
    if (statusNode) statusNode.textContent = 'הועתק!';
  } catch (e) {
    if (statusNode) statusNode.textContent = 'לא ניתן להעתיק אוטומטית — סמנו והעתיקו ידנית.';
  }
  if (statusNode) setTimeout(() => { statusNode.textContent = ''; }, 2500);
}

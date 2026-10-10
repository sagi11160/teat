import { api, el, errorText, PROVIDERS, showAlert, withBusy, ApiError } from './common.js';

const $ = (id) => document.getElementById(id);
const token = decodeURIComponent(window.location.pathname.split('/').filter(Boolean).pop() || '');

const CLOSE_MESSAGES = {
  invite_not_found: 'קישור ההזמנה אינו תקף (ייתכן שהמארגן החליף אותו).',
  name_required: 'יש להזין שם.',
  room_full: 'החדר מלא כרגע. נסו שוב מאוחר יותר.',
  room_closed: 'המארגן סגר את החדר.',
};

let ws = null;
let selfId = null;
let iceServers = [];
let localStream = new MediaStream();
let leaving = false;
let reconnectDelay = 1000;
let pingTimer = null;
let displayName = '';
let mediaNote = '';
const peers = new Map(); // peerId -> {pc, info, tile, stream, initiator, pendingIce, analyser}
const audioCtx = window.AudioContext ? new AudioContext() : null;

window.__mbRemoteStreams = new Map();
window.__mbCall = () => ({
  selfId,
  connected: !!ws && ws.readyState === WebSocket.OPEN,
  peers: [...peers.entries()].map(([id, p]) => ({
    id, name: p.info.name, source: p.info.source, state: p.pc.connectionState,
    tracks: p.stream.getTracks().map((t) => t.kind),
  })),
});

// ------------------------------------------------------------------ media
async function acquireMedia(wantAudio, wantVideo) {
  if (!wantAudio && !wantVideo) return { stream: new MediaStream(), note: '' };
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return { stream: new MediaStream(), note: 'הדפדפן לא מאפשר גישה למיקרופון ולמצלמה (נדרש חיבור מאובטח). אתם מחוברים להאזנה וצפייה בלבד.' };
  }
  const video = wantVideo ? { width: { ideal: 1280 }, height: { ideal: 720 } } : false;
  try {
    return { stream: await navigator.mediaDevices.getUserMedia({ audio: wantAudio, video }), note: '' };
  } catch (e) {
    if (wantVideo && wantAudio) {
      try {
        return { stream: await navigator.mediaDevices.getUserMedia({ audio: true }), note: 'לא הצלחנו להפעיל את המצלמה — אתם מחוברים עם מיקרופון בלבד.' };
      } catch (e2) { /* fall through */ }
    }
    return { stream: new MediaStream(), note: 'אין הרשאה למיקרופון או למצלמה. אתם מחוברים להאזנה וצפייה בלבד.' };
  }
}

// ------------------------------------------------------------------ tiles
function initials(name) {
  return (name || '?').trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
}

function makeTile(id, name, source, isSelf) {
  const video = el('video', { autoplay: true, playsinline: true });
  video.muted = !!isSelf;
  const avatar = el('div', { class: 'avatar', 'aria-hidden': 'true' }, initials(name));
  const muted = el('span', { class: 'hint', hidden: true }, '(מושתק)');
  const label = el('div', { class: 'label' },
    el('span', { class: `chip ${source}` }, PROVIDERS[source] || source),
    el('bdi', {}, isSelf ? `${name} (את/ה)` : name), muted);
  const tile = el('div', { class: 'tile', id: `tile-${id}`, role: 'group', 'aria-label': name }, avatar, video, label);
  tile._video = video;
  tile._avatar = avatar;
  tile._muted = muted;
  $('stage').append(tile);
  return tile;
}

function refreshTile(tile, stream) {
  const hasVideo = stream.getVideoTracks().some((t) => t.readyState === 'live' && t.enabled);
  tile._video.hidden = !hasVideo;
  tile._avatar.hidden = hasVideo;
}

function updateCount() {
  const n = peers.size + 1;
  $('count').textContent = `${n} בשיחה`;
}

function status(text) { $('call-status').textContent = text; }

// ------------------------------------------------------------------ signalling
function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function closePeer(id) {
  const p = peers.get(id);
  if (!p) return;
  peers.delete(id);
  window.__mbRemoteStreams.delete(id);
  try { p.pc.close(); } catch (e) { /* already closed */ }
  if (p.tile) p.tile.remove();
  updateCount();
}

function addLocalTracks(pc) {
  const audio = localStream.getAudioTracks()[0];
  const video = localStream.getVideoTracks()[0];
  if (audio) pc.addTrack(audio, localStream); else pc.addTransceiver('audio', { direction: 'recvonly' });
  if (video) pc.addTrack(video, localStream); else pc.addTransceiver('video', { direction: 'recvonly' });
}

function watchSpeaking(peer) {
  if (!audioCtx || peer.analyser || !peer.stream.getAudioTracks().length) return;
  const src = audioCtx.createMediaStreamSource(peer.stream);
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  src.connect(analyser);
  peer.analyser = analyser;
}

function createPeer(info, initiator) {
  closePeer(info.id);
  const pc = new RTCPeerConnection({ iceServers });
  const stream = new MediaStream();
  const peer = { pc, info, stream, initiator, pendingIce: [], analyser: null, tile: makeTile(info.id, info.name, info.source, false) };
  peers.set(info.id, peer);
  window.__mbRemoteStreams.set(info.id, stream);
  peer.tile._video.srcObject = stream;
  peer.tile._muted.hidden = info.audio !== false;
  refreshTile(peer.tile, stream);
  addLocalTracks(pc);
  pc.onicecandidate = (ev) => { if (ev.candidate) send({ type: 'signal', to: info.id, data: { candidate: ev.candidate.toJSON() } }); };
  pc.ontrack = (ev) => {
    stream.addTrack(ev.track);
    ev.track.onunmute = () => refreshTile(peer.tile, stream);
    ev.track.onended = () => { stream.removeTrack(ev.track); refreshTile(peer.tile, stream); };
    peer.tile._video.play().catch(() => {});
    refreshTile(peer.tile, stream);
    watchSpeaking(peer);
  };
  pc.onconnectionstatechange = async () => {
    if (pc.connectionState === 'failed' && peer.initiator) {
      try {
        await pc.setLocalDescription(await pc.createOffer({ iceRestart: true }));
        send({ type: 'signal', to: info.id, data: { sdp: pc.localDescription.toJSON() } });
      } catch (e) { /* peer gone */ }
    }
  };
  if (initiator) {
    (async () => {
      await pc.setLocalDescription(await pc.createOffer());
      send({ type: 'signal', to: info.id, data: { sdp: pc.localDescription.toJSON() } });
    })().catch((e) => console.warn('offer failed', e));
  }
  updateCount();
  return peer;
}

async function onSignal(from, data) {
  const peer = peers.get(from);
  if (!peer) return;
  const pc = peer.pc;
  if (data.sdp) {
    await pc.setRemoteDescription(data.sdp);
    for (const c of peer.pendingIce.splice(0)) await pc.addIceCandidate(c).catch(() => {});
    if (data.sdp.type === 'offer') {
      await pc.setLocalDescription(await pc.createAnswer());
      send({ type: 'signal', to: from, data: { sdp: pc.localDescription.toJSON() } });
    }
  } else if (data.candidate) {
    if (pc.remoteDescription) await pc.addIceCandidate(data.candidate).catch(() => {});
    else peer.pendingIce.push(data.candidate);
  }
}

function onMessage(msg) {
  switch (msg.type) {
    case 'welcome':
      selfId = msg.peer_id;
      iceServers = msg.ice_servers || [];
      reconnectDelay = 1000;
      msg.peers.forEach((p) => createPeer(p, true));
      status([mediaNote, msg.peers.length ? '' : 'אתם הראשונים בשיחה. משתתפים מ-Zoom, Meet, Teams ומהדפדפן יופיעו כאן כשיצטרפו.'].filter(Boolean).join(' '));
      sendMediaState();
      break;
    case 'peer-joined':
      createPeer(msg.peer, false);
      status(mediaNote);
      break;
    case 'peer-left':
      closePeer(msg.peer_id);
      break;
    case 'signal':
      onSignal(msg.from, msg.data).catch((e) => console.warn('signal error', e));
      break;
    case 'media-state': {
      const p = peers.get(msg.peer_id);
      if (p) { p.info.audio = msg.audio; p.info.video = msg.video; p.tile._muted.hidden = msg.audio; }
      break;
    }
    case 'room-closed':
      finish('room_closed');
      break;
    case 'error':
      if (CLOSE_MESSAGES[msg.code]) finish(msg.code);
      break;
    default:
      break;
  }
}

function connect() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${window.location.host}/ws/call/${encodeURIComponent(token)}?name=${encodeURIComponent(displayName)}`);
  ws.onopen = () => {
    clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ type: 'ping' }), 25000);
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    onMessage(msg);
  };
  ws.onclose = () => {
    clearInterval(pingTimer);
    if (leaving) return;
    [...peers.keys()].forEach(closePeer);
    status('החיבור לשיחה נותק — מתחבר מחדש…');
    setTimeout(() => { if (!leaving) connect(); }, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 15000);
  };
}

function finish(reason) {
  leaving = true;
  clearInterval(pingTimer);
  if (ws && ws.readyState === WebSocket.OPEN) {
    send({ type: 'leave' });
    ws.close();
  }
  [...peers.keys()].forEach(closePeer);
  localStream.getTracks().forEach((t) => t.stop());
  $('incall').hidden = true;
  $('left').hidden = false;
  $('rejoin').href = window.location.pathname;
  if (reason && reason !== 'leave') {
    $('left-title').textContent = 'השיחה הסתיימה';
    $('left-text').textContent = CLOSE_MESSAGES[reason] || '';
  }
  $('rejoin').focus();
}

// ------------------------------------------------------------------ controls
function sendMediaState() {
  const a = localStream.getAudioTracks()[0];
  const v = localStream.getVideoTracks()[0];
  send({ type: 'media-state', audio: !!a && a.enabled, video: !!v && v.enabled });
}

function setupControls() {
  const mic = $('toggle-mic');
  const cam = $('toggle-cam');
  const a = localStream.getAudioTracks()[0];
  const v = localStream.getVideoTracks()[0];
  mic.disabled = !a;
  cam.disabled = !v;
  if (!a) mic.textContent = 'אין מיקרופון';
  if (!v) cam.textContent = 'אין מצלמה';
  mic.addEventListener('click', () => {
    a.enabled = !a.enabled;
    mic.setAttribute('aria-pressed', String(!a.enabled));
    mic.textContent = a.enabled ? 'השתקה' : 'ביטול השתקה';
    sendMediaState();
  });
  cam.addEventListener('click', () => {
    v.enabled = !v.enabled;
    cam.setAttribute('aria-pressed', String(!v.enabled));
    cam.textContent = v.enabled ? 'כיבוי מצלמה' : 'הפעלת מצלמה';
    refreshTile(selfTile, localStream);
    sendMediaState();
  });
  $('leave').addEventListener('click', () => finish('leave'));
}

let selfTile = null;

setInterval(() => {
  for (const p of peers.values()) {
    if (!p.analyser) continue;
    const buf = new Uint8Array(p.analyser.fftSize);
    p.analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128));
    p.tile.classList.toggle('speaking', peak > 12);
  }
}, 200);

// ------------------------------------------------------------------ start
const nameInput = $('name');
nameInput.value = localStorage.getItem('mb_name') || '';

(async () => {
  try {
    const info = await api('GET', `/api/invite/${encodeURIComponent(token)}`);
    $('room-title').textContent = info.title;
    document.title = `${info.title} — שיחה`;
  } catch (err) {
    showAlert($('prejoin-alert'), 'error', errorText(err));
    if (err instanceof ApiError && err.status === 404) $('prejoin-form').hidden = true;
  }
  nameInput.focus();
})();

$('prejoin-form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  displayName = nameInput.value.trim().replace(/\s+/g, ' ');
  if (!displayName) {
    nameInput.setAttribute('aria-invalid', 'true');
    showAlert($('prejoin-alert'), 'error', CLOSE_MESSAGES.name_required);
    nameInput.focus();
    return;
  }
  nameInput.removeAttribute('aria-invalid');
  localStorage.setItem('mb_name', displayName);
  await withBusy($('join-btn'), async () => {
    if (audioCtx) audioCtx.resume().catch(() => {});
    const { stream, note } = await acquireMedia($('want-audio').checked, $('want-video').checked);
    localStream = stream;
    mediaNote = note;
    $('prejoin').hidden = true;
    $('incall').hidden = false;
    selfTile = makeTile('self', displayName, 'web', true);
    selfTile._video.srcObject = localStream;
    refreshTile(selfTile, localStream);
    setupControls();
    status(note || 'מתחבר לשיחה…');
    updateCount();
    connect();
  });
});

window.addEventListener('pagehide', () => { if (!leaving) { leaving = true; send({ type: 'leave' }); } });

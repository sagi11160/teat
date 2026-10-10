/*
 * MeetBridge in-page bridge. Injected (via Playwright add_init_script) into the web
 * client of an external meeting platform before any of its own scripts run.
 *
 * Direction 1, room -> platform: getUserMedia() is overridden so the platform's
 * "microphone" is a WebAudio mix of every other room participant, and its "camera"
 * is a canvas grid of their videos.
 *
 * Direction 2, platform -> room: every remote track the platform receives (via its
 * RTCPeerConnections, or via WebAudio playback for clients that decode audio in
 * WASM) is mixed into one audio track + one composited video track, which this page
 * publishes into the MeetBridge room over its own, unhooked, RTCPeerConnections.
 *
 * Signalling goes through the Playwright binding window.__mbSend (page -> server)
 * and window.__mbHubReceive (server -> page), so the platform's CSP and mixed-content
 * rules never apply to it. A platform never receives its own audio back because the
 * room only forwards *other* participants to each bridge.
 */
(() => {
  if (window.__mbInstalled) return;
  window.__mbInstalled = true;
  const cfg = window.__mbConfig || {};

  let topBridge = null;
  try {
    if (window.top !== window) topBridge = () => window.top.__mbBridge;
  } catch (e) {
    return; // cross-origin frame: leave it untouched
  }

  const NativePC = window.RTCPeerConnection;
  const mediaDevices = navigator.mediaDevices;
  const nativeGUM = mediaDevices && mediaDevices.getUserMedia ? mediaDevices.getUserMedia.bind(mediaDevices) : null;

  // ------------------------------------------------------------------ hooks (all frames)
  const getBridge = () => (topBridge ? topBridge() : window.__mbBridge);

  if (NativePC) {
    const Hooked = function (...args) {
      const pc = Reflect.construct(NativePC, args, new.target || Hooked);
      pc.addEventListener('track', (ev) => {
        const b = getBridge();
        if (b) b.captureRemoteTrack(ev.track);
      });
      return pc;
    };
    Hooked.prototype = NativePC.prototype;
    Object.setPrototypeOf(Hooked, NativePC);
    window.RTCPeerConnection = Hooked;
    if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = Hooked;
  }

  if (mediaDevices && nativeGUM) {
    mediaDevices.getUserMedia = async function (constraints) {
      const b = getBridge();
      if (!b) return nativeGUM(constraints);
      return b.syntheticUserMedia(constraints || {});
    };
  }

  const nativeConnect = AudioNode.prototype.connect;
  if (cfg.captureWebAudio) {
    AudioNode.prototype.connect = function (dest, ...rest) {
      const result = nativeConnect.call(this, dest, ...rest);
      try {
        if (dest instanceof AudioDestinationNode) {
          const b = getBridge();
          if (b) b.tapWebAudio(this);
        }
      } catch (e) { /* never break the platform's audio graph */ }
      return result;
    };
  }

  if (topBridge) return; // the rest only runs in the top frame

  // ------------------------------------------------------------------ bridge (top frame)
  const W = 1280, H = 720, FPS = 15;
  const ctx = new AudioContext({ sampleRate: 48000 });
  const keepAlive = () => { if (ctx.state !== 'running') ctx.resume().catch(() => {}); };
  setInterval(keepAlive, 1000);
  document.addEventListener('click', keepAlive, true);

  const silence = ctx.createConstantSource();
  silence.offset.value = 0;
  silence.start();

  // room -> platform
  const toPlatform = ctx.createMediaStreamDestination();
  silence.connect(toPlatform);
  const toPlatformAnalyser = ctx.createAnalyser();
  // platform -> room
  const fromPlatform = ctx.createMediaStreamDestination();
  silence.connect(fromPlatform);
  const fromPlatformAnalyser = ctx.createAnalyser();
  const pcAudioBus = ctx.createGain();
  const webAudioBus = ctx.createGain();
  pcAudioBus.connect(fromPlatform);
  webAudioBus.connect(fromPlatform);
  pcAudioBus.connect(fromPlatformAnalyser);
  webAudioBus.connect(fromPlatformAnalyser);

  const makeCanvas = () => {
    const c = document.createElement('canvas');
    c.width = W; c.height = H;
    return c;
  };
  const outCanvas = makeCanvas();   // what the platform sees as our camera
  const inCanvas = makeCanvas();    // what the room sees from this platform
  const outTrack = outCanvas.captureStream(FPS).getVideoTracks()[0];
  const inTrack = inCanvas.captureStream(FPS).getVideoTracks()[0];
  const ownTrackIds = new Set([outTrack.id, inTrack.id, toPlatform.stream.getAudioTracks()[0].id,
    fromPlatform.stream.getAudioTracks()[0].id]);

  const hidden = document.createElement('div');
  hidden.setAttribute('aria-hidden', 'true');
  hidden.style.cssText = 'position:fixed;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none;left:-10px;top:-10px;';
  const attachHidden = () => { if (!hidden.isConnected && document.documentElement) document.documentElement.appendChild(hidden); };

  const mediaEl = (tag, track) => {
    const el = document.createElement(tag);
    el.muted = true; el.autoplay = true; el.playsInline = true;
    el.srcObject = new MediaStream([track]);
    attachHidden();
    hidden.appendChild(el);
    el.play().catch(() => {});
    return el;
  };

  // ---- platform -> room capture
  const platformVideos = new Map(); // trackId -> {track, el}
  const platformAudios = new Map(); // trackId -> {track, el, node}
  const webAudioTaps = new WeakMap();
  let webAudioTapCount = 0;

  const captureRemoteTrack = (track) => {
    if (!track || ownTrackIds.has(track.id)) return;
    if (track.kind === 'audio') {
      if (platformAudios.has(track.id)) return;
      // Chrome only delivers remote WebRTC audio into WebAudio while the stream is
      // also attached to a media element, hence the muted <audio>.
      const el = mediaEl('audio', track);
      const node = ctx.createMediaStreamSource(new MediaStream([track]));
      node.connect(pcAudioBus);
      platformAudios.set(track.id, { track, el, node });
      track.addEventListener('ended', () => {
        node.disconnect(); el.remove(); platformAudios.delete(track.id);
      });
    } else if (track.kind === 'video') {
      if (platformVideos.has(track.id)) return;
      const el = mediaEl('video', track);
      platformVideos.set(track.id, { track, el });
      track.addEventListener('ended', () => { el.remove(); platformVideos.delete(track.id); });
    }
  };

  const tapWebAudio = (node) => {
    const foreign = node.context;
    if (foreign === ctx || !(foreign instanceof AudioContext)) return;
    let tap = webAudioTaps.get(foreign);
    if (!tap) {
      tap = foreign.createMediaStreamDestination();
      webAudioTaps.set(foreign, tap);
      const src = ctx.createMediaStreamSource(tap.stream);
      src.connect(webAudioBus);
      webAudioTapCount += 1;
    }
    nativeConnect.call(node, tap);
  };

  // Clients that play audio through WebAudio *and* receive it over WebRTC would be
  // captured twice; prefer the WebRTC tracks whenever there are any.
  setInterval(() => {
    const target = platformAudios.size > 0 ? 0 : 1;
    webAudioBus.gain.setTargetAtTime(target, ctx.currentTime, 0.05);
  }, 500);

  // Fallback for clients that render video without exposing remote tracks to us:
  // pick up MediaStream-backed <video> elements that appear in the page.
  setInterval(() => {
    document.querySelectorAll('video').forEach((v) => {
      if (hidden.contains(v)) return;
      const s = v.srcObject;
      if (s && typeof s.getVideoTracks === 'function') {
        s.getVideoTracks().forEach((t) => { if (t.readyState === 'live') captureRemoteTrack(t); });
        s.getAudioTracks && s.getAudioTracks().forEach((t) => { if (t.readyState === 'live') captureRemoteTrack(t); });
      }
    });
  }, 1000);

  // ---- room -> platform (hub peers)
  const hubPeers = new Map(); // peerId -> {pc, name, source, videoEl, audioEl, audioNode, initiator}

  // ---- compositing
  const drawGrid = (canvas, tiles, emptyLabel) => {
    const g = canvas.getContext('2d');
    g.fillStyle = '#0f172a';
    g.fillRect(0, 0, W, H);
    const live = tiles.filter((t) => t.el && t.el.videoWidth > 0 && t.track && t.track.readyState === 'live' && !t.track.muted);
    const labelled = tiles.length ? tiles : [];
    if (!labelled.length) {
      g.fillStyle = '#e2e8f0';
      g.font = 'bold 44px sans-serif';
      g.textAlign = 'center';
      g.fillText(cfg.roomTitle || 'MeetBridge', W / 2, H / 2 - 20);
      g.font = '28px sans-serif';
      g.fillStyle = '#94a3b8';
      g.fillText(emptyLabel, W / 2, H / 2 + 30);
      return;
    }
    const n = labelled.length;
    const cols = Math.ceil(Math.sqrt(n));
    const rows = Math.ceil(n / cols);
    const cw = W / cols, ch = H / rows;
    labelled.forEach((t, i) => {
      const x = (i % cols) * cw, y = Math.floor(i / cols) * ch;
      if (live.includes(t)) {
        const vw = t.el.videoWidth, vh = t.el.videoHeight;
        const scale = Math.min(cw / vw, ch / vh);
        const dw = vw * scale, dh = vh * scale;
        try { g.drawImage(t.el, x + (cw - dw) / 2, y + (ch - dh) / 2, dw, dh); } catch (e) { /* frame not ready */ }
      } else {
        g.fillStyle = '#1e293b';
        g.fillRect(x + 4, y + 4, cw - 8, ch - 8);
      }
      if (t.label) {
        g.font = '24px sans-serif';
        g.textAlign = 'start';
        const tw = g.measureText(t.label).width + 20;
        g.fillStyle = 'rgba(15,23,42,0.75)';
        g.fillRect(x + 8, y + ch - 44, tw, 36);
        g.fillStyle = '#f8fafc';
        g.fillText(t.label, x + 18, y + ch - 18);
      }
    });
  };

  setInterval(() => {
    const hubTiles = [...hubPeers.values()].map((p) => ({ el: p.videoEl, track: p.videoTrack, label: p.label }));
    drawGrid(outCanvas, hubTiles, cfg.waitingText || 'Waiting for participants from other platforms');
    const platformTiles = [...platformVideos.values()]
      .filter((v) => v.el.videoWidth > 0 && !v.track.muted)
      .map((v) => ({ el: v.el, track: v.track, label: '' }));
    drawGrid(inCanvas, platformTiles, cfg.platformName || '');
  }, 1000 / FPS);

  // ---- synthetic devices
  const decorate = (synthetic, native) => {
    if (!native) return synthetic;
    try {
      Object.defineProperty(synthetic, 'label', { get: () => native.label });
      const settings = native.getSettings();
      synthetic.getSettings = () => Object.assign({}, settings, synthetic.kind === 'video' ? { width: W, height: H, frameRate: FPS } : {});
      const caps = native.getCapabilities ? native.getCapabilities() : {};
      synthetic.getCapabilities = () => caps;
      synthetic.applyConstraints = async () => {};
    } catch (e) { /* best effort */ }
    return synthetic;
  };

  const syntheticUserMedia = async (constraints) => {
    keepAlive();
    let native = null;
    try { native = await nativeGUM(constraints); } catch (e) { native = null; }
    const out = new MediaStream();
    if (constraints.audio) {
      const t = toPlatform.stream.getAudioTracks()[0].clone();
      ownTrackIds.add(t.id);
      out.addTrack(decorate(t, native && native.getAudioTracks()[0]));
    }
    if (constraints.video) {
      const t = outTrack.clone();
      ownTrackIds.add(t.id);
      out.addTrack(decorate(t, native && native.getVideoTracks()[0]));
    }
    if (native) native.getTracks().forEach((t) => t.stop());
    return out;
  };

  // ---- room signalling
  let selfId = null;
  let iceServers = [];
  const send = (msg) => {
    if (typeof window.__mbSend === 'function') window.__mbSend(JSON.stringify(msg));
  };

  const closePeer = (id) => {
    const p = hubPeers.get(id);
    if (!p) return;
    hubPeers.delete(id);
    try { p.pc.close(); } catch (e) {}
    if (p.audioNode) p.audioNode.disconnect();
    if (p.audioEl) p.audioEl.remove();
    if (p.videoEl) p.videoEl.remove();
  };

  const createPeer = (info, initiator) => {
    closePeer(info.id);
    const pc = new NativePC({ iceServers });
    const peer = { pc, initiator, label: info.name, source: info.source, videoEl: null, videoTrack: null, audioEl: null, audioNode: null, pendingIce: [] };
    hubPeers.set(info.id, peer);
    pc.addTrack(fromPlatform.stream.getAudioTracks()[0], fromPlatform.stream);
    pc.addTrack(inTrack, fromPlatform.stream);
    pc.onicecandidate = (ev) => { if (ev.candidate) send({ type: 'signal', to: info.id, data: { candidate: ev.candidate.toJSON() } }); };
    pc.ontrack = (ev) => {
      const track = ev.track;
      if (track.kind === 'audio') {
        peer.audioEl = mediaEl('audio', track);
        peer.audioNode = ctx.createMediaStreamSource(new MediaStream([track]));
        peer.audioNode.connect(toPlatform);
        peer.audioNode.connect(toPlatformAnalyser);
      } else {
        peer.videoEl = mediaEl('video', track);
        peer.videoTrack = track;
      }
    };
    pc.onconnectionstatechange = async () => {
      if (pc.connectionState === 'failed' && peer.initiator) {
        try {
          const offer = await pc.createOffer({ iceRestart: true });
          await pc.setLocalDescription(offer);
          send({ type: 'signal', to: info.id, data: { sdp: pc.localDescription.toJSON() } });
        } catch (e) { /* peer gone */ }
      }
    };
    if (initiator) {
      (async () => {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        send({ type: 'signal', to: info.id, data: { sdp: pc.localDescription.toJSON() } });
      })();
    }
    return peer;
  };

  const onSignal = async (from, data) => {
    let peer = hubPeers.get(from);
    if (!peer) peer = createPeer({ id: from, name: '', source: '' }, false);
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
  };

  window.__mbHubReceive = (msg) => {
    switch (msg.type) {
      case 'welcome':
        selfId = msg.peer_id;
        iceServers = msg.ice_servers || [];
        msg.peers.forEach((p) => createPeer(p, true));
        break;
      case 'peer-joined':
        if (!hubPeers.has(msg.peer.id)) createPeer(msg.peer, false);
        hubPeers.get(msg.peer.id).label = msg.peer.name;
        break;
      case 'peer-left':
        closePeer(msg.peer_id);
        break;
      case 'signal':
        onSignal(msg.from, msg.data).catch((e) => console.warn('[meetbridge] signal error', e));
        break;
      case 'room-closed':
        [...hubPeers.keys()].forEach(closePeer);
        break;
      default:
        break;
    }
    return true;
  };

  // ---- diagnostics (read by the server for monitoring and by tests)
  const level = (analyser) => {
    const buf = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    return Math.sqrt(sum / buf.length);
  };
  window.__mbStats = () => ({
    selfId,
    hubPeers: [...hubPeers.entries()].map(([id, p]) => ({ id, label: p.label, state: p.pc.connectionState, hasVideo: !!p.videoTrack })),
    platformAudioTracks: platformAudios.size,
    platformVideoTracks: platformVideos.size,
    webAudioTaps: webAudioTapCount,
    toPlatformLevel: level(toPlatformAnalyser),
    fromPlatformLevel: level(fromPlatformAnalyser),
    audioContext: ctx.state,
  });

  window.__mbBridge = { captureRemoteTrack, tapWebAudio, syntheticUserMedia };
})();

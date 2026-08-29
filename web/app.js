const $ = (selector) => document.querySelector(selector);

const elements = {
  shell: $(".app-shell"),
  toolbar: $(".toolbar"),
  viewport: $("#viewport"),
  video: $("#remoteVideo"),
  overlay: $("#connectOverlay"),
  connectButton: $("#connectButton"),
  connectMessage: $("#connectMessage"),
  connectionLabel: $("#connectionLabel"),
  resolution: $("#resolutionMetric"),
  fps: $("#fpsMetric"),
  bitrate: $("#bitrateMetric"),
  latency: $("#latencyMetric"),
  drawer: $("#connectionDrawer"),
  settings: $("#settingsButton"),
  closeDrawer: $("#closeDrawerButton"),
  fullscreen: $("#fullscreenButton"),
  fit: $("#fitButton"),
  audio: $("#audioButton"),
  keyboard: $("#keyboardButton"),
  disconnect: $("#disconnectButton"),
  hint: $("#controlHint"),
  captureLatency: $("#captureLatency"),
  encodeLatency: $("#encodeLatency"),
  networkLatency: $("#networkLatency"),
  decodeLatency: $("#decodeLatency"),
};

const EVDEV = {
  Escape: 1, Digit1: 2, Digit2: 3, Digit3: 4, Digit4: 5, Digit5: 6, Digit6: 7,
  Digit7: 8, Digit8: 9, Digit9: 10, Digit0: 11, Minus: 12, Equal: 13,
  Backspace: 14, Tab: 15, KeyQ: 16, KeyW: 17, KeyE: 18, KeyR: 19, KeyT: 20,
  KeyY: 21, KeyU: 22, KeyI: 23, KeyO: 24, KeyP: 25, BracketLeft: 26,
  BracketRight: 27, Enter: 28, ControlLeft: 29, KeyA: 30, KeyS: 31, KeyD: 32,
  KeyF: 33, KeyG: 34, KeyH: 35, KeyJ: 36, KeyK: 37, KeyL: 38, Semicolon: 39,
  Quote: 40, Backquote: 41, ShiftLeft: 42, Backslash: 43, KeyZ: 44, KeyX: 45,
  KeyC: 46, KeyV: 47, KeyB: 48, KeyN: 49, KeyM: 50, Comma: 51, Period: 52,
  Slash: 53, ShiftRight: 54, NumpadMultiply: 55, AltLeft: 56, Space: 57,
  CapsLock: 58, F1: 59, F2: 60, F3: 61, F4: 62, F5: 63, F6: 64, F7: 65,
  F8: 66, F9: 67, F10: 68, NumLock: 69, ScrollLock: 70, Numpad7: 71,
  Numpad8: 72, Numpad9: 73, NumpadSubtract: 74, Numpad4: 75, Numpad5: 76,
  Numpad6: 77, NumpadAdd: 78, Numpad1: 79, Numpad2: 80, Numpad3: 81,
  Numpad0: 82, NumpadDecimal: 83, IntlBackslash: 86, F11: 87, F12: 88,
  NumpadEnter: 96, ControlRight: 97, NumpadDivide: 98, PrintScreen: 99,
  AltRight: 100, Home: 102, ArrowUp: 103, PageUp: 104, ArrowLeft: 105,
  ArrowRight: 106, End: 107, ArrowDown: 108, PageDown: 109, Insert: 110,
  Delete: 111, MetaLeft: 125, MetaRight: 126, ContextMenu: 127,
};
const BUTTONS = { 0: 272, 1: 274, 2: 273, 3: 275, 4: 276 };

let token = "";
let status = null;
let peer = null;
let control = null;
let stream = null;
let keyboardCapture = true;
let metricsTimer = null;
let lastStats = null;
let moveFrame = 0;
let pendingMove = { dx: 0, dy: 0 };
const downKeys = new Set();

function loadToken() {
  const url = new URL(location.href);
  token = url.searchParams.get("token") || sessionStorage.getItem("rkstream-token") || "";
  if (token) {
    sessionStorage.setItem("rkstream-token", token);
    url.searchParams.delete("token");
    history.replaceState(null, "", url);
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

function waitForIceGathering(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const changed = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", changed);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", changed);
  });
}

async function refreshStatus() {
  if (!token) throw new Error("访问链接缺少控制令牌");
  status = await api("/api/status");
  elements.resolution.textContent = status.video.height >= 1000 ? `${Math.round(status.video.height / 10) * 10}p` : `${status.video.width}×${status.video.height}`;
  if (!status.ready) throw new Error("GNOME 虚拟显示器仍在启动");
}

async function connect() {
  elements.connectButton.disabled = true;
  elements.connectMessage.textContent = "正在准备虚拟显示器";
  try {
    await refreshStatus();
    closePeer();
    peer = new RTCPeerConnection({ bundlePolicy: "max-bundle" });
    stream = new MediaStream();
    elements.video.srcObject = stream;

    peer.ontrack = ({ track }) => {
      stream.addTrack(track);
      elements.video.play().catch(() => {});
    };
    peer.ondatachannel = ({ channel }) => attachControl(channel);
    peer.onconnectionstatechange = updateConnectionState;

    elements.connectMessage.textContent = "正在协商 WebRTC";
    const offer = await api("/api/offer", { method: "POST", body: "{}" });
    await peer.setRemoteDescription(offer);
    const answer = await peer.createAnswer();
    await peer.setLocalDescription(answer);
    await waitForIceGathering(peer);
    await api("/api/answer", { method: "POST", body: JSON.stringify(peer.localDescription) });
  } catch (error) {
    showError(error.message);
  } finally {
    elements.connectButton.disabled = false;
  }
}

function attachControl(channel) {
  control = channel;
  control.onopen = () => {
    elements.overlay.classList.add("is-hidden");
    elements.viewport.focus();
  };
  control.onclose = () => { control = null; };
}

function updateConnectionState() {
  const state = peer?.connectionState || "disconnected";
  const online = state === "connected";
  elements.toolbar.classList.toggle("is-online", online);
  elements.connectionLabel.textContent = online ? "在线" : state === "connecting" ? "连接中" : "已断开";
  if (online) {
    elements.overlay.classList.add("is-hidden");
    startMetrics();
  } else if (["failed", "closed", "disconnected"].includes(state)) {
    elements.overlay.classList.remove("is-hidden");
    elements.connectMessage.textContent = "连接已断开";
    elements.connectButton.textContent = "重新连接";
  }
}

function closePeer() {
  releaseKeys();
  if (document.pointerLockElement) document.exitPointerLock();
  if (metricsTimer) clearInterval(metricsTimer);
  metricsTimer = null;
  lastStats = null;
  control?.close();
  control = null;
  peer?.close();
  peer = null;
  stream = null;
  elements.video.srcObject = null;
  elements.toolbar.classList.remove("is-online");
}

function disconnect() {
  closePeer();
  elements.overlay.classList.remove("is-hidden");
  elements.connectMessage.textContent = "连接已断开";
  elements.connectButton.textContent = "重新连接";
}

function showError(message) {
  closePeer();
  elements.overlay.classList.remove("is-hidden");
  elements.connectMessage.textContent = message;
  elements.connectButton.textContent = "重试";
}

function send(message) {
  if (control?.readyState === "open") control.send(JSON.stringify(message));
}

function scheduleRelativeMove(dx, dy) {
  pendingMove.dx += dx;
  pendingMove.dy += dy;
  if (moveFrame) return;
  moveFrame = requestAnimationFrame(() => {
    moveFrame = 0;
    if (pendingMove.dx || pendingMove.dy) send({ t: "m", ...pendingMove });
    pendingMove = { dx: 0, dy: 0 };
  });
}

function remoteCoordinates(event) {
  if (!status || !elements.video.videoWidth) return null;
  const box = elements.video.getBoundingClientRect();
  const remoteRatio = status.video.width / status.video.height;
  const boxRatio = box.width / box.height;
  const width = boxRatio > remoteRatio ? box.height * remoteRatio : box.width;
  const height = boxRatio > remoteRatio ? box.height : box.width / remoteRatio;
  const left = box.left + (box.width - width) / 2;
  const top = box.top + (box.height - height) / 2;
  if (event.clientX < left || event.clientX > left + width || event.clientY < top || event.clientY > top + height) return null;
  return {
    x: ((event.clientX - left) / width) * status.video.width,
    y: ((event.clientY - top) / height) * status.video.height,
  };
}

function onMouseMove(event) {
  if (document.pointerLockElement === elements.viewport) {
    scheduleRelativeMove(event.movementX, event.movementY);
  } else {
    const point = remoteCoordinates(event);
    if (point) send({ t: "p", ...point });
  }
}

function onMouseButton(event, down) {
  if (event.target.closest?.(".connection-drawer")) return;
  const button = BUTTONS[event.button];
  if (!button) return;
  event.preventDefault();
  send({ t: "b", button, down });
}

function onWheel(event) {
  event.preventDefault();
  const scale = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 10 : 0.5;
  send({
    t: "w",
    dx: Math.max(-100, Math.min(100, event.deltaX * scale)),
    dy: Math.max(-100, Math.min(100, event.deltaY * scale)),
  });
}

function onKey(event, down) {
  if (!keyboardCapture || !control || document.activeElement === elements.connectButton) return;
  const code = EVDEV[event.code];
  if (!code) return;
  event.preventDefault();
  event.stopPropagation();
  if (down && event.repeat) return;
  if (down) downKeys.add(code); else downKeys.delete(code);
  send({ t: "k", code, down });
}

function releaseKeys() {
  for (const code of downKeys) send({ t: "k", code, down: false });
  downKeys.clear();
}

function startMetrics() {
  if (metricsTimer) clearInterval(metricsTimer);
  metricsTimer = setInterval(updateMetrics, 1000);
  updateMetrics();
}

async function updateMetrics() {
  if (!peer || peer.connectionState !== "connected") return;
  const reports = await peer.getStats();
  let video = null;
  let pair = null;
  reports.forEach((report) => {
    if (report.type === "inbound-rtp" && report.kind === "video") video = report;
    if (report.type === "candidate-pair" && report.state === "succeeded" && report.nominated) pair = report;
  });
  if (video) {
    if (lastStats) {
      const seconds = (video.timestamp - lastStats.timestamp) / 1000;
      const mbps = ((video.bytesReceived - lastStats.bytesReceived) * 8) / seconds / 1_000_000;
      const fps = (video.framesDecoded - lastStats.framesDecoded) / seconds;
      const frames = video.framesDecoded - lastStats.framesDecoded;
      const decode = frames > 0 ? ((video.totalDecodeTime - lastStats.totalDecodeTime) / frames) * 1000 : 0;
      elements.bitrate.textContent = `${mbps.toFixed(1)} Mbps`;
      elements.fps.textContent = `${Math.round(fps)} FPS`;
      elements.decodeLatency.textContent = decode ? `${decode.toFixed(1)} ms` : "—";
    }
    lastStats = {
      timestamp: video.timestamp,
      bytesReceived: video.bytesReceived,
      framesDecoded: video.framesDecoded,
      totalDecodeTime: video.totalDecodeTime,
    };
  }
  if (pair?.currentRoundTripTime != null) {
    const rtt = pair.currentRoundTripTime * 1000;
    elements.latency.textContent = `${Math.round(rtt)} ms`;
    elements.networkLatency.textContent = `${(rtt / 2).toFixed(1)} ms`;
  }
}

elements.connectButton.addEventListener("click", connect);
elements.disconnect.addEventListener("click", disconnect);
elements.fullscreen.addEventListener("click", () => document.fullscreenElement ? document.exitFullscreen() : elements.shell.requestFullscreen());
elements.fit.addEventListener("click", () => {
  const original = elements.viewport.classList.toggle("original");
  elements.fit.classList.toggle("is-active", !original);
  elements.fit.title = original ? "适应窗口" : "原始大小";
});
elements.audio.addEventListener("click", () => {
  elements.video.muted = !elements.video.muted;
  elements.audio.classList.toggle("is-muted", elements.video.muted);
});
elements.keyboard.addEventListener("click", () => {
  keyboardCapture = !keyboardCapture;
  elements.keyboard.classList.toggle("is-active", keyboardCapture);
  if (!keyboardCapture) releaseKeys();
});
elements.settings.addEventListener("click", () => {
  const closed = elements.drawer.classList.toggle("is-closed");
  elements.settings.setAttribute("aria-expanded", String(!closed));
});
elements.closeDrawer.addEventListener("click", () => {
  elements.drawer.classList.add("is-closed");
  elements.settings.setAttribute("aria-expanded", "false");
});
elements.viewport.addEventListener("click", (event) => {
  if (event.target.closest?.(".connection-drawer")) return;
  if (peer?.connectionState === "connected" && document.pointerLockElement !== elements.viewport) elements.viewport.requestPointerLock();
});
elements.viewport.addEventListener("mousemove", onMouseMove);
elements.viewport.addEventListener("mousedown", (event) => onMouseButton(event, true));
elements.viewport.addEventListener("mouseup", (event) => onMouseButton(event, false));
elements.viewport.addEventListener("wheel", onWheel, { passive: false });
elements.viewport.addEventListener("contextmenu", (event) => event.preventDefault());
window.addEventListener("keydown", (event) => onKey(event, true), true);
window.addEventListener("keyup", (event) => onKey(event, false), true);
window.addEventListener("blur", releaseKeys);
document.addEventListener("pointerlockchange", () => {
  const locked = document.pointerLockElement === elements.viewport;
  elements.shell.classList.toggle("pointer-locked", locked);
  elements.hint.textContent = locked ? "键盘和鼠标已捕获 · Esc 释放" : "点击画面以捕获键盘和鼠标 · Esc 释放";
  if (!locked) releaseKeys();
});

loadToken();
refreshStatus().then(() => {
  elements.connectMessage.textContent = "准备建立 WebRTC 串流";
}).catch((error) => {
  elements.connectMessage.textContent = error.message;
  elements.connectButton.textContent = "重试";
});

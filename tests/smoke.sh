#!/bin/sh
set -eu

python3 -m py_compile server/rkwebscrd.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
fi
grep -q 'rkwebscr-dmabuf-encoder' server/rkwebscrd.py
grep -q 'SPA_DATA_DmaBuf' native/rkwebscr-dmabuf-encoder.cpp
grep -q 'ARGBToNV12' native/rkwebscr-dmabuf-encoder.cpp
grep -q 'encode_put_frame' native/rkwebscr-dmabuf-encoder.cpp
grep -q 'opusenc' server/rkwebscrd.py
grep -q 'ConnectToEIS' server/rkwebscrd.py
grep -q 'ei_device_keyboard_key' server/rkwebscrd.py
grep -q 'RKWEBSCR_CAPTURE_FPS=64' systemd/rkwebscr.service
grep -q '/usr/bin/rkwebscrd' systemd/rkwebscr.service
grep -q 'RTCPeerConnection' web/app.js
grep -q -- '--headless --wayland' systemd/rkwebscr-headless.service
grep -q '^Package: rkwebscr' debian/control
grep -q '^rkwebscr (0.2.0)' debian/changelog
grep -q 'dpkg-deb --build' debian/rules
grep -q '/usr/lib/rkwebscr/rkwebscr-dmabuf-encoder' debian/rules
if grep -R -q '/usr/libexec/rkwebscr' server debian systemd; then
  printf '%s\n' 'legacy libexec path found' >&2
  exit 1
fi
if grep -R -E -q 'Authorization|token-file|load_token' server web scripts; then
  printf '%s\n' 'authentication code found' >&2
  exit 1
fi
grep -q 'rockchip-mpp-dev' debian/control
grep -q 'wl-clipboard' debian/control
grep -q 'clipboard-set' server/rkwebscrd.py
grep -q 'wl-copy' server/rkwebscrd.py
grep -q 'clipboardDialog' web/app.js
grep -q 'clipboardDialog' web/index.html
test -f LICENSE
test -f CHANGELOG.md
test -f CONTRIBUTING.md
sh -n scripts/rkwebscr-setup
sh -n debian/postinst
printf '%s\n' 'smoke checks passed'

#!/bin/sh
set -eu

python3 -m py_compile server/rkwebscrd.py
python3 -c 'import xml.etree.ElementTree as E; E.parse("avahi/rkwebscr.service")'
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
grep -q 'RKWEBSCR_CAPTURE_FPS=60' systemd/rkwebscr.service
grep -q 'SPA_FORMAT_VIDEO_framerate, SPA_POD_Fraction(&capture_rate)' native/rkwebscr-dmabuf-encoder.cpp
if grep -q 'disable-animations' server/rkwebscrd.py; then
  printf '%s\n' 'GNOME animations disabled' >&2
  exit 1
fi
grep -q '/usr/bin/rkwebscrd' systemd/rkwebscr.service
grep -q 'RTCPeerConnection' web/app.js
grep -q -- '--headless --wayland --mode=ubuntu' systemd/rkwebscr-headless.service
grep -q 'GNOME_SHELL_SESSION_MODE=ubuntu' systemd/rkwebscr-headless.service
grep -q 'XDG_CURRENT_DESKTOP=ubuntu:GNOME' systemd/rkwebscr.service
grep -q '^Package: rkwebscr' debian/control
grep -q '^rkwebscr (0.3.1)' debian/changelog
grep -q 'server_version = "rkwebscr/0.3.1"' server/rkwebscrd.py
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
grep -q 'ubuntu-session' debian/control
grep -q 'avahi-daemon' debian/control
grep -q '_rkwebscr._tcp' avahi/rkwebscr.service
grep -q '<port>80</port>' avahi/rkwebscr.service
grep -q 'host-name=rkwebscr' systemd-system/avahi-daemon.service.d/rkwebscr.conf
grep -q 'ListenStream=80' systemd-system/rkwebscr-http.socket
grep -q 'systemd-socket-proxyd 127.0.0.1:8080' systemd-system/rkwebscr-http.service
grep -q 'clipboard-set' server/rkwebscrd.py
grep -q 'wl-copy' server/rkwebscrd.py
grep -q 'stderr=subprocess.DEVNULL' server/rkwebscrd.py
grep -q 'clipboardDialog' web/app.js
grep -q 'clipboardDialog' web/index.html
test -f LICENSE
test -f CHANGELOG.md
test -f CONTRIBUTING.md
sh -n scripts/rkwebscr-setup
sh -n debian/postinst
sh -n debian/prerm
sh -n debian/postrm
printf '%s\n' 'smoke checks passed'

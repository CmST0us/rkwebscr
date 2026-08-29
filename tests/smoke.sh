#!/bin/sh
set -eu

python3 -m py_compile server/rkstreamd.py
if command -v node >/dev/null 2>&1; then
  node --check web/app.js
fi
grep -q 'rkstream-dmabuf-encoder' server/rkstreamd.py
grep -q 'SPA_DATA_DmaBuf' native/rkstream-dmabuf-encoder.cpp
grep -q 'ARGBToNV12' native/rkstream-dmabuf-encoder.cpp
grep -q 'encode_put_frame' native/rkstream-dmabuf-encoder.cpp
grep -q 'opusenc' server/rkstreamd.py
grep -q 'ConnectToEIS' server/rkstreamd.py
grep -q 'ei_device_keyboard_key' server/rkstreamd.py
grep -q 'RKSTREAM_CAPTURE_FPS=64' systemd/rkstream.service
grep -q '/usr/bin/rkstreamd' systemd/rkstream.service
grep -q 'RTCPeerConnection' web/app.js
grep -q -- '--headless --wayland' systemd/rkstream-headless.service
grep -q '^Package: rkstream' debian/control
grep -q 'dpkg-deb --build' debian/rules
sh -n scripts/rkstream-setup
sh -n debian/postinst
printf '%s\n' 'smoke checks passed'

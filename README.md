# rkstream

GNOME Wayland remote control for one Chrome browser, tuned for Rockchip RK3588:

- Mutter `RecordVirtual` headless display
- PipeWire screen and system-audio capture
- Rockchip MPP H.264 hardware encoding
- Opus audio and WebRTC transport
- keyboard, absolute mouse and pointer-lock relative mouse control

Video uses a native DMA-BUF bridge for capture and encoding. Python handles Mutter D-Bus, SDP exchange, input validation, and the embedded HTTP server; GStreamer handles WebRTC and audio.

## Device requirements

Ubuntu 24.04 GNOME 46. No display, HDMI dummy plug, login screen, or pre-existing graphical session is required. The services run as the target desktop user, not as root.

```bash
sudo apt install python3-gi \
  gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 gir1.2-gst-plugins-bad-1.0 \
  gstreamer1.0-pipewire gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-nice libei1
```

The deployed bridge needs PipeWire, Mesa GBM, Rockchip MPP and their kernel drivers. Building it also needs their development headers plus libyuv:

```bash
make -C native
```

The included device deployment uses a statically linked libyuv, so it does not replace or modify an existing GStreamer installation.

## Install on the Rock5B

Copy this repository to `/opt/rkstream`, stop GNOME Remote Login so it does not create a competing virtual desktop, then install the media-device rule and user services:

```bash
sudo install -m 0644 deploy/99-rockchip-media.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=mpp_class --subsystem-match=misc --subsystem-match=dma_heap
sudo systemctl disable --now gnome-remote-desktop.service
systemctl --user disable --now gnome-remote-desktop.service || true

mkdir -p ~/.config/systemd/user
cp deploy/rkstream.service deploy/rkstream-headless.service ~/.config/systemd/user/
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now rkstream-headless.service rkstream.service
```

Read the token and open the resulting URL from Chrome:

```bash
TOKEN=$(cat ~/.config/rkstream/token)
printf 'http://ROCK5B_IP:8080/?token=%s\n' "$TOKEN"
```

WebRTC media is DTLS-SRTP encrypted. The current HTTP control endpoint is intended for a trusted LAN; use an SSH tunnel when the LAN is not trusted.

## Development run

```bash
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
export GST_REGISTRY=$XDG_RUNTIME_DIR/rkstream-gstreamer-registry.bin
python3 server/rkstreamd.py --web-root "$PWD/web" --verbose
```

The first click connects and permits Chrome to play audio. Clicking the remote frame captures the pointer; `Esc` releases it.

## Checks

```bash
sh tests/smoke.sh
```

The latency-first settings are 720p60, 6 Mbps CBR, a one-second GOP, three pending MPP frames and 10 ms Opus frames. Mutter is sampled at a calibrated 64 Hz because this headless RK3588 setup runs about 5% below its requested PipeWire maximum; MPP and WebRTC remain 60 FPS. `RKSTREAM_CAPTURE_FPS` is the calibration knob for other boards.

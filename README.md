# rkwebscr

Low-latency GNOME Wayland remote control for Rockchip RK3588 boards. rkwebscr
creates a headless Mutter virtual monitor and streams it to one browser on the
LAN. It is tested on Radxa ROCK 5B with Ubuntu 24.04 and GNOME 46.

## Features

- Mutter `RecordVirtual` headless display; HDMI is not required
- PipeWire linear DMA-BUF capture
- Rockchip MPP H.264 hardware encoding
- Opus system audio and WebRTC transport
- Keyboard, absolute mouse, relative mouse, and wheel input through libei
- Token-protected HTTP control API

The hot video path is:

```text
GNOME/Mutter -> PipeWire DMA-BUF -> GBM map -> libyuv NV12 -> MPP H.264 -> WebRTC -> browser
```

Python owns the Mutter D-Bus sessions, input validation, WebRTC negotiation,
and HTTP server. The C++ bridge owns DMA-BUF capture, pixel conversion, and MPP
encoding. GStreamer handles WebRTC RTP and Opus audio. The package does not
replace or modify an existing GStreamer installation.

## Repository layout

```text
native/     DMA-BUF to Rockchip MPP encoder
server/     GNOME, WebRTC, input, and HTTP service
web/        Browser client
systemd/    User services for headless GNOME and rkwebscr
udev/       Rockchip media-device permissions
scripts/    Post-install user setup
debian/     Debian source-package metadata
tests/      Fast repository checks
```

## Build

Install the build dependencies on the RK3588 target:

```bash
sudo apt install build-essential dpkg-dev pkg-config \
  libpipewire-0.3-dev libdrm-dev libgbm-dev libyuv-dev rockchip-mpp-dev
```

Build the native encoder and run the repository checks:

```bash
make
make check
```

Build the Debian binary package:

```bash
make deb
```

`dpkg-buildpackage` writes `rkwebscr_0.1.0_<architecture>.deb` to the parent
directory, following normal Debian source-package conventions.

## Install

Install the package and enable lingering for the desktop user:

```bash
sudo apt install ../rkwebscr_0.1.0_arm64.deb
sudo usermod -aG video "$USER"
sudo loginctl enable-linger "$USER"
```

Log out and back in after changing group membership. GNOME Remote Desktop must
not create a competing virtual monitor; disable it for the same user if it is
enabled:

```bash
systemctl --user disable --now gnome-remote-desktop.service
```

Start rkwebscr as the desktop user:

```bash
rkwebscr-setup
```

The command enables both user services and prints the tokenized LAN URL. The
token is stored with mode `0600` in `~/.config/rkwebscr/token`.

For USB instead of LAN transport:

```bash
adb forward tcp:18080 tcp:8080
```

Then open `http://127.0.0.1:18080/?token=TOKEN` in Chrome. WebRTC media is
DTLS-SRTP encrypted. The HTTP endpoint is intended for a trusted LAN or an ADB
or SSH tunnel.

## Configuration

The packaged defaults are 1280x720, 60 FPS, 6 Mbps CBR, and one-second GOP.
Override the service with `systemctl --user edit rkwebscr.service` when another
resolution or bitrate is needed. `RKWEBSCR_CAPTURE_FPS` is the hardware
calibration knob; the ROCK 5B default is 64 to produce approximately 60 output
frames per second.

Useful commands:

```bash
systemctl --user status rkwebscr-headless.service rkwebscr.service
journalctl --user -u rkwebscr.service -f
systemctl --user restart rkwebscr.service
```

A black but connected stream can simply be an empty headless workspace. Launch
an application on `WAYLAND_DISPLAY=wayland-0` to distinguish that from a video
failure. Encoder logs should report frames with zero `dropped` and `failed`.

## License

MIT. See [LICENSE](LICENSE).

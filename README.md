# rkwebscr

Low-latency GNOME Wayland remote control for Rockchip RK3588 boards. rkwebscr
creates a headless Mutter virtual monitor and streams it to one browser on the
LAN. It is tested on Radxa ROCK 5B with Ubuntu 24.04 and GNOME 46.

## Features

- Ubuntu GNOME session mode on a Mutter `RecordVirtual` headless display; HDMI is not required
- PipeWire linear DMA-BUF capture
- Rockchip MPP H.264 hardware encoding
- Opus system audio and WebRTC transport
- Keyboard, absolute mouse, relative mouse, and wheel input through libei
- Bidirectional text clipboard transfer through Wayland
- `rkwebscr.local` mDNS hostname and DNS-SD service discovery
- Direct HTTP control on a trusted local network

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
systemd-system/ Avahi system-service configuration
avahi/      DNS-SD service advertisement
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

`dpkg-buildpackage` writes `rkwebscr_0.3.3_<architecture>.deb` to the parent
directory, following normal Debian source-package conventions.

## Installed files

The Debian package uses the standard Ubuntu filesystem layout:

```text
/usr/bin/rkwebscrd                              public service executable
/usr/bin/rkwebscr-setup                         first-run user setup
/usr/lib/rkwebscr/rkwebscr-dmabuf-encoder       package-private native helper
/usr/share/rkwebscr/web/                         architecture-independent web UI
/usr/lib/systemd/user/rkwebscr.service           user service
/usr/lib/systemd/user/rkwebscr-headless.service  headless GNOME user service
/usr/lib/systemd/system/avahi-daemon.service.d/  mDNS hostname configuration
/usr/lib/systemd/system/rkwebscr-http.*           port 80 socket proxy
/etc/avahi/services/rkwebscr.service             DNS-SD service description
/usr/lib/udev/rules.d/99-rkwebscr-rockchip.rules device permissions
/usr/share/doc/rkwebscr/                         package documentation
```

## Install

Install the package and enable lingering for the desktop user:

```bash
sudo apt install ../rkwebscr_0.3.3_arm64.deb
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

The command enables both user services and prints the local and LAN URLs.
On the same LAN, open `http://rkwebscr.local/`; Avahi also advertises
`_rkwebscr._tcp` and `_http._tcp`. If another device already owns the same
mDNS name, Avahi may add a numeric suffix to avoid a collision.

For USB instead of LAN transport:

```bash
adb forward tcp:18080 tcp:8080
```

Then open `http://127.0.0.1:18080/` in Chrome. WebRTC media is DTLS-SRTP
encrypted, but the HTTP control endpoint has no authentication. Run it only on
a trusted LAN or behind an ADB or SSH tunnel.

Use the clipboard button in the toolbar to transfer text in either direction.
On localhost the browser can usually read and write the local clipboard
directly. On plain HTTP LAN addresses, use the dialog's text box if the browser
blocks its Clipboard API.

## Configuration

The packaged defaults are 1280x720, 60 FPS, 6 Mbps CBR, and one-second GOP.
Override the service with `systemctl --user edit rkwebscr.service` when another
resolution or bitrate is needed. `RKWEBSCR_CAPTURE_FPS` is the display-clock
calibration knob; the ROCK 5B default is 64 to produce approximately 60 output
frames per second. Mutter emits frames only when pixels change, with this value
as its refresh-rate ceiling.

Useful commands:

```bash
systemctl --user status rkwebscr-headless.service rkwebscr.service
journalctl --user -u rkwebscr.service -f
systemctl --user restart rkwebscr.service
```

A black but connected stream can simply be an empty headless workspace. Launch
an application on `WAYLAND_DISPLAY=wayland-0` to distinguish that from a video
failure. Encoder logs should report frames with zero `dropped` and `failed`.

## Development and deployment

Git is the source of truth. Do not copy individual files into `/opt`, `/usr`,
or a user's systemd directory. Every device update follows this sequence:

```bash
# develop and commit in this repository
make check

# add a new top entry to debian/changelog, then build
make deb

# deploy only the resulting package
adb push ../rkwebscr_VERSION_arm64.deb /data/local/tmp/
adb shell 'apt install /data/local/tmp/rkwebscr_VERSION_arm64.deb'

# reload and restart as the desktop user
systemctl --user daemon-reload
systemctl --user restart rkwebscr-headless.service rkwebscr.service
```

Run `rkwebscr-setup` only for the first installation. Commit the release state
before building so the installed package can always be traced to Git.

## License

MIT. See [LICENSE](LICENSE).

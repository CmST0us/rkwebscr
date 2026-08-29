# rkwebscr

Low-latency GNOME Wayland remote control for Rockchip RK3588 boards. rkwebscr
creates a headless Mutter virtual monitor and streams it to one browser on the
LAN. It is tested on Radxa ROCK 5B with Ubuntu 24.04 and GNOME 46.

## Features

- Ubuntu GNOME session mode on a Mutter `RecordVirtual` headless display; HDMI is not required
- PipeWire linear DMA-BUF capture
- Rockchip MPP H.264 hardware encoding
- Bidirectional Opus audio: system output to the browser and browser microphone to GNOME
- Display-refresh-aligned browser rendering with a bounded frame buffer
- Frame-rate-coalesced pointer input plus keyboard and wheel control through libei
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

Modern browsers use a six-frame `ImageBitmap` queue for the final presentation
step. This trades a small amount of local latency for stable display cadence;
browsers without the required APIs fall back to the native video element.

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

`dpkg-buildpackage` writes `rkwebscr_0.4.1_<architecture>.deb` to the parent
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
sudo apt install ../rkwebscr_0.4.1_arm64.deb
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

The public HTTP listener uses TCP port 80 and WebRTC media uses UDP port 8090.
Allow both ports on the trusted LAN if a firewall is enabled. WebRTC media is
DTLS-SRTP encrypted, but the HTTP control endpoint has no authentication.

Use the clipboard button in the toolbar to transfer text in either direction.
On plain HTTP LAN addresses, use the dialog's text box if the browser blocks
its Clipboard API.

Browsers require HTTPS before granting microphone access to a plain LAN
hostname.

## Configuration

The packaged defaults are 1280x720, 60 FPS, 6 Mbps CBR, and one-second GOP.
Override the service with `systemctl --user edit rkwebscr.service` when another
resolution or bitrate is needed. `RKWEBSCR_CAPTURE_FPS` is the display-clock
calibration knob; the ROCK 5B default is 66 to produce approximately 60 output
frames per second. Mutter emits frames only when pixels change, with this value
as its refresh-rate ceiling.

The package pins the RK3588 CPU and memory clocks while limiting the GPU to
900 MHz; this avoids frame-time spikes without using the ROCK 5B's unstable
all-domain maximum. Disable the tuning with
`sudo systemctl disable --now rkwebscr-performance.service` when power use is
more important than a steady 60 FPS. Its four `RKWEBSCR_*_HZ` environment
values are the board calibration knobs.

Useful commands:

```bash
systemctl --user status rkwebscr-headless.service rkwebscr.service
journalctl --user -u rkwebscr.service -f
systemctl --user restart rkwebscr.service
```

A black but connected stream can simply be an empty headless workspace. Launch
an application on `WAYLAND_DISPLAY=wayland-0` to distinguish that from a video
failure. Encoder logs should report frames with zero `dropped` and `failed`.

The connection drawer reports browser cadence, dropped frames, and freezes. A
periodic RTT spike on Wi-Fi can come from NetworkManager power saving. Disable
it for the active connection when low-latency streaming is the priority:

```bash
WIFI_CONNECTION="$(nmcli -g GENERAL.CONNECTION device show wlan0)"
sudo nmcli connection modify "$WIFI_CONNECTION" 802-11-wireless.powersave 2
sudo nmcli connection up "$WIFI_CONNECTION" ifname wlan0
```

Bringing the connection up briefly interrupts Wi-Fi. The setting is stored in
that NetworkManager connection profile.

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

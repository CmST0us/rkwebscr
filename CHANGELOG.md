# Changelog

## 0.4.3 - 2026-08-30

- Switch the headless desktop from the Ubuntu session profile to native GNOME
  Shell user mode.
- Replace Ubuntu session and extension dependencies with GNOME Session and
  GNOME Shell.

## 0.4.2 - 2026-08-30

- Tie the headless shell to `graphical-session.target` so GNOME portal services
  and applications start without D-Bus timeouts.

## 0.4.1 - 2026-08-29

- Remove the ADB-forwarded USB transport and use normal LAN ICE candidates.
- Expose HTTP on port 80 while keeping the application backend on loopback.
- Start normally when no application is producing desktop audio yet.

## 0.4.0 - 2026-08-29

- Forward the browser microphone to a PipeWire source selected as GNOME's default input.
- Capture desktop sound from a dedicated headless PipeWire output instead of a physical HDMI monitor.
- Add separate speaker and microphone controls to the web client.

## 0.3.8 - 2026-08-29

- Use the browser's native WebRTC video compositor instead of a second canvas
  frame clock, eliminating periodic catch-up frames on high-refresh displays.

## 0.3.7 - 2026-08-29

- Correct FrameSmoother clock drift with single-refresh phase adjustments
  instead of periodic full-frame holds or drops.

## 0.3.6 - 2026-08-29

- Carry WebRTC media over ADB-forwarded ICE-TCP when the page is opened on
  localhost, while preserving normal LAN WebRTC behavior.
- Pin tested RK3588 clocks below the unstable all-domain maximum for smooth
  60 FPS rendering.
- Calibrate the ROCK 5B virtual monitor to 66 Hz for steady 60 FPS output.
- Align browser presentation to the local display refresh with a bounded
  `ImageBitmap` frame buffer, avoiding clustered frame-time stalls.
- Coalesce high-rate pointer motion on a continuous frame-rate clock so input
  does not starve GNOME rendering.

## 0.3.5 - 2026-08-29

- Force an H.264 IDR frame when a browser viewer connects, including on a
  static GNOME desktop.

## 0.3.4 - 2026-08-29

- Pace encoded frames on a 60 Hz clock before WebRTC packetization.
- Bound the pacing queue and apply backpressure without dropping dependent H.264
  frames.

## 0.3.3 - 2026-08-29

- Report browser frame cadence, jitter, dropped frames, and freezes in the
  connection drawer.

## 0.3.2 - 2026-08-29

- Keep standard GNOME animations enabled in the headless desktop.

## 0.3.1 - 2026-08-29

- Configure the `rkwebscr.local` hostname before Avahi starts so queries are
  answered reliably.
- Make the advertised URL available directly on standard HTTP port 80.

## 0.3.0 - 2026-08-29

- Advertise `rkwebscr.local` and the `_rkwebscr._tcp`/`_http._tcp` services
  through Avahi mDNS.

## 0.2.1 - 2026-08-29

- Match the headless desktop to the standard Ubuntu GNOME session, including
  Yaru Shell and the default Ubuntu extensions.

## 0.2.0 - 2026-08-29

- Add bidirectional text clipboard transfer between the browser and GNOME.

## 0.1.2 - 2026-08-29

- Remove HTTP control authentication for trusted local networks.

## 0.1.1 - 2026-08-29

- Use Ubuntu-standard package paths and package-managed deployment.

## 0.1.0 - 2026-08-29

- Create a headless GNOME Wayland virtual monitor through Mutter.
- Capture linear DMA-BUF frames and encode H.264 with Rockchip MPP.
- Stream H.264 and Opus to one browser over WebRTC.
- Forward keyboard, mouse, and wheel input through Mutter EIS.
- Add systemd user services, udev permissions, and Debian packaging.

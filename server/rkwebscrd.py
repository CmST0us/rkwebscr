#!/usr/bin/env python3
"""GNOME virtual monitor streamed to one browser over WebRTC."""

from __future__ import annotations

import argparse
from collections import deque
import ctypes
import json
import logging
import mimetypes
import os
from pathlib import Path
import signal
import struct
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstSdp", "1.0")
gi.require_version("GstWebRTC", "1.0")
from gi.repository import Gio, GLib, Gst, GstSdp, GstWebRTC


LOG = logging.getLogger("rkwebscr")
MUTTER_REMOTE_DESKTOP = "org.gnome.Mutter.RemoteDesktop"
MUTTER_SCREEN_CAST = "org.gnome.Mutter.ScreenCast"


def proxy(name: str, path: str, interface: str) -> Gio.DBusProxy:
    return Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SESSION,
        Gio.DBusProxyFlags.DO_NOT_AUTO_START,
        None,
        name,
        path,
        interface,
        None,
    )


class EiInput:
    CONNECT = 1
    DISCONNECT = 2
    SEAT_ADDED = 3
    DEVICE_ADDED = 5
    DEVICE_REMOVED = 6
    DEVICE_RESUMED = 8

    POINTER = 1 << 0
    POINTER_ABSOLUTE = 1 << 1
    KEYBOARD = 1 << 2
    SCROLL = 1 << 4
    BUTTON = 1 << 5

    def __init__(self, fd: int, width: int, height: int, ready_callback):
        self.width = width
        self.height = height
        self.sequence = 0
        self.keyboard = None
        self.pointer = None
        self.absolute = None
        self.region = None
        self.ready_callback = ready_callback
        self.lib = ctypes.CDLL("libei.so.1")
        self._bind()
        self.context = self.lib.ei_new_sender(None)
        if not self.context:
            raise RuntimeError("Could not create libei sender")
        self.lib.ei_configure_name(self.context, b"rkwebscr")
        result = self.lib.ei_setup_backend_fd(self.context, fd)
        if result < 0:
            self.lib.ei_unref(self.context)
            self.context = None
            raise RuntimeError(f"Could not connect to Mutter EIS: {result}")
        self.source_id = GLib.io_add_watch(
            self.lib.ei_get_fd(self.context),
            GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR,
            self._on_ready,
        )

    def _bind(self) -> None:
        pointer = ctypes.c_void_p
        integer = ctypes.c_int
        uint = ctypes.c_uint32
        self.lib.ei_new_sender.argtypes = [pointer]
        self.lib.ei_new_sender.restype = pointer
        self.lib.ei_configure_name.argtypes = [pointer, ctypes.c_char_p]
        self.lib.ei_setup_backend_fd.argtypes = [pointer, integer]
        self.lib.ei_setup_backend_fd.restype = integer
        self.lib.ei_get_fd.argtypes = [pointer]
        self.lib.ei_get_fd.restype = integer
        self.lib.ei_dispatch.argtypes = [pointer]
        self.lib.ei_dispatch.restype = integer
        self.lib.ei_get_event.argtypes = [pointer]
        self.lib.ei_get_event.restype = pointer
        self.lib.ei_event_get_type.argtypes = [pointer]
        self.lib.ei_event_get_type.restype = integer
        self.lib.ei_event_get_seat.argtypes = [pointer]
        self.lib.ei_event_get_seat.restype = pointer
        self.lib.ei_event_get_device.argtypes = [pointer]
        self.lib.ei_event_get_device.restype = pointer
        self.lib.ei_event_unref.argtypes = [pointer]
        self.lib.ei_seat_bind_capabilities.argtypes = [pointer]
        self.lib.ei_device_has_capability.argtypes = [pointer, integer]
        self.lib.ei_device_has_capability.restype = ctypes.c_bool
        self.lib.ei_device_ref.argtypes = [pointer]
        self.lib.ei_device_ref.restype = pointer
        self.lib.ei_device_unref.argtypes = [pointer]
        self.lib.ei_device_get_region.argtypes = [pointer, ctypes.c_size_t]
        self.lib.ei_device_get_region.restype = pointer
        self.lib.ei_region_ref.argtypes = [pointer]
        self.lib.ei_region_ref.restype = pointer
        self.lib.ei_region_unref.argtypes = [pointer]
        for name in ("x", "y", "width", "height"):
            function = getattr(self.lib, f"ei_region_get_{name}")
            function.argtypes = [pointer]
            function.restype = uint
        self.lib.ei_device_start_emulating.argtypes = [pointer, uint]
        self.lib.ei_device_frame.argtypes = [pointer, ctypes.c_uint64]
        self.lib.ei_device_keyboard_key.argtypes = [pointer, uint, ctypes.c_bool]
        self.lib.ei_device_pointer_motion.argtypes = [pointer, ctypes.c_double, ctypes.c_double]
        self.lib.ei_device_pointer_motion_absolute.argtypes = [pointer, ctypes.c_double, ctypes.c_double]
        self.lib.ei_device_button_button.argtypes = [pointer, uint, ctypes.c_bool]
        self.lib.ei_device_scroll_delta.argtypes = [pointer, ctypes.c_double, ctypes.c_double]
        self.lib.ei_device_scroll_stop.argtypes = [pointer, ctypes.c_bool, ctypes.c_bool]
        self.lib.ei_unref.argtypes = [pointer]

    def _on_ready(self, _fd, condition) -> bool:
        if condition & (GLib.IOCondition.HUP | GLib.IOCondition.ERR):
            LOG.error("Mutter EIS connection closed")
            return GLib.SOURCE_REMOVE
        if self.lib.ei_dispatch(self.context) < 0:
            return GLib.SOURCE_REMOVE
        while event := self.lib.ei_get_event(self.context):
            try:
                self._handle_event(event)
            finally:
                self.lib.ei_event_unref(event)
        return GLib.SOURCE_CONTINUE

    def _handle_event(self, event) -> None:
        kind = self.lib.ei_event_get_type(event)
        if kind == self.CONNECT:
            if self.ready_callback:
                callback, self.ready_callback = self.ready_callback, None
                callback()
        elif kind == self.SEAT_ADDED:
            seat = self.lib.ei_event_get_seat(event)
            self.lib.ei_seat_bind_capabilities(
                seat,
                self.POINTER,
                self.POINTER_ABSOLUTE,
                self.KEYBOARD,
                self.SCROLL,
                self.BUTTON,
                0,
            )
        elif kind == self.DEVICE_ADDED:
            device = self.lib.ei_event_get_device(event)
            if self.lib.ei_device_has_capability(device, self.KEYBOARD):
                self._replace("keyboard", device)
            if self.lib.ei_device_has_capability(device, self.POINTER):
                self._replace("pointer", device)
            if self.lib.ei_device_has_capability(device, self.POINTER_ABSOLUTE):
                self._replace("absolute", device)
                if self.region:
                    self.lib.ei_region_unref(self.region)
                region = self.lib.ei_device_get_region(device, 0)
                self.region = self.lib.ei_region_ref(region) if region else None
        elif kind == self.DEVICE_RESUMED:
            device = self.lib.ei_event_get_device(event)
            self.sequence += 1
            self.lib.ei_device_start_emulating(device, self.sequence)
            LOG.info("Mutter EIS input device ready")
        elif kind == self.DEVICE_REMOVED:
            device = self.lib.ei_event_get_device(event)
            for name in ("keyboard", "pointer", "absolute"):
                if getattr(self, name) == device:
                    self._replace(name, None)
        elif kind == self.DISCONNECT:
            LOG.error("Mutter rejected the EIS input connection")

    def _replace(self, name: str, device) -> None:
        previous = getattr(self, name)
        if previous:
            self.lib.ei_device_unref(previous)
        setattr(self, name, self.lib.ei_device_ref(device) if device else None)

    @staticmethod
    def _time() -> int:
        return time.monotonic_ns() // 1_000

    def key(self, code: int, down: bool) -> None:
        if self.keyboard:
            self.lib.ei_device_keyboard_key(self.keyboard, code, down)
            self.lib.ei_device_frame(self.keyboard, self._time())

    def motion(self, dx: float, dy: float) -> None:
        if self.pointer:
            self.lib.ei_device_pointer_motion(self.pointer, dx, dy)
            self.lib.ei_device_frame(self.pointer, self._time())

    def position(self, x: float, y: float) -> None:
        if not self.absolute or not self.region:
            return
        region_x = self.lib.ei_region_get_x(self.region)
        region_y = self.lib.ei_region_get_y(self.region)
        region_width = self.lib.ei_region_get_width(self.region)
        region_height = self.lib.ei_region_get_height(self.region)
        target_x = region_x + x * region_width / self.width
        target_y = region_y + y * region_height / self.height
        self.lib.ei_device_pointer_motion_absolute(self.absolute, target_x, target_y)
        self.lib.ei_device_frame(self.absolute, self._time())

    def button(self, code: int, down: bool) -> None:
        if self.absolute:
            self.lib.ei_device_button_button(self.absolute, code, down)
            self.lib.ei_device_frame(self.absolute, self._time())

    def scroll(self, dx: float, dy: float) -> None:
        if self.absolute:
            now = self._time()
            self.lib.ei_device_scroll_delta(self.absolute, dx, dy)
            self.lib.ei_device_frame(self.absolute, now)
            self.lib.ei_device_scroll_stop(self.absolute, bool(dx), bool(dy))
            self.lib.ei_device_frame(self.absolute, now)

    def close(self) -> None:
        if self.source_id:
            GLib.source_remove(self.source_id)
            self.source_id = 0
        if self.region:
            self.lib.ei_region_unref(self.region)
            self.region = None
        for name in ("keyboard", "pointer", "absolute"):
            self._replace(name, None)
        if self.context:
            self.lib.ei_unref(self.context)
            self.context = None


class MutterSession:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.remote_session: Gio.DBusProxy | None = None
        self.screen_session: Gio.DBusProxy | None = None
        self.stream: Gio.DBusProxy | None = None
        self.stream_path = ""
        self.input: EiInput | None = None
        self._node_callback = None

    def start(self, node_callback) -> None:
        self._node_callback = node_callback

        remote = proxy(
            MUTTER_REMOTE_DESKTOP,
            "/org/gnome/Mutter/RemoteDesktop",
            MUTTER_REMOTE_DESKTOP,
        )
        result = remote.call_sync(
            "CreateSession", None, Gio.DBusCallFlags.NONE, -1, None
        )
        remote_path = result.unpack()[0]
        self.remote_session = proxy(
            MUTTER_REMOTE_DESKTOP,
            remote_path,
            f"{MUTTER_REMOTE_DESKTOP}.Session",
        )
        result, fd_list = self.remote_session.call_with_unix_fd_list_sync(
            "ConnectToEIS",
            GLib.Variant("(a{sv})", ({},)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            None,
        )
        self.input = EiInput(
            fd_list.get(result.unpack()[0]), self.width, self.height, self._start_session
        )

        session_id = self.remote_session.get_cached_property("SessionId").unpack()
        screen = proxy(
            MUTTER_SCREEN_CAST,
            "/org/gnome/Mutter/ScreenCast",
            MUTTER_SCREEN_CAST,
        )
        properties = {
            "remote-desktop-session-id": GLib.Variant("s", session_id),
        }
        result = screen.call_sync(
            "CreateSession",
            GLib.Variant("(a{sv})", (properties,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        screen_path = result.unpack()[0]
        self.screen_session = proxy(
            MUTTER_SCREEN_CAST,
            screen_path,
            f"{MUTTER_SCREEN_CAST}.Session",
        )

    def _start_session(self) -> None:
        self.remote_session.call(
            "Start",
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_session_started,
            None,
        )

    def _on_session_started(self, session, result, _user_data) -> None:
        try:
            session.call_finish(result)
        except GLib.Error as error:
            LOG.error("Failed to start Mutter session: %s", error.message)
            return

        record_options = {
            "cursor-mode": GLib.Variant("u", 1),
            "is-platform": GLib.Variant("b", True),
        }
        result = self.screen_session.call_sync(
            "RecordVirtual",
            GLib.Variant("(a{sv})", (record_options,)),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        self.stream_path = result.unpack()[0]
        self.stream = proxy(
            MUTTER_SCREEN_CAST,
            self.stream_path,
            f"{MUTTER_SCREEN_CAST}.Stream",
        )
        self.stream.connect("g-signal", self._on_stream_signal)
        self.remote_session.connect("g-signal", self._on_remote_signal)
        LOG.info("Mutter virtual stream created: %s", self.stream_path)
        self.stream.call(
            "Start",
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_stream_started,
            None,
        )

    @staticmethod
    def _on_stream_started(stream, result, _user_data) -> None:
        try:
            stream.call_finish(result)
        except GLib.Error as error:
            LOG.error("Failed to start Mutter stream: %s", error.message)

    def stop(self) -> None:
        if self.input:
            self.input.close()
            self.input = None
        if not self.remote_session:
            return
        try:
            self.remote_session.call_sync(
                "Stop", None, Gio.DBusCallFlags.NONE, 2_000, None
            )
        except GLib.Error:
            pass
        self.remote_session = None
        self.screen_session = None
        self.stream = None

    def _on_stream_signal(self, _proxy, _sender, name, parameters) -> None:
        if name != "PipeWireStreamAdded":
            return
        node_id = parameters.unpack()[0]
        LOG.info("Mutter PipeWire node: %d", node_id)
        self._node_callback(node_id)

    def _on_remote_signal(self, _proxy, _sender, name, _parameters) -> None:
        if name == "Closed":
            LOG.error("Mutter closed the remote desktop session")

    def dispatch_input(self, message: dict) -> None:
        if not self.input:
            return

        kind = message.get("t")
        try:
            if kind == "m":
                dx = max(-2000.0, min(2000.0, float(message["dx"])))
                dy = max(-2000.0, min(2000.0, float(message["dy"])))
                self.input.motion(dx, dy)
            elif kind == "p":
                x = max(0.0, min(float(self.width - 1), float(message["x"])))
                y = max(0.0, min(float(self.height - 1), float(message["y"])))
                self.input.position(x, y)
            elif kind == "b":
                button = int(message["button"])
                if button not in (272, 273, 274, 275, 276):
                    return
                self.input.button(button, bool(message["down"]))
            elif kind == "w":
                dx = max(-100.0, min(100.0, float(message.get("dx", 0))))
                dy = max(-100.0, min(100.0, float(message.get("dy", 0))))
                self.input.scroll(dx, dy)
            elif kind == "k":
                code = int(message["code"])
                if not 1 <= code <= 767:
                    return
                self.input.key(code, bool(message["down"]))
        except (KeyError, TypeError, ValueError):
            LOG.debug("Dropped malformed input message: %r", message)


class WebRTCSession:
    MAX_CONTROL_MESSAGE = 1024 * 1024
    MAX_CLIPBOARD_BYTES = 256 * 1024

    def __init__(self, app: "Application", node_id: int):
        self.app = app
        self.node_id = node_id
        self.pipeline: Gst.Pipeline | None = None
        self.webrtc: Gst.Element | None = None
        self.screen: Gst.Element | None = None
        self.control = None
        self.started_ns = time.monotonic_ns()
        self._frame_duration = Gst.SECOND // app.config.fps
        self._frame_queue: deque[bytes] = deque()
        self._frame_ready = threading.Condition()
        self._pacer_stopping = False
        self._offer_event: threading.Event | None = None
        self._offer_sdp: str | None = None
        self._build_pipeline()
        self._pacer = threading.Thread(target=self._pace_frames, daemon=True)
        self._pacer.start()

    def _build_pipeline(self) -> None:
        c = self.app.config
        audio_branch = ""
        if c.audio:
            audio_branch = """
                pipewiresrc name=audio do-timestamp=true keepalive-time=10 min-buffers=2 max-buffers=8 !
                  audio/x-raw,rate=48000,channels=2 !
                  queue leaky=downstream max-size-buffers=4 max-size-bytes=0 max-size-time=40000000 !
                  audioconvert ! audioresample !
                  opusenc bitrate=128000 bitrate-type=cbr frame-size=10 inband-fec=true packet-loss-percentage=2 !
                  valve name=audio_gate drop=true drop-mode=forward-sticky-events !
                  rtpopuspay pt=111 mtu=1200 !
                  application/x-rtp,media=audio,encoding-name=OPUS,payload=111,clock-rate=48000,encoding-params=(string)2 !
                  queue name=audio_out max-size-buffers=64 max-size-bytes=0 max-size-time=200000000
            """
        desc = f"""
            webrtcbin name=webrtc bundle-policy=max-bundle latency=0
            appsrc name=screen is-live=true format=time block=false !
              video/x-h264,stream-format=byte-stream,alignment=au,width={c.width},height={c.height},framerate={c.fps}/1 !
              h264parse config-interval=-1 disable-passthrough=true !
              video/x-h264,stream-format=byte-stream,alignment=au,profile=baseline !
              valve name=video_gate drop=true drop-mode=forward-sticky-events !
              rtph264pay pt=96 mtu=1200 config-interval=-1 aggregate-mode=zero-latency !
              application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,packetization-mode=(string)1 !
              queue name=video_out max-size-buffers=512 max-size-bytes=0 max-size-time=500000000
            {audio_branch}
        """
        self.pipeline = Gst.parse_launch(desc)
        self.webrtc = self.pipeline.get_by_name("webrtc")
        self.video_gate = self.pipeline.get_by_name("video_gate")
        self.audio_gate = self.pipeline.get_by_name("audio_gate")
        output_names = ["video_out"] + (["audio_out"] if c.audio else [])
        for output_name in output_names:
            output = self.pipeline.get_by_name(output_name)
            sink_pad = self.webrtc.request_pad_simple("sink_%u")
            if not sink_pad:
                raise RuntimeError(f"Could not request WebRTC pad for {output_name}")
            result = output.get_static_pad("src").link(sink_pad)
            if result != Gst.PadLinkReturn.OK:
                raise RuntimeError(f"Could not link {output_name} to WebRTC: {result}")
        self.screen = self.pipeline.get_by_name("screen")

        if c.audio:
            audio = self.pipeline.get_by_name("audio")
            audio.set_property("client-name", "rkwebscr-audio")
            props = Gst.Structure.new_empty("props")
            props.set_value("media.role", "Screen")
            props.set_value("stream.capture.sink", True)
            if c.audio_target:
                audio.set_property("target-object", c.audio_target)
            audio.set_property("stream-properties", props)

        self.webrtc.connect("notify::ice-gathering-state", self._on_ice_state)
        self.webrtc.connect("notify::connection-state", self._on_connection_state)

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)
        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer pipeline failed to enter PLAYING")
        self.control = self.webrtc.emit("create-data-channel", "control", None)
        if not self.control:
            raise RuntimeError("Could not create the WebRTC control data channel")
        self.control.connect("on-message-string", self._on_control_message)
        LOG.info("GStreamer pipeline started: %dx%d@%d, %d bps", c.width, c.height, c.fps, c.bitrate)

    def push_frame(self, data: bytes) -> None:
        with self._frame_ready:
            while len(self._frame_queue) >= 3 and not self._pacer_stopping:
                self._frame_ready.wait()
            if self._pacer_stopping:
                return
            self._frame_queue.append(data)
            self._frame_ready.notify()

    def _pace_frames(self) -> None:
        deadline = 0
        while True:
            with self._frame_ready:
                while not self._frame_queue and not self._pacer_stopping:
                    deadline = 0
                    self._frame_ready.wait()
                if self._pacer_stopping:
                    return
                if not deadline:
                    deadline = time.monotonic_ns() + self._frame_duration
                remaining = deadline - time.monotonic_ns()
                if remaining > 0:
                    self._frame_ready.wait(remaining / Gst.SECOND)
                    continue
                data = self._frame_queue.popleft()
                self._frame_ready.notify_all()

            self._emit_frame(data, max(0, deadline - self.started_ns))
            now = time.monotonic_ns()
            deadline += self._frame_duration
            if deadline < now - self._frame_duration:
                deadline = now + self._frame_duration

    def _emit_frame(self, data: bytes, pts: int) -> None:
        if not self.screen:
            return
        buffer = Gst.Buffer.new_allocate(None, len(data), None)
        buffer.fill(0, data)
        buffer.pts = buffer.dts = pts
        buffer.duration = self._frame_duration
        self.screen.emit("push-buffer", buffer)

    def close(self) -> None:
        with self._frame_ready:
            self._pacer_stopping = True
            self._frame_ready.notify_all()
        self._pacer.join(timeout=2)
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        self.pipeline = None
        self.webrtc = None
        self.screen = None
        self.control = None

    def create_offer(self, timeout: float = 12.0) -> str:
        if not self.webrtc:
            raise RuntimeError("WebRTC pipeline is not ready")
        if self._offer_event and not self._offer_event.is_set():
            raise RuntimeError("An offer is already being created")

        self._offer_sdp = None
        self._offer_event = threading.Event()
        GLib.idle_add(self._create_offer_on_main)
        if not self._offer_event.wait(timeout):
            raise TimeoutError("Timed out while gathering WebRTC ICE candidates")
        if not self._offer_sdp:
            raise RuntimeError("GStreamer did not create an SDP offer")
        return self._offer_sdp

    def _create_offer_on_main(self) -> bool:
        promise = Gst.Promise.new_with_change_func(self._on_offer_created, None, None)
        self.webrtc.emit("create-offer", None, promise)
        return GLib.SOURCE_REMOVE

    def _on_offer_created(self, promise, _user_data, _unused) -> None:
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        set_promise = Gst.Promise.new()
        self.webrtc.emit("set-local-description", offer, set_promise)
        set_promise.interrupt()
        if self.webrtc.get_property("ice-gathering-state") == GstWebRTC.WebRTCICEGatheringState.COMPLETE:
            self._finish_offer()

    def _on_ice_state(self, _webrtc, _param) -> None:
        if self.webrtc.get_property("ice-gathering-state") == GstWebRTC.WebRTCICEGatheringState.COMPLETE:
            self._finish_offer()

    def _finish_offer(self) -> None:
        if not self._offer_event or self._offer_event.is_set():
            return
        description = self.webrtc.get_property("local-description")
        if not description:
            return
        self._offer_sdp = description.sdp.as_text()
        self._offer_event.set()

    def set_answer(self, sdp_text: str) -> None:
        result, sdp = GstSdp.SDPMessage.new()
        if result != GstSdp.SDPResult.OK:
            raise RuntimeError("Could not allocate SDP message")
        result = GstSdp.sdp_message_parse_buffer(sdp_text.encode(), sdp)
        if result != GstSdp.SDPResult.OK:
            raise ValueError("Invalid SDP answer")
        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdp
        )
        promise = Gst.Promise.new_with_change_func(self._on_answer_set, None, None)
        self.webrtc.emit("set-remote-description", answer, promise)

    def _on_answer_set(self, _promise, _user_data, _unused) -> None:
        GLib.idle_add(self._open_video_gate)

    def _open_video_gate(self) -> bool:
        self.video_gate.set_property("drop", False)
        if self.audio_gate:
            self.audio_gate.set_property("drop", False)
        return GLib.SOURCE_REMOVE

    def _on_control_message(self, _channel, payload: str) -> None:
        if len(payload) > self.MAX_CONTROL_MESSAGE:
            return
        try:
            message = json.loads(payload)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict):
            return
        kind = message.get("t")
        if kind == "ping":
            self.send_control({"t": "pong", "at": message.get("at")})
            return
        if kind == "clipboard-set":
            text = message.get("text")
            if (
                not isinstance(text, str)
                or len(text.encode("utf-8")) > self.MAX_CLIPBOARD_BYTES
            ):
                self.send_control(
                    {
                        "t": "clipboard-set-result",
                        "ok": False,
                        "error": "Clipboard text is too large",
                    }
                )
                return
            threading.Thread(target=self._write_clipboard, args=(text,), daemon=True).start()
            return
        if kind == "clipboard-get":
            threading.Thread(target=self._read_clipboard, daemon=True).start()
            return
        GLib.idle_add(self.app.mutter.dispatch_input, message)

    def _write_clipboard(self, text: str) -> None:
        try:
            result = subprocess.run(
                ["wl-copy", "--type", "text/plain;charset=utf-8"],
                input=text.encode("utf-8"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"wl-copy exited with status {result.returncode}")
            reply = {"t": "clipboard-set-result", "ok": True}
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            reply = {"t": "clipboard-set-result", "ok": False, "error": str(error)[:160]}
        GLib.idle_add(self.send_control, reply)

    def _read_clipboard(self) -> None:
        try:
            result = subprocess.run(
                ["wl-paste", "--no-newline"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
            if len(result.stdout) > self.MAX_CLIPBOARD_BYTES:
                raise RuntimeError("Clipboard text is too large")
            reply = {
                "t": "clipboard-data",
                "ok": True,
                "text": result.stdout.decode("utf-8", "replace"),
            }
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as error:
            reply = {"t": "clipboard-data", "ok": False, "error": str(error)[:160]}
        GLib.idle_add(self.send_control, reply)

    def send_control(self, message: dict) -> None:
        if not self.control:
            return
        try:
            self.control.emit("send-string", json.dumps(message, separators=(",", ":")))
        except GLib.Error:
            pass

    def _on_connection_state(self, _webrtc, _param) -> None:
        state = self.webrtc.get_property("connection-state")
        LOG.info("WebRTC connection state: %s", state.value_nick)

    def _on_bus_message(self, _bus, message: Gst.Message) -> None:
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            if message.src.get_name().startswith("sctpenc") and "association went into error state" in debug:
                LOG.info("Browser data channel closed")
            else:
                LOG.error("GStreamer error from %s: %s (%s)", message.src.get_name(), error, debug)
        elif message.type == Gst.MessageType.WARNING:
            warning, debug = message.parse_warning()
            LOG.warning("GStreamer warning from %s: %s (%s)", message.src.get_name(), warning, debug)


class NativeEncoder:
    MAX_PACKET = 16 * 1024 * 1024

    def __init__(self, app: "Application", node_id: int):
        self.app = app
        self.stopping = False
        read_fd, write_fd = os.pipe()
        command = [
            str(app.config.encoder_bridge),
            str(node_id),
            str(app.config.width),
            str(app.config.height),
            str(app.config.fps),
            str(app.config.bitrate),
            str(write_fd),
        ]
        try:
            self.process = subprocess.Popen(command, pass_fds=(write_fd,))
        except Exception:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        self.output = os.fdopen(read_fd, "rb", buffering=0)
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def _read_loop(self) -> None:
        try:
            while not self.stopping:
                size = struct.unpack(">I", self._read_exact(4))[0]
                if not 0 < size <= self.MAX_PACKET:
                    raise RuntimeError(f"invalid encoded frame size: {size}")
                self.app.push_frame(self._read_exact(size))
        except (EOFError, OSError, RuntimeError) as error:
            if not self.stopping:
                LOG.error("DMA-BUF encoder stopped: %s", error)
                os._exit(1)

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.output.read(size - len(data))
            if not chunk:
                raise EOFError("unexpected end of encoder output")
            data.extend(chunk)
        return bytes(data)

    def stop(self) -> None:
        self.stopping = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.output.close()
        self.thread.join(timeout=1)


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "rkwebscr/0.3.4"

    @property
    def app(self) -> "Application":
        return self.server.app

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            return self._json(HTTPStatus.OK, self.app.status())
        self._serve_static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/offer":
                offer = self.app.create_offer()
                return self._json(HTTPStatus.OK, {"type": "offer", "sdp": offer})
            if path == "/api/answer":
                body = self._read_json()
                if body.get("type") != "answer" or not isinstance(body.get("sdp"), str):
                    raise ValueError("Expected an SDP answer")
                self.app.set_answer(body["sdp"])
                return self._json(HTTPStatus.OK, {"ok": True})
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except TimeoutError as error:
            self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": str(error)})
        except (RuntimeError, ValueError) as error:
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})

    def _read_json(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 2_000_000:
            raise ValueError("Invalid request size")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    def _json(self, status: HTTPStatus, value: dict) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, request_path: str) -> None:
        request_path = unquote(request_path)
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        root = self.app.web_root.resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else mime)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        LOG.debug("HTTP %s - %s", self.address_string(), fmt % args)


class Config:
    def __init__(self, args):
        self.bind = args.bind
        self.port = args.port
        self.width = args.width
        self.height = args.height
        self.fps = args.fps
        self.bitrate = args.bitrate
        self.audio_target = args.audio_target
        self.audio = not args.no_audio
        self.encoder_bridge = Path(args.encoder_bridge).expanduser()


class Application:
    def __init__(self, args):
        self.config = Config(args)
        self.web_root = Path(args.web_root)
        self.mutter = MutterSession(args.width, args.height)
        self.webrtc: WebRTCSession | None = None
        self.encoder: NativeEncoder | None = None
        self.httpd: ThreadingHTTPServer | None = None
        self._node_id: int | None = None

    def start(self) -> None:
        self.httpd = ThreadingHTTPServer(
            (self.config.bind, self.config.port), RequestHandler
        )
        self.httpd.app = self
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        LOG.info("Control URL: http://%s:%d/", self.config.bind, self.config.port)
        self.mutter.start(self._on_pipewire_node)

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
        if self.encoder:
            self.encoder.stop()
        if self.webrtc:
            self.webrtc.close()
        self.mutter.stop()

    def _on_pipewire_node(self, node_id: int) -> None:
        self._node_id = node_id
        if self.encoder:
            self.encoder.stop()
        if self.webrtc:
            self.webrtc.close()
        self.webrtc = WebRTCSession(self, node_id)
        self.encoder = NativeEncoder(self, node_id)

    def push_frame(self, data: bytes) -> None:
        session = self.webrtc
        if session:
            session.push_frame(data)

    def status(self) -> dict:
        return {
            "ready": self.webrtc is not None and self.encoder is not None and self.encoder.alive,
            "device": "Rock5B",
            "video": {
                "codec": "H.264 (MPP)",
                "width": self.config.width,
                "height": self.config.height,
                "fps": self.config.fps,
                "bitrate": self.config.bitrate,
            },
            "audio": {"enabled": self.config.audio, "codec": "Opus", "rate": 48000, "channels": 2},
            "transport": "WebRTC",
        }

    def create_offer(self) -> str:
        if not self.webrtc:
            raise RuntimeError("Screen capture is still starting")
        if self.webrtc._offer_sdp is not None:
            self.webrtc.close()
            self.webrtc = WebRTCSession(self, self._node_id)
        return self.webrtc.create_offer()

    def set_answer(self, answer: str) -> None:
        if not self.webrtc:
            raise RuntimeError("Screen capture is not ready")
        GLib.idle_add(self.webrtc.set_answer, answer)


def parse_args():
    root = Path(__file__).resolve().parents[1]
    installed_web = Path("/usr/share/rkwebscr/web")
    installed_encoder = Path("/usr/lib/rkwebscr/rkwebscr-dmabuf-encoder")
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--bitrate", type=int, default=12_000_000)
    parser.add_argument("--audio-target", default="")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument(
        "--web-root",
        default=str(installed_web if installed_web.is_dir() else root / "web"),
    )
    parser.add_argument(
        "--encoder-bridge",
        default=str(
            installed_encoder
            if installed_encoder.is_file()
            else root / "native" / "rkwebscr-dmabuf-encoder"
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not (96 <= args.width <= 7680 and 64 <= args.height <= 4320):
        parser.error("unsupported video dimensions")
    if not 1 <= args.fps <= 120:
        parser.error("fps must be between 1 and 120")
    if not 100_000 <= args.bitrate <= 100_000_000:
        parser.error("bitrate is outside the supported range")
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    Gst.init(None)
    app = Application(args)
    loop = GLib.MainLoop()

    def shutdown(*_args):
        if app.encoder:
            app.encoder.stopping = True
        GLib.idle_add(loop.quit)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        app.start()
        loop.run()
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

# pyright: reportMissingImports=false, reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import asyncio
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction
from threading import Lock
from typing import Any

import numpy as np

from .frame_store import FrameStore

try:
    from aiortc import (
        MediaStreamTrack as AiortcMediaStreamTrack,
        RTCPeerConnection as AiortcRTCPeerConnection,
        RTCRtpSender as AiortcRTCRtpSender,
        RTCSessionDescription as AiortcRTCSessionDescription,
    )
    from av import VideoFrame as AvVideoFrame

    WEBRTC_IMPORT_ERROR: Exception | None = None
except Exception as error:  # pragma: no cover - import availability is environment specific
    WEBRTC_IMPORT_ERROR = error

    AiortcMediaStreamTrack = Any
    AiortcRTCPeerConnection = Any
    AiortcRTCRtpSender = Any
    AiortcRTCSessionDescription = Any
    AvVideoFrame = Any


if WEBRTC_IMPORT_ERROR is None:
    MediaStreamTrackBase = AiortcMediaStreamTrack  # type: ignore[assignment]
    RTCPeerConnectionType = AiortcRTCPeerConnection  # type: ignore[assignment]
    RTCRtpSenderType = AiortcRTCRtpSender  # type: ignore[assignment]
    RTCSessionDescriptionType = AiortcRTCSessionDescription  # type: ignore[assignment]
    VideoFrameType = AvVideoFrame  # type: ignore[assignment]
else:

    class MediaStreamTrackBase:
        kind = "video"

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    class RTCPeerConnectionType:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("aiortc dependency is required for WebRTC runtime") from WEBRTC_IMPORT_ERROR

    class RTCSessionDescriptionType:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("aiortc dependency is required for WebRTC runtime") from WEBRTC_IMPORT_ERROR

    class RTCRtpSenderType:
        @staticmethod
        def getCapabilities(_kind: str) -> Any:
            return type("_Capabilities", (), {"codecs": []})()

    class VideoFrameType:
        @staticmethod
        def from_ndarray(*_args: object, **_kwargs: object) -> Any:
            raise RuntimeError("av dependency is required for WebRTC runtime") from WEBRTC_IMPORT_ERROR


class SessionVideoTrack(MediaStreamTrackBase):
    kind = "video"

    def __init__(self, target_fps: int, width: int, height: int) -> None:
        if WEBRTC_IMPORT_ERROR is not None:
            raise RuntimeError("aiortc dependency is required for WebRTC runtime") from WEBRTC_IMPORT_ERROR

        super().__init__()
        self._target_fps = max(1, target_fps)
        self._clock_hz = 90_000
        self._clock_tick = max(1, int(self._clock_hz / self._target_fps))
        self._timestamp = 0
        self._time_base = Fraction(1, self._clock_hz)
        self._fallback_frame = np.zeros((max(1, height), max(1, width), 3), dtype=np.uint8)
        self._latest_frame = self._fallback_frame
        self._pending_frame: np.ndarray[Any, Any] | None = None
        self._pending_lock = Lock()
        self._frames_emitted = 0

    def push_frame(self, frame: Any) -> bool:
        frame_array = np.asarray(frame)
        if frame_array.ndim != 3 or frame_array.shape[2] != 3:
            return False

        candidate = frame_array.copy()
        dropped = False
        with self._pending_lock:
            if self._pending_frame is not None:
                dropped = True
            self._pending_frame = candidate
        return dropped

    @property
    def frames_emitted(self) -> int:
        return self._frames_emitted

    async def recv(self) -> Any:
        deadline = time.perf_counter() + (1.0 / self._target_fps)
        frame_to_send: np.ndarray[Any, Any] | None = None

        while frame_to_send is None:
            with self._pending_lock:
                frame_to_send = self._pending_frame
                self._pending_frame = None

            if frame_to_send is not None:
                break

            if time.perf_counter() >= deadline:
                break
            await asyncio.sleep(0.004)

        if frame_to_send is None:
            frame_to_send = self._latest_frame
        else:
            self._latest_frame = frame_to_send

        frame = VideoFrameType.from_ndarray(frame_to_send, format="rgb24")
        frame.pts = self._timestamp
        frame.time_base = self._time_base
        self._timestamp += self._clock_tick
        self._frames_emitted += 1
        return frame


@dataclass
class WebRtcSession:
    peer_id: str
    pc: Any
    track: SessionVideoTrack
    width: int
    height: int
    created_at_ms: float


class WebRtcSessionManager:
    def __init__(
        self,
        frame_store: FrameStore,
        logger: Any,
        target_fps: int,
        default_width: int,
        default_height: int,
        max_sessions: int,
    ) -> None:
        self._frame_store = frame_store
        self._logger = logger
        self._target_fps = max(1, target_fps)
        self._default_width = max(1, default_width)
        self._default_height = max(1, default_height)
        self._max_sessions = max(1, max_sessions)
        self._sessions: dict[str, WebRtcSession] = {}
        self._sessions_lock = Lock()
        self._frames_pushed = 0
        self._track_drop_count = 0
        self._historical_track_frames_emitted = 0

    @staticmethod
    def ensure_available() -> None:
        if WEBRTC_IMPORT_ERROR is not None:
            raise RuntimeError(
                "WebRTC dependencies are missing. Install backend requirements to enable /webrtc endpoints."
            ) from WEBRTC_IMPORT_ERROR

    def active_session_count(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def push_frame(self, frame: Any) -> None:
        sessions = self._session_snapshot()
        if not sessions:
            return

        pushed_count = 0
        dropped_count = 0
        for session in sessions:
            pushed_count += 1
            if session.track.push_frame(frame):
                dropped_count += 1

        with self._sessions_lock:
            self._frames_pushed += pushed_count
            self._track_drop_count += dropped_count

    def stats(self) -> dict[str, object]:
        sessions = self._session_snapshot()
        track_frames_emitted_live = sum(session.track.frames_emitted for session in sessions)

        with self._sessions_lock:
            frames_pushed = self._frames_pushed
            track_drop_count = self._track_drop_count
            historical_track_frames_emitted = self._historical_track_frames_emitted

        return {
            "active_sessions": len(sessions),
            "media_pipeline": "aiortc_track",
            "preferred_codec": "h264",
            "frames_pushed": frames_pushed,
            "track_drop_count": track_drop_count,
            "track_frames_emitted_live": track_frames_emitted_live,
            "track_frames_emitted_total": historical_track_frames_emitted + track_frames_emitted_live,
            # Deprecated fields kept for compatibility with older health parsers.
            "encoder_drop_count": 0,
            "ffmpeg_frames_written_total": 0,
            "ffmpeg_restart_count_total": 0,
            "ffmpeg_restart_failures_total": 0,
            "ffmpeg_enabled": False,
        }

    async def create_answer(self, offer_sdp: str, offer_type: str, peer_id: str | None = None) -> dict[str, str]:
        self.ensure_available()

        if offer_type != "offer":
            raise ValueError("WebRTC signaling only accepts offer type")

        selected_peer_id = peer_id.strip() if peer_id else uuid.uuid4().hex
        if not selected_peer_id:
            selected_peer_id = uuid.uuid4().hex

        snapshot = self._frame_store.snapshot()
        width = snapshot.width if snapshot.width > 0 else self._default_width
        height = snapshot.height if snapshot.height > 0 else self._default_height

        pc = RTCPeerConnectionType()
        track = SessionVideoTrack(
            target_fps=self._target_fps,
            width=width,
            height=height,
        )
        transceiver = pc.addTransceiver(track, direction="sendonly")
        self._prefer_h264(transceiver)

        stale_sessions: list[WebRtcSession] = []
        with self._sessions_lock:
            existing = self._sessions.pop(selected_peer_id, None)
            if existing is not None:
                stale_sessions.append(existing)

            if len(self._sessions) >= self._max_sessions:
                oldest = min(self._sessions.values(), key=lambda session: session.created_at_ms)
                stale = self._sessions.pop(oldest.peer_id)
                stale_sessions.append(stale)
                self._logger.info("webrtc removed_stale_peer peer_id=%s", stale.peer_id)

            self._sessions[selected_peer_id] = WebRtcSession(
                peer_id=selected_peer_id,
                pc=pc,
                track=track,
                width=width,
                height=height,
                created_at_ms=time.time() * 1000.0,
            )

        for stale in stale_sessions:
            await self._close_session_resources(stale)

        @pc.on("connectionstatechange")
        def on_connection_state_change() -> None:
            state = pc.connectionState
            if state in {"failed", "closed", "disconnected"}:
                asyncio.create_task(self.close_session(selected_peer_id))

        try:
            await pc.setRemoteDescription(RTCSessionDescriptionType(sdp=offer_sdp, type=offer_type))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            await self._wait_for_ice_gathering_complete(pc)
            local_description = pc.localDescription
            if local_description is None:
                raise RuntimeError("WebRTC local description is unavailable")

            self._logger.info("webrtc session_ready peer_id=%s", selected_peer_id)
            return {
                "peerId": selected_peer_id,
                "sdp": local_description.sdp,
                "type": local_description.type,
            }
        except Exception:
            await self.close_session(selected_peer_id)
            raise

    async def close_session(self, peer_id: str) -> bool:
        with self._sessions_lock:
            session = self._sessions.pop(peer_id, None)

        if session is None:
            return False

        await self._close_session_resources(session)
        self._logger.info("webrtc session_closed peer_id=%s", peer_id)
        return True

    async def close_all(self) -> None:
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            await self._close_session_resources(session)

    async def _close_session_resources(self, session: WebRtcSession) -> None:
        emitted = session.track.frames_emitted
        await session.pc.close()
        with self._sessions_lock:
            self._historical_track_frames_emitted += emitted

    def _session_snapshot(self) -> list[WebRtcSession]:
        with self._sessions_lock:
            return list(self._sessions.values())

    @staticmethod
    def _prefer_h264(transceiver: Any) -> None:
        try:
            capabilities = RTCRtpSenderType.getCapabilities("video")
        except Exception:
            return

        codecs = [codec for codec in capabilities.codecs if str(codec.mimeType).lower() == "video/h264"]
        if codecs:
            transceiver.setCodecPreferences(codecs)

    @staticmethod
    async def _wait_for_ice_gathering_complete(pc: Any, timeout_s: float = 2.0) -> None:
        if pc.iceGatheringState == "complete":
            return

        complete_event = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def on_ice_state_change() -> None:
            if pc.iceGatheringState == "complete":
                complete_event.set()

        try:
            await asyncio.wait_for(complete_event.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return

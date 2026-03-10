from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse

from .frame_store import FrameStore
from .metadata_source import build_overlay_payload_with_frame_context
from .telemetry import DeliveryTelemetry

PROFILE_PRESETS = {
    "low": {"width": 960, "height": 540, "fps": 24, "jpeg_quality": 70},
    "balanced": {"width": 1280, "height": 720, "fps": 30, "jpeg_quality": 75},
    "high": {"width": 1920, "height": 1080, "fps": 30, "jpeg_quality": 82},
}


@dataclass(frozen=True)
class AppSettings:
    stream_profile: str
    frame_width: int
    frame_height: int
    stream_fps: int
    jpeg_quality: int
    metadata_fps: int
    metrics_log_interval_sec: int
    log_level: str

    @classmethod
    def from_env(cls) -> AppSettings:
        stream_profile = os.getenv("STREAM_PROFILE", "balanced").strip().lower()
        if stream_profile not in PROFILE_PRESETS:
            stream_profile = "balanced"

        profile = PROFILE_PRESETS[stream_profile]

        return cls(
            stream_profile=stream_profile,
            frame_width=max(160, int(os.getenv("FRAME_WIDTH", str(profile["width"])))),
            frame_height=max(120, int(os.getenv("FRAME_HEIGHT", str(profile["height"])))),
            stream_fps=max(1, min(60, int(os.getenv("STREAM_FPS", str(profile["fps"]))))),
            jpeg_quality=max(40, min(95, int(os.getenv("JPEG_QUALITY", str(profile["jpeg_quality"]))))),
            metadata_fps=max(1, min(30, int(os.getenv("METADATA_FPS", "10")))),
            metrics_log_interval_sec=max(0, int(os.getenv("METRICS_LOG_INTERVAL_SEC", "10"))),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )


class PluginRuntime:
    def __init__(
        self,
        settings: AppSettings,
        frame_store: FrameStore | None = None,
        telemetry: DeliveryTelemetry | None = None,
        logger_name: str = "video_server.backend",
    ) -> None:
        logging.basicConfig(
            level=getattr(logging, settings.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )

        self.settings = settings
        self.frame_store = frame_store if frame_store is not None else FrameStore(jpeg_quality=settings.jpeg_quality)
        self.telemetry = (
            telemetry if telemetry is not None else DeliveryTelemetry(metadata_target_fps=settings.metadata_fps)
        )
        self.logger = logging.getLogger(logger_name)
        self.last_metrics_log_time = 0.0

    def on_camera_frame(
        self,
        frame: Any,
        seq: int | None = None,
        timestamp_ms: float | None = None,
        stream_id: str = "default",
    ) -> int:
        return self.ingest_frame(frame=frame, seq=seq, timestamp_ms=timestamp_ms, stream_id=stream_id)

    def ingest_frame(
        self,
        frame: Any,
        seq: int | None = None,
        timestamp_ms: float | None = None,
        stream_id: str = "default",
    ) -> int:
        previous_snapshot = self.frame_store.snapshot()
        next_seq = self.frame_store.ingest_frame(
            frame=frame,
            seq=seq,
            timestamp_ms=timestamp_ms,
            stream_id=stream_id,
        )

        if next_seq <= previous_snapshot.seq:
            self.logger.warning(
                "non_monotonic_seq stream_id=%s provided=%s current=%s",
                stream_id,
                next_seq,
                previous_snapshot.seq,
            )

        self.maybe_log_metrics(time.perf_counter())
        return next_seq

    def maybe_log_metrics(self, now: float) -> None:
        if self.settings.metrics_log_interval_sec <= 0:
            return

        if now - self.last_metrics_log_time < self.settings.metrics_log_interval_sec:
            return

        self.last_metrics_log_time = now

        snapshot = self.frame_store.snapshot()
        frame_seq = snapshot.seq
        frame_time = snapshot.perf_time
        capture_fps = snapshot.capture_fps_estimate
        delivery = self.telemetry.snapshot()
        snapshot_delivery = delivery["snapshot"]
        mjpeg = delivery["mjpeg"]
        metadata = delivery["metadata"]

        frame_age_ms = 0.0
        if frame_time > 0:
            frame_age_ms = max(0.0, (time.perf_counter() - frame_time) * 1000.0)

        read_ms = snapshot.capture_read_ms_estimate
        encode_ms = snapshot.capture_encode_ms_estimate

        self.logger.info(
            "metrics frame_seq=%s capture_fps=%.2f frame_age_ms=%.2f capture_read_ms=%.2f capture_encode_ms=%.2f snapshot_mbps=%.2f mjpeg_mbps=%.2f metadata_mbps=%.2f overlay_lag_proxy_ms=%.2f",
            frame_seq,
            capture_fps,
            frame_age_ms,
            read_ms,
            encode_ms,
            float(snapshot_delivery["throughput_mbps_estimate"]),
            float(mjpeg["throughput_mbps_estimate"]),
            float(metadata["throughput_mbps_estimate"]),
            float(metadata["overlay_lag_proxy_ms_estimate"]),
        )

    def health_payload(self) -> dict[str, object]:
        frame_snapshot = self.frame_store.snapshot()
        frame_seq = frame_snapshot.seq
        frame_time = frame_snapshot.perf_time
        capture_fps = frame_snapshot.capture_fps_estimate
        frame_timestamp_ms = frame_snapshot.timestamp_ms
        width = frame_snapshot.width
        height = frame_snapshot.height

        delivery = self.telemetry.snapshot()
        has_frame = frame_timestamp_ms > 0 and width > 0 and height > 0

        read_ms = frame_snapshot.capture_read_ms_estimate
        encode_ms = frame_snapshot.capture_encode_ms_estimate

        metadata_delivery = delivery["metadata"]

        frame_age_ms = None
        if frame_time > 0:
            frame_age_ms = max(0.0, (time.perf_counter() - frame_time) * 1000.0)

        return {
            "status": "ok" if has_frame else "waiting_for_frames",
            "stream_profile": self.settings.stream_profile,
            "profile_presets": PROFILE_PRESETS,
            "camera_source": {
                "requested_source": "external_callback",
                "active_source": "external_callback",
                "stream_id": frame_snapshot.stream_id,
            },
            "frame_shape": [height, width, 3] if has_frame else None,
            "stream_fps_target": self.settings.stream_fps,
            "jpeg_quality": self.settings.jpeg_quality,
            "frame_ready": has_frame,
            "capture_fps_estimate": round(capture_fps, 2),
            "capture_read_ms_estimate": round(read_ms, 2),
            "capture_encode_ms_estimate": round(encode_ms, 2),
            "delivery": delivery,
            "overlay_lag_proxy_ms_estimate": metadata_delivery["overlay_lag_proxy_ms_estimate"],
            "latest_frame_seq": frame_seq,
            "latest_frame_timestamp_ms": round(frame_timestamp_ms, 2) if frame_timestamp_ms > 0 else None,
            "latest_frame_age_ms": None if frame_age_ms is None else round(frame_age_ms, 2),
        }

    async def mjpeg_bytes(self, stream_fps: int) -> AsyncGenerator[bytes, None]:
        frame_interval = 1.0 / stream_fps
        self.telemetry.add_mjpeg_client()

        try:
            while True:
                start = time.perf_counter()
                payload = self.frame_store.snapshot().jpeg
                if payload is None:
                    await asyncio.sleep(0.02)
                    continue

                self.telemetry.update_mjpeg(now=time.perf_counter(), payload_bytes=len(payload))

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
                    + payload
                    + b"\r\n"
                )

                elapsed = time.perf_counter() - start
                await asyncio.sleep(max(0.0, frame_interval - elapsed))
        finally:
            self.telemetry.remove_mjpeg_client()

    async def metadata_loop(self, websocket: WebSocket) -> None:
        await websocket.accept()
        frame_interval = 1.0 / self.settings.metadata_fps
        self.telemetry.add_metadata_client()

        try:
            while True:
                start = time.perf_counter()
                snapshot = self.frame_store.snapshot()

                if snapshot.timestamp_ms <= 0 or snapshot.width <= 0 or snapshot.height <= 0:
                    await asyncio.sleep(0.02)
                    continue

                reference_timestamp_ms = snapshot.timestamp_ms
                server_timestamp_ms = time.time() * 1000.0

                payload = build_overlay_payload_with_frame_context(
                    timestamp_ms=reference_timestamp_ms,
                    source_width=snapshot.width,
                    source_height=snapshot.height,
                    frame_seq=snapshot.seq,
                    server_timestamp_ms=server_timestamp_ms,
                )
                await websocket.send_json(payload)
                payload_size = len(json.dumps(payload, separators=(",", ":")))
                self.telemetry.update_metadata(
                    now=time.perf_counter(),
                    payload_bytes=payload_size,
                    frame_skew_ms=max(0.0, server_timestamp_ms - reference_timestamp_ms),
                )

                elapsed = time.perf_counter() - start
                await asyncio.sleep(max(0.0, frame_interval - elapsed))
        except WebSocketDisconnect:
            pass
        finally:
            self.telemetry.remove_metadata_client()


def create_app(runtime: PluginRuntime | None = None, settings: AppSettings | None = None) -> FastAPI:
    active_settings = settings if settings is not None else AppSettings.from_env()
    active_runtime = runtime if runtime is not None else PluginRuntime(active_settings)

    app = FastAPI(title="Video Server Backend", version="0.1.0")
    app.state.runtime = active_runtime
    app.state.settings = active_settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def startup_plugin_runtime() -> None:
        active_runtime.logger.info(
            "startup mode=plugin-callback profile=%s default_width=%s default_height=%s stream_fps=%s metadata_fps=%s",
            active_settings.stream_profile,
            active_settings.frame_width,
            active_settings.frame_height,
            active_settings.stream_fps,
            active_settings.metadata_fps,
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        return active_runtime.health_payload()

    @app.get("/frame.jpg")
    def frame_jpeg() -> Response:
        snapshot = active_runtime.frame_store.snapshot()
        payload = snapshot.jpeg
        if payload is None:
            raise HTTPException(
                status_code=503,
                detail="No frames ingested yet. External owner must call on_camera_frame().",
            )

        active_runtime.telemetry.update_snapshot(now=time.perf_counter(), payload_bytes=len(payload))

        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "X-Frame-Seq": str(snapshot.seq),
                "X-Frame-Timestamp-Ms": f"{snapshot.timestamp_ms:.2f}",
            },
        )

    @app.get("/stream.mjpeg")
    async def stream_mjpeg(fps: int | None = None) -> StreamingResponse:
        stream_fps = active_settings.stream_fps if fps is None else max(1, min(60, fps))

        return StreamingResponse(
            active_runtime.mjpeg_bytes(stream_fps),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
        )

    @app.websocket("/ws/metadata")
    async def websocket_metadata(websocket: WebSocket) -> None:
        await active_runtime.metadata_loop(websocket)

    @app.on_event("shutdown")
    def shutdown_plugin_runtime() -> None:
        active_runtime.logger.info("shutdown mode=plugin-callback")

    return app


def get_runtime(target_app: FastAPI) -> PluginRuntime:
    runtime = getattr(target_app.state, "runtime", None)
    if not isinstance(runtime, PluginRuntime):
        raise RuntimeError("FastAPI app is missing PluginRuntime in app.state.runtime")
    return runtime


_default_settings = AppSettings.from_env()
_default_runtime = PluginRuntime(_default_settings)
app = create_app(runtime=_default_runtime, settings=_default_settings)


def on_camera_frame(
    frame: Any,
    seq: int | None = None,
    timestamp_ms: float | None = None,
    stream_id: str = "default",
) -> int:
    return _default_runtime.on_camera_frame(frame=frame, seq=seq, timestamp_ms=timestamp_ms, stream_id=stream_id)

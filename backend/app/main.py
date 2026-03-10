from __future__ import annotations

import asyncio
import io
import os
from threading import Event, Lock, Thread
import time
from typing import AsyncGenerator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from PIL import Image

from .frame_source import build_frame_source
from .metadata_source import build_overlay_payload_with_frame_context

PROFILE_PRESETS = {
    "low": {"width": 960, "height": 540, "fps": 24, "jpeg_quality": 70},
    "balanced": {"width": 1280, "height": 720, "fps": 30, "jpeg_quality": 75},
    "high": {"width": 1920, "height": 1080, "fps": 30, "jpeg_quality": 82},
}

STREAM_PROFILE = os.getenv("STREAM_PROFILE", "balanced").strip().lower()
if STREAM_PROFILE not in PROFILE_PRESETS:
    STREAM_PROFILE = "balanced"

profile = PROFILE_PRESETS[STREAM_PROFILE]

FRAME_WIDTH = max(160, int(os.getenv("FRAME_WIDTH", str(profile["width"]))))
FRAME_HEIGHT = max(120, int(os.getenv("FRAME_HEIGHT", str(profile["height"]))))
STREAM_FPS = max(1, min(60, int(os.getenv("STREAM_FPS", str(profile["fps"])))) )
JPEG_QUALITY = max(40, min(95, int(os.getenv("JPEG_QUALITY", str(profile["jpeg_quality"])))))
CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "auto")
CAMERA_INDEX = int(os.getenv("CAMERA_INDEX", "0"))
RANDOM_SEED = int(os.getenv("RANDOM_SEED", "7"))
METADATA_FPS = max(1, min(30, int(os.getenv("METADATA_FPS", "10"))))

app = FastAPI(title="Video Server Backend", version="0.1.0")

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

frame_source, frame_source_info = build_frame_source(
    source=CAMERA_SOURCE,
    width=FRAME_WIDTH,
    height=FRAME_HEIGHT,
    camera_index=CAMERA_INDEX,
    seed=RANDOM_SEED,
)
frame_lock = Lock()
source_state_lock = Lock()
source_stop_event = Event()
source_thread: Thread | None = None
telemetry_lock = Lock()

latest_jpeg: bytes | None = None
latest_frame_seq = 0
latest_frame_time = 0.0
latest_frame_timestamp_ms = 0.0
capture_fps_ema = 0.0

snapshot_delivery_count = 0
snapshot_delivery_time = 0.0
snapshot_delivery_fps_ema = 0.0

mjpeg_delivery_count = 0
mjpeg_delivery_time = 0.0
mjpeg_delivery_fps_ema = 0.0
mjpeg_active_clients = 0

metadata_message_count = 0
metadata_message_time = 0.0
metadata_message_fps_ema = 0.0
metadata_active_clients = 0


def read_frame():
    with frame_lock:
        return frame_source.next_frame()


def encode_jpeg(frame) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(frame, mode="RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


def _capture_loop() -> None:
    global latest_jpeg, latest_frame_seq, latest_frame_time, latest_frame_timestamp_ms, capture_fps_ema

    while not source_stop_event.is_set():
        try:
            frame = read_frame()
            payload = encode_jpeg(frame)
            now = time.perf_counter()

            with source_state_lock:
                if latest_frame_time > 0:
                    instantaneous_fps = 1.0 / max(0.001, now - latest_frame_time)
                    capture_fps_ema = (
                        instantaneous_fps
                        if capture_fps_ema == 0
                        else capture_fps_ema * 0.85 + instantaneous_fps * 0.15
                    )

                latest_jpeg = payload
                latest_frame_seq += 1
                latest_frame_time = now
                latest_frame_timestamp_ms = time.time() * 1000.0
        except RuntimeError:
            time.sleep(0.02)
            continue

        if frame_source_info.get("active_source") == "random":
            time.sleep(1.0 / STREAM_FPS)


def get_latest_jpeg() -> tuple[bytes | None, int, float, float, float]:
    with source_state_lock:
        return (
            latest_jpeg,
            latest_frame_seq,
            latest_frame_time,
            capture_fps_ema,
            latest_frame_timestamp_ms,
        )


def update_delivery_telemetry(kind: str, now: float) -> None:
    global snapshot_delivery_count, snapshot_delivery_time, snapshot_delivery_fps_ema
    global mjpeg_delivery_count, mjpeg_delivery_time, mjpeg_delivery_fps_ema

    with telemetry_lock:
        if kind == "snapshot":
            if snapshot_delivery_time > 0:
                instantaneous_fps = 1.0 / max(0.001, now - snapshot_delivery_time)
                snapshot_delivery_fps_ema = (
                    instantaneous_fps
                    if snapshot_delivery_fps_ema == 0
                    else snapshot_delivery_fps_ema * 0.85 + instantaneous_fps * 0.15
                )

            snapshot_delivery_count += 1
            snapshot_delivery_time = now
            return

        if mjpeg_delivery_time > 0:
            instantaneous_fps = 1.0 / max(0.001, now - mjpeg_delivery_time)
            mjpeg_delivery_fps_ema = (
                instantaneous_fps
                if mjpeg_delivery_fps_ema == 0
                else mjpeg_delivery_fps_ema * 0.85 + instantaneous_fps * 0.15
            )

        mjpeg_delivery_count += 1
        mjpeg_delivery_time = now


def update_metadata_telemetry(now: float) -> None:
    global metadata_message_count, metadata_message_time, metadata_message_fps_ema

    with telemetry_lock:
        if metadata_message_time > 0:
            instantaneous_fps = 1.0 / max(0.001, now - metadata_message_time)
            metadata_message_fps_ema = (
                instantaneous_fps
                if metadata_message_fps_ema == 0
                else metadata_message_fps_ema * 0.85 + instantaneous_fps * 0.15
            )

        metadata_message_count += 1
        metadata_message_time = now


def get_delivery_telemetry() -> dict[str, object]:
    with telemetry_lock:
        return {
            "snapshot": {
                "count": snapshot_delivery_count,
                "fps_estimate": round(snapshot_delivery_fps_ema, 2),
            },
            "mjpeg": {
                "count": mjpeg_delivery_count,
                "fps_estimate": round(mjpeg_delivery_fps_ema, 2),
                "active_clients": mjpeg_active_clients,
            },
            "metadata": {
                "count": metadata_message_count,
                "fps_estimate": round(metadata_message_fps_ema, 2),
                "active_clients": metadata_active_clients,
                "target_fps": METADATA_FPS,
            },
        }


@app.on_event("startup")
def start_frame_capture() -> None:
    global source_thread

    source_stop_event.clear()
    source_thread = Thread(target=_capture_loop, daemon=True)
    source_thread.start()


@app.get("/health")
def health() -> dict[str, object]:
    _, frame_seq, frame_time, capture_fps, frame_timestamp_ms = get_latest_jpeg()
    delivery = get_delivery_telemetry()
    frame_age_ms = None
    if frame_time > 0:
        frame_age_ms = max(0.0, (time.perf_counter() - frame_time) * 1000.0)

    return {
        "status": "ok",
        "stream_profile": STREAM_PROFILE,
        "profile_presets": PROFILE_PRESETS,
        "camera_source": frame_source_info,
        "frame_shape": [FRAME_HEIGHT, FRAME_WIDTH, 3],
        "stream_fps_target": STREAM_FPS,
        "jpeg_quality": JPEG_QUALITY,
        "capture_fps_estimate": round(capture_fps, 2),
        "delivery": delivery,
        "latest_frame_seq": frame_seq,
        "latest_frame_timestamp_ms": round(frame_timestamp_ms, 2) if frame_timestamp_ms > 0 else None,
        "latest_frame_age_ms": None if frame_age_ms is None else round(frame_age_ms, 2),
    }


@app.get("/frame.jpg")
def frame_jpeg() -> Response:
    payload, frame_seq, _, _, frame_timestamp_ms = get_latest_jpeg()
    if payload is None:
        frame = read_frame()
        payload = encode_jpeg(frame)
        frame_timestamp_ms = time.time() * 1000.0

    update_delivery_telemetry("snapshot", time.perf_counter())

    return Response(
        content=payload,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "X-Frame-Seq": str(frame_seq),
            "X-Frame-Timestamp-Ms": f"{frame_timestamp_ms:.2f}",
        },
    )


async def mjpeg_bytes(stream_fps: int) -> AsyncGenerator[bytes, None]:
    global mjpeg_active_clients

    frame_interval = 1.0 / stream_fps

    with telemetry_lock:
        mjpeg_active_clients += 1

    try:
        while True:
            start = time.perf_counter()
            payload, _, _, _, _ = get_latest_jpeg()
            if payload is None:
                await asyncio.sleep(0.02)
                continue

            update_delivery_telemetry("mjpeg", time.perf_counter())

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
        with telemetry_lock:
            mjpeg_active_clients = max(0, mjpeg_active_clients - 1)


@app.get("/stream.mjpeg")
async def stream_mjpeg(fps: int | None = None) -> StreamingResponse:
    stream_fps = STREAM_FPS if fps is None else max(1, min(60, fps))

    return StreamingResponse(
        mjpeg_bytes(stream_fps),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.websocket("/ws/metadata")
async def websocket_metadata(websocket: WebSocket) -> None:
    global metadata_active_clients

    await websocket.accept()
    frame_interval = 1.0 / METADATA_FPS

    with telemetry_lock:
        metadata_active_clients += 1

    try:
        while True:
            start = time.perf_counter()
            _, frame_seq, _, _, frame_timestamp_ms = get_latest_jpeg()
            reference_timestamp_ms = (
                frame_timestamp_ms if frame_timestamp_ms > 0 else time.time() * 1000.0
            )

            payload = build_overlay_payload_with_frame_context(
                timestamp_ms=reference_timestamp_ms,
                source_width=FRAME_WIDTH,
                source_height=FRAME_HEIGHT,
                frame_seq=frame_seq,
                server_timestamp_ms=time.time() * 1000.0,
            )
            await websocket.send_json(payload)
            update_metadata_telemetry(time.perf_counter())

            elapsed = time.perf_counter() - start
            await asyncio.sleep(max(0.0, frame_interval - elapsed))
    except WebSocketDisconnect:
        pass
    finally:
        with telemetry_lock:
            metadata_active_clients = max(0, metadata_active_clients - 1)


@app.on_event("shutdown")
def shutdown_frame_source() -> None:
    source_stop_event.set()

    if source_thread is not None and source_thread.is_alive():
        source_thread.join(timeout=1.5)

    close_method = getattr(frame_source, "close", None)
    if callable(close_method):
        close_method()
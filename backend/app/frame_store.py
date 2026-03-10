from __future__ import annotations

import io
from dataclasses import dataclass
from threading import Lock
import time
from typing import Any

from PIL import Image


@dataclass
class FrameSnapshot:
    jpeg: bytes | None
    seq: int
    perf_time: float
    timestamp_ms: float
    width: int
    height: int
    stream_id: str
    capture_fps_estimate: float
    capture_read_ms_estimate: float
    capture_encode_ms_estimate: float


class FrameStore:
    def __init__(self, jpeg_quality: int) -> None:
        self._jpeg_quality = jpeg_quality
        self._lock = Lock()

        self._latest_jpeg: bytes | None = None
        self._latest_frame_seq = 0
        self._latest_frame_time = 0.0
        self._latest_frame_timestamp_ms = 0.0
        self._latest_frame_width = 0
        self._latest_frame_height = 0
        self._latest_stream_id = "default"
        self._capture_fps_ema = 0.0
        self._capture_read_ms_ema = 0.0
        self._capture_encode_ms_ema = 0.0

    @staticmethod
    def _ema(previous: float, sample: float, alpha: float = 0.15) -> float:
        if previous == 0:
            return sample
        return previous * (1.0 - alpha) + sample * alpha

    @staticmethod
    def _validate_frame(frame: Any) -> tuple[int, int]:
        shape = getattr(frame, "shape", None)
        if not isinstance(shape, tuple) and not isinstance(shape, list):
            raise ValueError("frame must expose shape like (height, width, channels)")

        if len(shape) != 3:
            raise ValueError("frame must have 3 dimensions: (height, width, channels)")

        height = int(shape[0])
        width = int(shape[1])
        channels = int(shape[2])
        if height <= 0 or width <= 0 or channels != 3:
            raise ValueError("frame must be RGB with shape (height>0, width>0, 3)")

        return width, height

    def _encode_jpeg(self, frame: Any) -> bytes:
        buffer = io.BytesIO()
        Image.fromarray(frame, mode="RGB").save(buffer, format="JPEG", quality=self._jpeg_quality)
        return buffer.getvalue()

    def ingest_frame(
        self,
        frame: Any,
        seq: int | None = None,
        timestamp_ms: float | None = None,
        stream_id: str = "default",
    ) -> int:
        width, height = self._validate_frame(frame)

        encode_started = time.perf_counter()
        payload = self._encode_jpeg(frame)
        encode_elapsed_ms = (time.perf_counter() - encode_started) * 1000.0
        now = time.perf_counter()

        with self._lock:
            if self._latest_frame_time > 0:
                instantaneous_fps = 1.0 / max(0.001, now - self._latest_frame_time)
                self._capture_fps_ema = self._ema(self._capture_fps_ema, instantaneous_fps)

            next_seq = self._latest_frame_seq + 1 if seq is None else int(seq)

            self._latest_jpeg = payload
            self._latest_frame_seq = next_seq
            self._latest_frame_time = now
            self._latest_frame_timestamp_ms = time.time() * 1000.0 if timestamp_ms is None else float(timestamp_ms)
            self._latest_frame_width = width
            self._latest_frame_height = height
            self._latest_stream_id = stream_id

            # Plugin mode receives frames from external owner, so read latency in this process is zero.
            self._capture_read_ms_ema = self._ema(self._capture_read_ms_ema, 0.0)
            self._capture_encode_ms_ema = self._ema(self._capture_encode_ms_ema, encode_elapsed_ms)

            return next_seq

    def snapshot(self) -> FrameSnapshot:
        with self._lock:
            return FrameSnapshot(
                jpeg=self._latest_jpeg,
                seq=self._latest_frame_seq,
                perf_time=self._latest_frame_time,
                timestamp_ms=self._latest_frame_timestamp_ms,
                width=self._latest_frame_width,
                height=self._latest_frame_height,
                stream_id=self._latest_stream_id,
                capture_fps_estimate=self._capture_fps_ema,
                capture_read_ms_estimate=self._capture_read_ms_ema,
                capture_encode_ms_estimate=self._capture_encode_ms_ema,
            )

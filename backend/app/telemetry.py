from __future__ import annotations

from threading import Lock
from typing import Any


class DeliveryTelemetry:
    def __init__(self, metadata_target_fps: int) -> None:
        self._metadata_target_fps = metadata_target_fps
        self._lock = Lock()

        self._snapshot_count = 0
        self._snapshot_time = 0.0
        self._snapshot_fps_ema = 0.0
        self._snapshot_mbps_ema = 0.0
        self._snapshot_bytes_total = 0

        self._mjpeg_count = 0
        self._mjpeg_time = 0.0
        self._mjpeg_fps_ema = 0.0
        self._mjpeg_mbps_ema = 0.0
        self._mjpeg_bytes_total = 0
        self._mjpeg_active_clients = 0

        self._metadata_count = 0
        self._metadata_time = 0.0
        self._metadata_fps_ema = 0.0
        self._metadata_mbps_ema = 0.0
        self._metadata_bytes_total = 0
        self._metadata_frame_skew_ms_ema = 0.0
        self._metadata_active_clients = 0

    @staticmethod
    def _ema(previous: float, sample: float, alpha: float = 0.15) -> float:
        if previous == 0:
            return sample
        return previous * (1.0 - alpha) + sample * alpha

    def update_snapshot(self, now: float, payload_bytes: int) -> None:
        with self._lock:
            if self._snapshot_time > 0:
                delta = max(0.001, now - self._snapshot_time)
                instantaneous_fps = 1.0 / delta
                instantaneous_mbps = (payload_bytes * 8.0) / delta / 1_000_000
                self._snapshot_fps_ema = self._ema(self._snapshot_fps_ema, instantaneous_fps)
                self._snapshot_mbps_ema = self._ema(self._snapshot_mbps_ema, instantaneous_mbps)

            self._snapshot_count += 1
            self._snapshot_time = now
            self._snapshot_bytes_total += payload_bytes

    def update_mjpeg(self, now: float, payload_bytes: int) -> None:
        with self._lock:
            if self._mjpeg_time > 0:
                delta = max(0.001, now - self._mjpeg_time)
                instantaneous_fps = 1.0 / delta
                instantaneous_mbps = (payload_bytes * 8.0) / delta / 1_000_000
                self._mjpeg_fps_ema = self._ema(self._mjpeg_fps_ema, instantaneous_fps)
                self._mjpeg_mbps_ema = self._ema(self._mjpeg_mbps_ema, instantaneous_mbps)

            self._mjpeg_count += 1
            self._mjpeg_time = now
            self._mjpeg_bytes_total += payload_bytes

    def update_metadata(self, now: float, payload_bytes: int, frame_skew_ms: float) -> None:
        with self._lock:
            if self._metadata_time > 0:
                delta = max(0.001, now - self._metadata_time)
                instantaneous_fps = 1.0 / delta
                instantaneous_mbps = (payload_bytes * 8.0) / delta / 1_000_000
                self._metadata_fps_ema = self._ema(self._metadata_fps_ema, instantaneous_fps)
                self._metadata_mbps_ema = self._ema(self._metadata_mbps_ema, instantaneous_mbps)

            self._metadata_count += 1
            self._metadata_time = now
            self._metadata_bytes_total += payload_bytes
            self._metadata_frame_skew_ms_ema = self._ema(self._metadata_frame_skew_ms_ema, frame_skew_ms)

    def add_mjpeg_client(self) -> None:
        with self._lock:
            self._mjpeg_active_clients += 1

    def remove_mjpeg_client(self) -> None:
        with self._lock:
            self._mjpeg_active_clients = max(0, self._mjpeg_active_clients - 1)

    def add_metadata_client(self) -> None:
        with self._lock:
            self._metadata_active_clients += 1

    def remove_metadata_client(self) -> None:
        with self._lock:
            self._metadata_active_clients = max(0, self._metadata_active_clients - 1)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                "snapshot": {
                    "count": self._snapshot_count,
                    "fps_estimate": round(self._snapshot_fps_ema, 2),
                    "throughput_mbps_estimate": round(self._snapshot_mbps_ema, 2),
                    "bytes_total": self._snapshot_bytes_total,
                },
                "mjpeg": {
                    "count": self._mjpeg_count,
                    "fps_estimate": round(self._mjpeg_fps_ema, 2),
                    "throughput_mbps_estimate": round(self._mjpeg_mbps_ema, 2),
                    "bytes_total": self._mjpeg_bytes_total,
                    "active_clients": self._mjpeg_active_clients,
                },
                "metadata": {
                    "count": self._metadata_count,
                    "fps_estimate": round(self._metadata_fps_ema, 2),
                    "throughput_mbps_estimate": round(self._metadata_mbps_ema, 2),
                    "bytes_total": self._metadata_bytes_total,
                    "active_clients": self._metadata_active_clients,
                    "target_fps": self._metadata_target_fps,
                    "frame_skew_ms_estimate": round(self._metadata_frame_skew_ms_ema, 2),
                    "overlay_lag_proxy_ms_estimate": round(self._metadata_frame_skew_ms_ema, 2),
                },
            }

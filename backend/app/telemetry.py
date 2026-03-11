from __future__ import annotations

from threading import Lock
import time
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

        self._webrtc_offer_count = 0
        self._webrtc_active_sessions = 0
        self._webrtc_last_offer_timestamp_ms = 0.0

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

    def update_webrtc_offer(self, active_sessions: int) -> None:
        with self._lock:
            self._webrtc_offer_count += 1
            self._webrtc_active_sessions = max(0, int(active_sessions))
            self._webrtc_last_offer_timestamp_ms = time.time() * 1000.0

    def set_webrtc_active_sessions(self, active_sessions: int) -> None:
        with self._lock:
            self._webrtc_active_sessions = max(0, int(active_sessions))

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
                "webrtc": {
                    "offer_count": self._webrtc_offer_count,
                    "active_sessions": self._webrtc_active_sessions,
                    "last_offer_timestamp_ms": round(self._webrtc_last_offer_timestamp_ms, 2)
                    if self._webrtc_last_offer_timestamp_ms > 0
                    else None,
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

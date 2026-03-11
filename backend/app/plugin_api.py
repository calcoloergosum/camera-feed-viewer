from __future__ import annotations

from typing import Any, Protocol

from fastapi import FastAPI

from .server import app as default_app
from .server import get_runtime

class FrameCallback(Protocol):
    def __call__(
        self,
        frame: Any,
        seq: int | None = None,
        timestamp_ms: float | None = None,
        stream_id: str = "default",
    ) -> int: ...


class MetadataCallback(Protocol):
    def __call__(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


def get_frame_callback(target_app: FastAPI | None = None) -> FrameCallback:
    """Return frame callback bound to the provided app runtime (or default app runtime)."""

    runtime = get_runtime(target_app or default_app)
    return runtime.on_camera_frame


def get_metadata_callback(target_app: FastAPI | None = None) -> MetadataCallback:
    """Return metadata callback bound to the provided app runtime (or default app runtime)."""

    runtime = get_runtime(target_app or default_app)
    return runtime.on_metadata_payload

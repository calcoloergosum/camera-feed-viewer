from __future__ import annotations

from typing import Any

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - runtime dependency check
    cv2 = None


class RandomArrayFrameSource:
    """Emulates a camera source by returning synthetic RGB NumPy frames."""

    def __init__(self, width: int = 1280, height: int = 720, seed: int = 7) -> None:
        self.width = width
        self.height = height
        self._frame_index = 0
        self._rng = np.random.default_rng(seed)
        self._marker_width = max(8, self.width // 40)

    def next_frame(self) -> np.ndarray:
        """Returns an RGB uint8 frame with strong random texture and a moving marker."""
        self._frame_index += 1

        frame = self._rng.integers(
            20,
            256,
            size=(self.height, self.width, 3),
            dtype=np.uint8,
        )

        marker_x = (self._frame_index * 17) % self.width
        marker_end = min(self.width, marker_x + self._marker_width)
        frame[:, marker_x:marker_end, :] = np.array([255, 255, 255], dtype=np.uint8)

        return frame


class OpenCVCameraFrameSource:
    """Reads RGB frames from a physical camera via cv2.VideoCapture."""

    def __init__(self, camera_index: int = 0, width: int = 1280, height: int = 720) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python is not installed")

        self.camera_index = camera_index
        self.width = width
        self.height = height

        self._capture = cv2.VideoCapture(self.camera_index)
        if not self._capture.isOpened():
            raise RuntimeError(f"Unable to open camera index {self.camera_index}")

        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))

    def next_frame(self) -> np.ndarray:
        ok, bgr_frame = self._capture.read()
        if not ok or bgr_frame is None:
            raise RuntimeError(f"Failed to read frame from camera index {self.camera_index}")

        return cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)

    def close(self) -> None:
        if hasattr(self, "_capture") and self._capture is not None:
            self._capture.release()


def build_frame_source(
    source: str,
    width: int,
    height: int,
    camera_index: int,
    seed: int,
) -> tuple[Any, dict[str, Any]]:
    normalized = source.strip().lower()
    if normalized not in {"auto", "cv2", "random"}:
        normalized = "auto"

    if normalized in {"auto", "cv2"}:
        try:
            camera_source = OpenCVCameraFrameSource(
                camera_index=camera_index,
                width=width,
                height=height,
            )
            return (
                camera_source,
                {
                    "requested_source": source,
                    "active_source": "cv2",
                    "camera_index": camera_index,
                    "frame_shape": [height, width, 3],
                },
            )
        except RuntimeError as error:
            if normalized == "cv2":
                raise

            fallback = RandomArrayFrameSource(width=width, height=height, seed=seed)
            return (
                fallback,
                {
                    "requested_source": source,
                    "active_source": "random",
                    "fallback_reason": str(error),
                    "frame_shape": [height, width, 3],
                },
            )

    random_source = RandomArrayFrameSource(width=width, height=height, seed=seed)
    return (
        random_source,
        {
            "requested_source": source,
            "active_source": "random",
            "frame_shape": [height, width, 3],
        },
    )
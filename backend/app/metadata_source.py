from __future__ import annotations

import math
from typing import Any


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def build_overlay_payload(timestamp_ms: float, source_width: int, source_height: int) -> dict[str, Any]:
    """Builds deterministic synthetic overlay metadata for backend publishing."""
    t = timestamp_ms / 1000.0

    center_x = source_width * (0.5 + math.sin(t * 0.6) * 0.25)
    center_y = source_height * (0.45 + math.cos(t * 0.9) * 0.18)

    box_width = 190.0
    box_height = 130.0
    box_x = _clamp(center_x - box_width / 2.0, 0.0, source_width - box_width)
    box_y = _clamp(center_y - box_height / 2.0, 0.0, source_height - box_height)

    sweep = (math.sin(t * 0.75) + 1.0) / 2.0
    poly_left = 140.0 + sweep * 160.0
    poly_top = 160.0 + math.cos(t * 0.4) * 40.0

    return {
        "timestampMs": timestamp_ms,
        "serverTimestampMs": timestamp_ms,
        "sourceWidth": float(source_width),
        "sourceHeight": float(source_height),
        "items": [
            {
                "kind": "box",
                "id": "tracked-target",
                "x": box_x,
                "y": box_y,
                "width": box_width,
                "height": box_height,
                "label": "Target A",
                "color": "#2ec4b6",
            },
            {
                "kind": "polygon",
                "id": "attention-zone",
                "points": [
                    [poly_left, poly_top],
                    [poly_left + 300.0, poly_top - 45.0],
                    [poly_left + 410.0, poly_top + 125.0],
                    [poly_left + 120.0, poly_top + 220.0],
                ],
                "label": "Zone 2",
                "color": "#ff9f1c",
            },
            {
                "kind": "label",
                "id": "status",
                "x": 32.0,
                "y": 42.0,
                "text": f"metadata tick {int(t * 2.0)}",
                "color": "#e8f1ff",
            },
        ],
    }


def build_overlay_payload_with_frame_context(
    timestamp_ms: float,
    source_width: int,
    source_height: int,
    frame_seq: int,
    server_timestamp_ms: float,
) -> dict[str, Any]:
    payload = build_overlay_payload(
        timestamp_ms=timestamp_ms,
        source_width=source_width,
        source_height=source_height,
    )
    payload["frameSeq"] = frame_seq
    payload["serverTimestampMs"] = server_timestamp_ms
    return payload

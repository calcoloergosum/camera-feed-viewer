from __future__ import annotations

import argparse
import math
from threading import Event, Thread
import time

import uvicorn

from backend.app.frame_source import build_frame_source
from backend.app.plugin_api import get_frame_callback, get_metadata_callback
from backend.app.server import app


def build_polygon_metadata_payload(
    timestamp_ms: float,
    server_timestamp_ms: float,
    frame_seq: int,
    source_width: int,
    source_height: int,
) -> dict[str, object]:
    t = timestamp_ms / 1000.0
    sweep = (math.sin(t * 0.8) + 1.0) / 2.0
    left = source_width * (0.12 + 0.18 * sweep)
    top = source_height * (0.18 + 0.06 * math.cos(t * 0.5))
    zone_width = source_width * 0.32
    zone_height = source_height * 0.28

    return {
        "timestampMs": timestamp_ms,
        "serverTimestampMs": server_timestamp_ms,
        "frameSeq": frame_seq,
        "sourceWidth": float(source_width),
        "sourceHeight": float(source_height),
        "items": [
            {
                "kind": "polygon",
                "id": "owner-zone",
                "points": [
                    [left, top],
                    [left + zone_width, top - source_height * 0.05],
                    [left + zone_width + source_width * 0.08, top + zone_height],
                    [left + source_width * 0.04, top + zone_height + source_height * 0.08],
                ],
                "label": "Owner Polygon",
                "color": "#ff9f1c",
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end test using webcam: external process owns camera and feeds plugin callback"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--source", default="auto", choices=["auto", "cv2", "random"])
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    frame_source, frame_source_info = build_frame_source(
        source=args.source,
        width=max(160, args.width),
        height=max(120, args.height),
        camera_index=args.camera_index,
        seed=args.seed,
    )
    frame_callback = get_frame_callback(app)
    metadata_callback = get_metadata_callback(app)

    stop_event = Event()

    def feed_loop() -> None:
        frame_seq = 0

        while not stop_event.is_set():
            frame = frame_source.next_frame()
            frame_seq += 1

            timestamp_ms = time.time() * 1000.0
            frame_callback(
                frame=frame,
                seq=frame_seq,
                timestamp_ms=timestamp_ms,
                stream_id="legacy-owner-harness",
            )

            source_height = int(frame.shape[0])
            source_width = int(frame.shape[1])
            metadata_callback(
                build_polygon_metadata_payload(
                    timestamp_ms=timestamp_ms,
                    server_timestamp_ms=time.time() * 1000.0,
                    frame_seq=frame_seq,
                    source_width=source_width,
                    source_height=source_height,
                )
            )

            if frame_source_info.get("active_source") == "random":
                time.sleep(max(0.0, 1.0 / max(1.0, args.fps)))

    feeder = Thread(target=feed_loop, daemon=True)
    feeder.start()

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        stop_event.set()
        feeder.join(timeout=1.5)
        close_method = getattr(frame_source, "close", None)
        if callable(close_method):
            close_method()


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from threading import Event, Thread
import time

import uvicorn

from backend.app.frame_source import build_frame_source
from backend.app.plugin_api import get_frame_callback
from backend.app.server import app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Legacy owner harness: external process owns camera and feeds plugin callback"
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

    stop_event = Event()

    def feed_loop() -> None:
        frame_seq = 0

        while not stop_event.is_set():
            frame = frame_source.next_frame()
            frame_seq += 1
            frame_callback(
                frame=frame,
                seq=frame_seq,
                timestamp_ms=time.time() * 1000.0,
                stream_id="legacy-owner-harness",
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

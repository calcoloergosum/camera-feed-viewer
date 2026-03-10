from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import websockets


@dataclass
class SmokeResult:
    name: str
    ok: bool
    detail: str


def check_health(base_url: str) -> SmokeResult:
    with urlopen(f"{base_url}/health", timeout=3.0) as response:
        payload = json.loads(response.read().decode("utf-8"))

    required = ["status", "delivery", "latest_frame_seq", "latest_frame_timestamp_ms"]
    missing = [key for key in required if key not in payload]
    if payload.get("status") != "ok" or missing:
        return SmokeResult("health", False, f"invalid health payload, missing={missing}")

    return SmokeResult("health", True, "status ok")


def check_health_plugin_empty(base_url: str) -> SmokeResult:
    with urlopen(f"{base_url}/health", timeout=3.0) as response:
        payload = json.loads(response.read().decode("utf-8"))

    status = payload.get("status")
    frame_ready = payload.get("frame_ready")
    if status != "waiting_for_frames" or frame_ready is not False:
        return SmokeResult(
            "health_plugin_empty",
            False,
            f"unexpected plugin-empty health state status={status} frame_ready={frame_ready}",
        )

    return SmokeResult("health_plugin_empty", True, "waiting-for-frames state confirmed")


def check_frame(base_url: str) -> SmokeResult:
    request = Request(f"{base_url}/frame.jpg", headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=4.0) as response:
        payload = response.read()
        seq = response.headers.get("X-Frame-Seq")
        timestamp_ms = response.headers.get("X-Frame-Timestamp-Ms")

    if not payload:
        return SmokeResult("frame", False, "empty frame payload")

    if seq is None or timestamp_ms is None:
        return SmokeResult("frame", False, "missing frame sequence/timestamp headers")

    return SmokeResult("frame", True, f"bytes={len(payload)} seq={seq}")


def check_frame_plugin_empty(base_url: str) -> SmokeResult:
    request = Request(f"{base_url}/frame.jpg", headers={"Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=4.0):
            pass
    except HTTPError as error:
        if error.code == 503:
            return SmokeResult("frame_plugin_empty", True, "503 before first ingested frame")
        return SmokeResult("frame_plugin_empty", False, f"unexpected status={error.code}")

    return SmokeResult("frame_plugin_empty", False, "expected 503 but endpoint returned success")


async def check_metadata(base_url: str, timeout_s: float) -> SmokeResult:
    ws_url = f"{base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/metadata"

    try:
        async with websockets.connect(ws_url) as socket:
            raw = await asyncio.wait_for(socket.recv(), timeout=timeout_s)
    except Exception as error:
        return SmokeResult("metadata", False, f"websocket receive failed: {error}")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return SmokeResult("metadata", False, "message was not valid json")

    required = ["timestampMs", "serverTimestampMs", "frameSeq", "items"]
    missing = [key for key in required if key not in payload]
    if missing:
        return SmokeResult("metadata", False, f"missing metadata keys: {missing}")

    return SmokeResult("metadata", True, "received metadata message with sync keys")


async def check_metadata_plugin_empty(base_url: str, timeout_s: float) -> SmokeResult:
    ws_url = f"{base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/metadata"

    try:
        async with websockets.connect(ws_url) as socket:
            try:
                await asyncio.wait_for(socket.recv(), timeout=timeout_s)
                return SmokeResult(
                    "metadata_plugin_empty",
                    False,
                    "received metadata unexpectedly before first ingested frame",
                )
            except asyncio.TimeoutError:
                return SmokeResult(
                    "metadata_plugin_empty",
                    True,
                    "no metadata emitted before first ingested frame",
                )
    except Exception as error:
        return SmokeResult("metadata_plugin_empty", False, f"websocket connection failed: {error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backend smoke checks for Stage E")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--metadata-timeout", type=float, default=4.0)
    parser.add_argument(
        "--mode",
        default="harness",
        choices=["harness", "plugin-empty"],
        help="harness validates frame+metadata delivery; plugin-empty validates waiting state before frame ingestion",
    )
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    if args.mode == "plugin-empty":
        results = [
            check_health_plugin_empty(base_url),
            check_frame_plugin_empty(base_url),
            asyncio.run(check_metadata_plugin_empty(base_url, max(1.0, args.metadata_timeout))),
        ]
    else:
        results = [
            check_health(base_url),
            check_frame(base_url),
            asyncio.run(check_metadata(base_url, max(1.0, args.metadata_timeout))),
        ]

    failures = [result for result in results if not result.ok]
    for result in results:
        marker = "PASS" if result.ok else "FAIL"
        print(f"[{marker}] {result.name}: {result.detail}")

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

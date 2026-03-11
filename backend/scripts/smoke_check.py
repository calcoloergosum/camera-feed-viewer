from __future__ import annotations

# pyright: reportMissingImports=false, reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import websockets

try:
    from aiortc import (
        RTCPeerConnection as AiortcRTCPeerConnection,
        RTCSessionDescription as AiortcRTCSessionDescription,
    )

    WEBRTC_CHECK_IMPORT_ERROR: Exception | None = None
except Exception as error:  # pragma: no cover - dependency availability differs by environment
    WEBRTC_CHECK_IMPORT_ERROR = error
    AiortcRTCPeerConnection = Any
    AiortcRTCSessionDescription = Any


if WEBRTC_CHECK_IMPORT_ERROR is None:
    RTCPeerConnectionType = AiortcRTCPeerConnection  # type: ignore[assignment]
    RTCSessionDescriptionType = AiortcRTCSessionDescription  # type: ignore[assignment]
else:

    class RTCPeerConnectionType:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"aiortc import failed: {WEBRTC_CHECK_IMPORT_ERROR}")

    class RTCSessionDescriptionType:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"aiortc import failed: {WEBRTC_CHECK_IMPORT_ERROR}")


@dataclass
class SmokeResult:
    name: str
    ok: bool
    detail: str


def fetch_health_payload(base_url: str) -> dict[str, object]:
    with urlopen(f"{base_url}/health", timeout=3.0) as response:
        return json.loads(response.read().decode("utf-8"))


def frame_diagnostics_enabled(base_url: str) -> bool:
    try:
        payload = fetch_health_payload(base_url)
    except Exception:
        return True

    diagnostics = payload.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return True

    enabled = diagnostics.get("frame_jpeg_enabled")
    if isinstance(enabled, bool):
        return enabled

    return True


def check_health(base_url: str) -> SmokeResult:
    payload = fetch_health_payload(base_url)

    required = ["status", "delivery", "latest_frame_seq", "latest_frame_timestamp_ms"]
    missing = [key for key in required if key not in payload]
    if payload.get("status") != "ok" or missing:
        return SmokeResult("health", False, f"invalid health payload, missing={missing}")

    return SmokeResult("health", True, "status ok")


def check_health_plugin_empty(base_url: str) -> SmokeResult:
    payload = fetch_health_payload(base_url)

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


def check_frame_disabled(base_url: str) -> SmokeResult:
    request = Request(f"{base_url}/frame.jpg", headers={"Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=4.0):
            pass
    except HTTPError as error:
        if error.code == 404:
            return SmokeResult("frame_disabled", True, "frame diagnostics endpoint disabled by policy")
        return SmokeResult("frame_disabled", False, f"unexpected status={error.code}")

    return SmokeResult("frame_disabled", False, "expected /frame.jpg to be disabled")


def check_stream_removed(base_url: str) -> SmokeResult:
    request = Request(f"{base_url}/stream.mjpeg", headers={"Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=3.0):
            pass
    except HTTPError as error:
        if error.code == 404:
            return SmokeResult("stream_removed", True, "legacy MJPEG route is removed")
        return SmokeResult("stream_removed", False, f"unexpected status={error.code}")

    return SmokeResult("stream_removed", False, "legacy MJPEG route still exists")


def check_webrtc_signaling(base_url: str) -> SmokeResult:
    request = Request(
        f"{base_url}/webrtc/offer",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=3.0):
            return SmokeResult("webrtc_signaling", False, "unexpected success for invalid offer payload")
    except HTTPError as error:
        if error.code == 422:
            return SmokeResult("webrtc_signaling", True, "endpoint reachable and validating payload schema")
        return SmokeResult("webrtc_signaling", False, f"unexpected status={error.code}")


async def _wait_for_ice_complete(peer_connection: Any, timeout_s: float = 2.0) -> None:
    if peer_connection.iceGatheringState == "complete":
        return

    complete_event = asyncio.Event()

    @peer_connection.on("icegatheringstatechange")
    def on_ice_state_change() -> None:
        if peer_connection.iceGatheringState == "complete":
            complete_event.set()

    try:
        await asyncio.wait_for(complete_event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return


async def _wait_for_connection_settle(peer_connection: Any, timeout_s: float = 0.35) -> None:
    if peer_connection.connectionState in {"connected", "failed", "closed"}:
        return

    settle_event = asyncio.Event()

    @peer_connection.on("connectionstatechange")
    def on_connection_state_change() -> None:
        if peer_connection.connectionState in {"connected", "failed", "closed"}:
            settle_event.set()

    try:
        await asyncio.wait_for(settle_event.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        return


def _post_json(url: str, payload: dict[str, object], timeout_s: float) -> dict[str, object]:
    encoded = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def _delete(url: str, timeout_s: float) -> None:
    request = Request(url, method="DELETE")
    with urlopen(request, timeout=timeout_s):
        return


async def check_webrtc_offer_answer(base_url: str) -> SmokeResult:
    if WEBRTC_CHECK_IMPORT_ERROR is not None:
        return SmokeResult(
            "webrtc_offer_answer",
            False,
            f"aiortc import failed: {WEBRTC_CHECK_IMPORT_ERROR}",
        )

    peer_connection = RTCPeerConnectionType()
    peer_id: str | None = None

    try:
        peer_connection.addTransceiver("video", direction="recvonly")
        offer = await peer_connection.createOffer()
        await peer_connection.setLocalDescription(offer)
        await _wait_for_ice_complete(peer_connection)

        local_description = peer_connection.localDescription
        if local_description is None or not local_description.sdp:
            return SmokeResult("webrtc_offer_answer", False, "local offer sdp missing")

        answer_payload = await asyncio.to_thread(
            _post_json,
            f"{base_url}/webrtc/offer",
            {
                "sdp": local_description.sdp,
                "type": local_description.type,
            },
            6.0,
        )

        sdp = answer_payload.get("sdp")
        answer_type = answer_payload.get("type")
        peer_id_value = answer_payload.get("peerId")
        if not isinstance(sdp, str) or not sdp.strip():
            return SmokeResult("webrtc_offer_answer", False, "answer payload missing sdp")
        if answer_type != "answer":
            return SmokeResult("webrtc_offer_answer", False, f"unexpected answer type={answer_type}")
        if not isinstance(peer_id_value, str) or not peer_id_value.strip():
            return SmokeResult("webrtc_offer_answer", False, "answer payload missing peerId")

        peer_id = peer_id_value
        await peer_connection.setRemoteDescription(RTCSessionDescriptionType(sdp=sdp, type="answer"))
        return SmokeResult("webrtc_offer_answer", True, f"negotiation complete peer_id={peer_id}")
    except Exception as error:
        return SmokeResult("webrtc_offer_answer", False, f"offer/answer failed: {error}")
    finally:
        await _wait_for_connection_settle(peer_connection)
        await peer_connection.close()
        if peer_id:
            try:
                await asyncio.to_thread(_delete, f"{base_url}/webrtc/{peer_id}", 4.0)
            except Exception:
                pass


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
    diagnostics_enabled = frame_diagnostics_enabled(base_url)

    if args.mode == "plugin-empty":
        results = [
            check_health_plugin_empty(base_url),
            check_frame_plugin_empty(base_url) if diagnostics_enabled else check_frame_disabled(base_url),
            check_stream_removed(base_url),
            check_webrtc_signaling(base_url),
            asyncio.run(check_webrtc_offer_answer(base_url)),
            asyncio.run(check_metadata_plugin_empty(base_url, max(1.0, args.metadata_timeout))),
        ]
    else:
        results = [
            check_health(base_url),
            check_frame(base_url) if diagnostics_enabled else check_frame_disabled(base_url),
            check_stream_removed(base_url),
            check_webrtc_signaling(base_url),
            asyncio.run(check_webrtc_offer_answer(base_url)),
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

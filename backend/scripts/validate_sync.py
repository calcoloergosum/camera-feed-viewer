from __future__ import annotations

# pyright: reportMissingImports=false, reportGeneralTypeIssues=false, reportAttributeAccessIssue=false

import argparse
import asyncio
import json
import random
import statistics
import time
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

    WEBRTC_VALIDATE_IMPORT_ERROR: Exception | None = None
except Exception as error:  # pragma: no cover - dependency availability differs by environment
    WEBRTC_VALIDATE_IMPORT_ERROR = error
    AiortcRTCPeerConnection = Any
    AiortcRTCSessionDescription = Any


if WEBRTC_VALIDATE_IMPORT_ERROR is None:
    RTCPeerConnectionType = AiortcRTCPeerConnection  # type: ignore[assignment]
    RTCSessionDescriptionType = AiortcRTCSessionDescription  # type: ignore[assignment]
else:

    class RTCPeerConnectionType:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"aiortc import failed: {WEBRTC_VALIDATE_IMPORT_ERROR}")

    class RTCSessionDescriptionType:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"aiortc import failed: {WEBRTC_VALIDATE_IMPORT_ERROR}")


@dataclass
class MetadataSample:
    seq: int
    timestamp_ms: float
    server_timestamp_ms: float
    received_at_ms: float


@dataclass
class FrameProbe:
    seq: int
    timestamp_ms: float
    sampled_metadata_seq: int


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0

    sorted_values = sorted(values)
    index = min(len(sorted_values) - 1, max(0, int(len(sorted_values) * p) - 1))
    return sorted_values[index]


def fetch_frame_probe(frame_url: str, sampled_metadata_seq: int) -> FrameProbe:
    request = Request(frame_url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=3.0) as response:
        _ = response.read(1)
        seq_header = response.headers.get("X-Frame-Seq")
        ts_header = response.headers.get("X-Frame-Timestamp-Ms")

    if seq_header is None or ts_header is None:
        raise RuntimeError("frame headers missing X-Frame-Seq or X-Frame-Timestamp-Ms")

    return FrameProbe(
        seq=int(seq_header),
        timestamp_ms=float(ts_header),
        sampled_metadata_seq=sampled_metadata_seq,
    )


def fetch_health(base_url: str) -> dict[str, Any]:
    with urlopen(f"{base_url}/health", timeout=3.0) as response:
        return json.loads(response.read().decode("utf-8"))


def frame_diagnostics_enabled(health: dict[str, Any]) -> bool:
    diagnostics = health.get("diagnostics")
    if not isinstance(diagnostics, dict):
        return True

    enabled = diagnostics.get("frame_jpeg_enabled")
    if isinstance(enabled, bool):
        return enabled

    return True


def _post_json(url: str, payload: dict[str, object], timeout_s: float) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def _delete(url: str, timeout_s: float) -> None:
    request = Request(url, method="DELETE")
    with urlopen(request, timeout=timeout_s):
        return


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


async def open_webrtc_session(base_url: str) -> tuple[Any, str]:
    if WEBRTC_VALIDATE_IMPORT_ERROR is not None:
        raise RuntimeError(f"aiortc import failed: {WEBRTC_VALIDATE_IMPORT_ERROR}")

    peer_connection = RTCPeerConnectionType()
    peer_connection.addTransceiver("video", direction="recvonly")
    offer = await peer_connection.createOffer()
    await peer_connection.setLocalDescription(offer)
    await _wait_for_ice_complete(peer_connection)

    local_description = peer_connection.localDescription
    if local_description is None or not local_description.sdp:
        raise RuntimeError("local offer sdp unavailable")

    answer_payload = await asyncio.to_thread(
        _post_json,
        f"{base_url}/webrtc/offer",
        {
            "sdp": local_description.sdp,
            "type": local_description.type,
        },
        6.0,
    )

    answer_sdp = answer_payload.get("sdp")
    answer_type = answer_payload.get("type")
    peer_id = answer_payload.get("peerId")
    if not isinstance(answer_sdp, str) or not answer_sdp.strip():
        raise RuntimeError("backend answer missing sdp")
    if answer_type != "answer":
        raise RuntimeError(f"unexpected answer type={answer_type}")
    if not isinstance(peer_id, str) or not peer_id.strip():
        raise RuntimeError("backend answer missing peerId")

    await peer_connection.setRemoteDescription(RTCSessionDescriptionType(sdp=answer_sdp, type="answer"))
    return peer_connection, peer_id


async def close_webrtc_session(base_url: str, peer_connection: Any, peer_id: str) -> None:
    await _wait_for_connection_settle(peer_connection)
    await peer_connection.close()
    try:
        await asyncio.to_thread(_delete, f"{base_url}/webrtc/{peer_id}", 4.0)
    except Exception:
        pass


async def collect_sync_samples(
    ws_url: str,
    frame_url: str,
    base_url: str,
    samples: int,
    probe_every: int,
    jitter_ms: float,
    burst_every: int,
    burst_pause_ms: float,
    hold_webrtc_session: bool,
) -> tuple[list[MetadataSample], list[FrameProbe], str | None]:
    metadata_samples: list[MetadataSample] = []
    frame_probes: list[FrameProbe] = []
    peer_connection: Any | None = None
    peer_id: str | None = None

    if hold_webrtc_session:
        peer_connection, peer_id = await open_webrtc_session(base_url)

    try:
        async with websockets.connect(ws_url) as socket:
            for index in range(samples):
                raw = await asyncio.wait_for(socket.recv(), timeout=3.0)
                payload = json.loads(raw)

                timestamp_ms = float(payload["timestampMs"])
                server_timestamp_ms = float(payload["serverTimestampMs"])
                seq = int(payload["frameSeq"])

                metadata_samples.append(
                    MetadataSample(
                        seq=seq,
                        timestamp_ms=timestamp_ms,
                        server_timestamp_ms=server_timestamp_ms,
                        received_at_ms=time.time() * 1000.0,
                    )
                )

                if probe_every > 0 and (index + 1) % probe_every == 0:
                    probe = await asyncio.to_thread(fetch_frame_probe, frame_url, seq)
                    frame_probes.append(probe)

                if jitter_ms > 0:
                    await asyncio.sleep(random.uniform(0.0, jitter_ms) / 1000.0)

                if burst_every > 0 and burst_pause_ms > 0 and (index + 1) % burst_every == 0:
                    await asyncio.sleep(burst_pause_ms / 1000.0)
    finally:
        if hold_webrtc_session and peer_connection is not None and peer_id is not None:
            await close_webrtc_session(base_url, peer_connection, peer_id)

    return metadata_samples, frame_probes, peer_id


def assert_webrtc_runtime_activity(
    base_url: str,
    min_frames_pushed: int,
    min_track_frames_emitted: int,
    retries: int,
    retry_delay_s: float,
) -> dict[str, int | str]:
    for attempt in range(max(1, retries)):
        health = fetch_health(base_url)
        runtime = health.get("webrtc_runtime")
        if not isinstance(runtime, dict):
            raise RuntimeError("health payload missing webrtc_runtime object")

        frames_pushed = int(runtime.get("frames_pushed", 0))
        track_frames_emitted_total = int(runtime.get("track_frames_emitted_total", 0))
        media_pipeline = str(runtime.get("media_pipeline", "unknown"))

        if frames_pushed >= min_frames_pushed and track_frames_emitted_total >= min_track_frames_emitted:
            return {
                "frames_pushed": frames_pushed,
                "track_frames_emitted_total": track_frames_emitted_total,
                "media_pipeline": media_pipeline,
            }

        if attempt < max(1, retries) - 1:
            time.sleep(max(0.05, retry_delay_s))

    raise RuntimeError(
        "webrtc runtime activity below threshold "
        f"(frames_pushed>={min_frames_pushed}, track_frames_emitted_total>={min_track_frames_emitted})"
    )


def summarize(metadata_samples: list[MetadataSample], frame_probes: list[FrameProbe]) -> dict[str, float | int]:
    if not metadata_samples:
        raise RuntimeError("no metadata samples collected")

    seq_values = [sample.seq for sample in metadata_samples]
    seq_deltas = [b - a for a, b in zip(seq_values, seq_values[1:])]
    non_increasing = [delta for delta in seq_deltas if delta <= 0]

    server_minus_frame = [
        sample.server_timestamp_ms - sample.timestamp_ms for sample in metadata_samples
    ]
    receive_minus_frame = [
        sample.received_at_ms - sample.timestamp_ms for sample in metadata_samples
    ]

    print("Synchronization validation summary")
    print(f"- metadata_samples: {len(metadata_samples)}")
    print(f"- metadata_seq_first_last: {seq_values[0]} -> {seq_values[-1]}")
    print(f"- metadata_seq_non_increasing_count: {len(non_increasing)}")
    print(f"- metadata_seq_gap_max: {max(seq_deltas) if seq_deltas else 0}")
    print(f"- metadata_seq_gap_p95: {_percentile([float(v) for v in seq_deltas], 0.95):.2f}")
    print(f"- server_minus_frame_ms_mean: {statistics.mean(server_minus_frame):.2f}")
    print(f"- server_minus_frame_ms_p95: {_percentile(server_minus_frame, 0.95):.2f}")
    print(f"- receive_minus_frame_ms_mean: {statistics.mean(receive_minus_frame):.2f}")
    print(f"- receive_minus_frame_ms_p95: {_percentile(receive_minus_frame, 0.95):.2f}")

    metrics: dict[str, float | int] = {
        "metadata_seq_non_increasing_count": len(non_increasing),
        "metadata_server_minus_frame_ms_p95": _percentile(server_minus_frame, 0.95),
        "metadata_receive_minus_frame_ms_p95": _percentile(receive_minus_frame, 0.95),
        "frame_probe_non_increasing_count": 0,
    }

    if frame_probes:
        probe_seq_values = [probe.seq for probe in frame_probes]
        probe_deltas = [b - a for a, b in zip(probe_seq_values, probe_seq_values[1:])]
        probe_non_increasing = [delta for delta in probe_deltas if delta <= 0]
        cross_channel_delta = [
            probe.seq - probe.sampled_metadata_seq for probe in frame_probes
        ]

        print(f"- frame_probes: {len(frame_probes)}")
        print(
            f"- frame_probe_seq_first_last: {probe_seq_values[0]} -> {probe_seq_values[-1]}"
        )
        print(f"- frame_probe_non_increasing_count: {len(probe_non_increasing)}")
        print(
            f"- frame_minus_metadata_seq_delta_mean: {statistics.mean(cross_channel_delta):.2f}"
        )
        print(
            f"- frame_minus_metadata_seq_delta_p95: {_percentile([float(v) for v in cross_channel_delta], 0.95):.2f}"
        )

        metrics["frame_probe_non_increasing_count"] = len(probe_non_increasing)

    return metrics


async def validate_plugin_empty_mode(base_url: str, metadata_timeout_s: float) -> None:
    health = fetch_health(base_url)
    status = health.get("status")
    frame_ready = health.get("frame_ready")
    diagnostics_enabled = frame_diagnostics_enabled(health)
    if status != "waiting_for_frames" or frame_ready is not False:
        raise RuntimeError(
            f"plugin-empty health mismatch status={status} frame_ready={frame_ready}"
        )

    frame_url = f"{base_url}/frame.jpg"
    request = Request(frame_url, headers={"Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=3.0):
            pass
    except HTTPError as error:
        expected_status = 503 if diagnostics_enabled else 404
        if error.code != expected_status:
            raise RuntimeError(
                f"plugin-empty expected {expected_status} from /frame.jpg but got {error.code}"
            )
    else:
        if diagnostics_enabled:
            raise RuntimeError("plugin-empty expected /frame.jpg to return 503 before frame ingestion")
        raise RuntimeError("plugin-empty expected /frame.jpg to be disabled")

    ws_url = f"{base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/metadata"
    async with websockets.connect(ws_url) as socket:
        try:
            await asyncio.wait_for(socket.recv(), timeout=metadata_timeout_s)
        except asyncio.TimeoutError:
            print("Synchronization validation summary")
            print("- mode: plugin-empty")
            print("- health_waiting_for_frames: true")
            if diagnostics_enabled:
                print("- frame_endpoint_503_before_ingest: true")
            else:
                print("- frame_endpoint_disabled: true")
            print("- metadata_message_before_ingest: false")
            return

        raise RuntimeError("plugin-empty expected no metadata before first ingested frame")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate backend video/metadata synchronization")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--mode",
        default="harness",
        choices=["harness", "plugin-empty"],
        help="harness validates live sequence/timestamps; plugin-empty validates pre-ingest waiting behavior",
    )
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument(
        "--probe-every",
        type=int,
        default=12,
        help="Frame probe cadence (0 disables /frame.jpg probes)",
    )
    parser.add_argument("--jitter-ms", type=float, default=60.0)
    parser.add_argument("--burst-every", type=int, default=30)
    parser.add_argument("--burst-pause-ms", type=float, default=180.0)
    parser.add_argument("--metadata-timeout", type=float, default=2.0)
    parser.add_argument("--max-server-minus-frame-ms-p95", type=float, default=1200.0)
    parser.add_argument("--max-receive-minus-frame-ms-p95", type=float, default=2500.0)
    parser.add_argument(
        "--require-webrtc-active",
        action="store_true",
        help="Open a live WebRTC session during harness sync sampling and assert runtime counters",
    )
    parser.add_argument("--min-webrtc-frames-pushed", type=int, default=20)
    parser.add_argument("--min-webrtc-frames-emitted", type=int, default=5)
    parser.add_argument("--webrtc-runtime-retries", type=int, default=4)
    parser.add_argument("--webrtc-runtime-retry-delay", type=float, default=0.2)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")

    if args.mode == "plugin-empty":
        asyncio.run(
            validate_plugin_empty_mode(
                base_url=base_url,
                metadata_timeout_s=max(0.5, args.metadata_timeout),
            )
        )
        return

    ws_url = f"{base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/metadata"
    frame_url = f"{base_url}/frame.jpg"
    health = fetch_health(base_url)
    diagnostics_enabled = frame_diagnostics_enabled(health)
    requested_probe_every = max(0, args.probe_every)
    if requested_probe_every > 0 and not diagnostics_enabled:
        raise RuntimeError(
            "frame diagnostics are disabled but probe-every is > 0. "
            "Set --probe-every 0 or enable FRAME_JPEG_ENABLED."
        )

    metadata_samples, frame_probes, _peer_id = asyncio.run(
        collect_sync_samples(
            ws_url=ws_url,
            frame_url=frame_url,
            base_url=base_url,
            samples=max(20, args.samples),
            probe_every=requested_probe_every,
            jitter_ms=max(0.0, args.jitter_ms),
            burst_every=max(0, args.burst_every),
            burst_pause_ms=max(0.0, args.burst_pause_ms),
            hold_webrtc_session=args.require_webrtc_active,
        )
    )

    metrics = summarize(metadata_samples, frame_probes)

    if int(metrics["metadata_seq_non_increasing_count"]) > 0:
        raise RuntimeError("metadata frameSeq is not strictly increasing")

    if int(metrics["frame_probe_non_increasing_count"]) > 0:
        raise RuntimeError("frame probe X-Frame-Seq is not strictly increasing")

    if float(metrics["metadata_server_minus_frame_ms_p95"]) > args.max_server_minus_frame_ms_p95:
        raise RuntimeError(
            "server-minus-frame skew p95 exceeded threshold "
            f"({metrics['metadata_server_minus_frame_ms_p95']:.2f} > {args.max_server_minus_frame_ms_p95:.2f})"
        )

    if float(metrics["metadata_receive_minus_frame_ms_p95"]) > args.max_receive_minus_frame_ms_p95:
        raise RuntimeError(
            "receive-minus-frame lag p95 exceeded threshold "
            f"({metrics['metadata_receive_minus_frame_ms_p95']:.2f} > {args.max_receive_minus_frame_ms_p95:.2f})"
        )

    if args.require_webrtc_active:
        runtime_metrics = assert_webrtc_runtime_activity(
            base_url=base_url,
            min_frames_pushed=max(1, args.min_webrtc_frames_pushed),
            min_track_frames_emitted=max(1, args.min_webrtc_frames_emitted),
            retries=max(1, args.webrtc_runtime_retries),
            retry_delay_s=max(0.05, args.webrtc_runtime_retry_delay),
        )
        print(f"- webrtc_runtime_frames_pushed: {runtime_metrics['frames_pushed']}")
        print(f"- webrtc_runtime_track_frames_emitted_total: {runtime_metrics['track_frames_emitted_total']}")
        print(f"- webrtc_runtime_media_pipeline: {runtime_metrics['media_pipeline']}")


if __name__ == "__main__":
    main()

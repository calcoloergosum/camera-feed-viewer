from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription

    WEBRTC_BENCH_IMPORT_ERROR: Exception | None = None
except Exception as error:  # pragma: no cover - dependency availability differs by environment
    WEBRTC_BENCH_IMPORT_ERROR = error


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


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


async def _wait_for_ice_complete(peer_connection: RTCPeerConnection, timeout_s: float = 2.0) -> None:
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


async def _wait_for_connection_settle(peer_connection: RTCPeerConnection, timeout_s: float = 0.35) -> None:
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


async def run_offer_cycle(base_url: str) -> tuple[bool, float, str]:
    if WEBRTC_BENCH_IMPORT_ERROR is not None:
        return False, 0.0, f"aiortc unavailable: {WEBRTC_BENCH_IMPORT_ERROR}"

    peer_connection = RTCPeerConnection()
    peer_id: str | None = None
    started = time.perf_counter()
    try:
        peer_connection.addTransceiver("video", direction="recvonly")
        offer = await peer_connection.createOffer()
        await peer_connection.setLocalDescription(offer)
        await _wait_for_ice_complete(peer_connection)

        local_description = peer_connection.localDescription
        if local_description is None or not local_description.sdp:
            return False, 0.0, "local offer unavailable"

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
            return False, 0.0, "answer missing sdp"
        if answer_type != "answer":
            return False, 0.0, f"unexpected answer type={answer_type}"
        if not isinstance(peer_id_value, str) or not peer_id_value.strip():
            return False, 0.0, "answer missing peerId"

        peer_id = peer_id_value
        await peer_connection.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return True, elapsed_ms, "ok"
    except Exception as error:
        return False, 0.0, str(error)
    finally:
        await _wait_for_connection_settle(peer_connection)
        await peer_connection.close()
        if peer_id:
            try:
                await asyncio.to_thread(_delete, f"{base_url}/webrtc/{peer_id}", 4.0)
            except Exception:
                pass


def run_benchmark(base_url: str, duration_s: float, request_interval_s: float, signaling_cycles: int) -> None:
    health_url = f"{base_url}/health"

    health_count = 0
    latencies_ms: list[float] = []

    health_capture_fps: list[float] = []
    health_frame_age_ms: list[float] = []
    health_metadata_mbps: list[float] = []
    health_webrtc_sessions: list[float] = []

    start = time.perf_counter()

    while True:
        now = time.perf_counter()
        if now - start >= duration_s:
            break

        health_start = time.perf_counter()
        try:
            health = fetch_json(health_url, timeout=3.0)
        except URLError:
            time.sleep(0.1)
            continue

        health_elapsed_ms = (time.perf_counter() - health_start) * 1000.0
        latencies_ms.append(health_elapsed_ms)
        health_count += 1

        capture_fps = health.get("capture_fps_estimate")
        frame_age_ms = health.get("latest_frame_age_ms")
        if isinstance(capture_fps, (int, float)):
            health_capture_fps.append(float(capture_fps))
        if isinstance(frame_age_ms, (int, float)):
            health_frame_age_ms.append(float(frame_age_ms))

        delivery = health.get("delivery")
        if isinstance(delivery, dict):
            metadata = delivery.get("metadata")
            webrtc = delivery.get("webrtc")

            if isinstance(metadata, dict):
                metadata_mbps = metadata.get("throughput_mbps_estimate")
                if isinstance(metadata_mbps, (int, float)):
                    health_metadata_mbps.append(float(metadata_mbps))

            if isinstance(webrtc, dict):
                active_sessions = webrtc.get("active_sessions")
                if isinstance(active_sessions, (int, float)):
                    health_webrtc_sessions.append(float(active_sessions))

        sleep_for = request_interval_s - (time.perf_counter() - health_start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    elapsed_s = max(0.001, time.perf_counter() - start)

    health_poll_fps = health_count / elapsed_s

    signaling_successes = 0
    signaling_failures = 0
    signaling_latencies_ms: list[float] = []
    signaling_failure_message: str | None = None
    if signaling_cycles > 0:
        for _ in range(signaling_cycles):
            ok, latency_ms, detail = asyncio.run(run_offer_cycle(base_url))
            if ok:
                signaling_successes += 1
                signaling_latencies_ms.append(latency_ms)
            else:
                signaling_failures += 1
                if signaling_failure_message is None:
                    signaling_failure_message = detail

    print("Benchmark results")
    print(f"- base_url: {base_url}")
    print(f"- elapsed_s: {elapsed_s:.2f}")
    print(f"- health_polls: {health_count}")
    print(f"- health_poll_fps: {health_poll_fps:.2f}")

    if latencies_ms:
        sorted_latencies = sorted(latencies_ms)
        p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95) - 1))
        print(f"- health_latency_ms_mean: {statistics.mean(latencies_ms):.2f}")
        print(f"- health_latency_ms_p95: {sorted_latencies[p95_index]:.2f}")

    if health_capture_fps:
        print(f"- health_capture_fps_mean: {statistics.mean(health_capture_fps):.2f}")

    if health_frame_age_ms:
        print(f"- health_frame_age_ms_mean: {statistics.mean(health_frame_age_ms):.2f}")

    if health_metadata_mbps:
        print(f"- health_metadata_mbps_mean: {statistics.mean(health_metadata_mbps):.2f}")

    if health_webrtc_sessions:
        print(f"- health_webrtc_sessions_mean: {statistics.mean(health_webrtc_sessions):.2f}")
        print(f"- health_webrtc_sessions_max: {max(health_webrtc_sessions):.0f}")

    if signaling_cycles > 0:
        print(f"- signaling_cycles_requested: {signaling_cycles}")
        print(f"- signaling_cycles_success: {signaling_successes}")
        print(f"- signaling_cycles_failure: {signaling_failures}")

        if signaling_latencies_ms:
            sorted_latencies = sorted(signaling_latencies_ms)
            p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95) - 1))
            print(f"- signaling_latency_ms_mean: {statistics.mean(signaling_latencies_ms):.2f}")
            print(f"- signaling_latency_ms_p95: {sorted_latencies[p95_index]:.2f}")

        if signaling_failure_message:
            print(f"- signaling_failure_example: {signaling_failure_message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark backend WebRTC-era delivery health and signaling")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument(
        "--signaling-cycles",
        type=int,
        default=0,
        help="Number of offer/answer handshake cycles to benchmark (0 disables signaling benchmark)",
    )
    args = parser.parse_args()

    run_benchmark(
        base_url=args.base_url.rstrip("/"),
        duration_s=max(1.0, args.duration),
        request_interval_s=max(0.05, args.interval),
        signaling_cycles=max(0, args.signaling_cycles),
    )


if __name__ == "__main__":
    main()

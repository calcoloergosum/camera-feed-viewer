from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_frame(url: str, timeout: float) -> tuple[int, int | None]:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
        seq_header = response.headers.get("X-Frame-Seq")

    seq: int | None = None
    if seq_header is not None:
        try:
            seq = int(seq_header)
        except ValueError:
            seq = None

    return len(payload), seq


def run_benchmark(base_url: str, duration_s: float, request_interval_s: float) -> None:
    frame_url = f"{base_url}/frame.jpg"
    health_url = f"{base_url}/health"

    request_count = 0
    bytes_total = 0
    latencies_ms: list[float] = []
    seq_first: int | None = None
    seq_last: int | None = None

    health_capture_fps: list[float] = []
    health_frame_age_ms: list[float] = []

    start = time.perf_counter()
    next_health_poll = start

    while True:
        now = time.perf_counter()
        if now - start >= duration_s:
            break

        if now >= next_health_poll:
            try:
                health = fetch_json(health_url, timeout=2.0)
                capture_fps = health.get("capture_fps_estimate")
                frame_age_ms = health.get("latest_frame_age_ms")
                if isinstance(capture_fps, (int, float)):
                    health_capture_fps.append(float(capture_fps))
                if isinstance(frame_age_ms, (int, float)):
                    health_frame_age_ms.append(float(frame_age_ms))
            except URLError:
                pass

            next_health_poll = now + 1.0

        frame_start = time.perf_counter()
        try:
            payload_size, seq = fetch_frame(frame_url, timeout=3.0)
        except URLError:
            time.sleep(0.1)
            continue

        request_elapsed_ms = (time.perf_counter() - frame_start) * 1000.0
        latencies_ms.append(request_elapsed_ms)
        bytes_total += payload_size
        request_count += 1

        if seq is not None:
            if seq_first is None:
                seq_first = seq
            seq_last = seq

        sleep_for = request_interval_s - (time.perf_counter() - frame_start)
        if sleep_for > 0:
            time.sleep(sleep_for)

    elapsed_s = max(0.001, time.perf_counter() - start)

    request_fps = request_count / elapsed_s
    mbps = (bytes_total * 8.0) / elapsed_s / 1_000_000
    seq_fps = None
    if seq_first is not None and seq_last is not None and seq_last >= seq_first:
        seq_fps = (seq_last - seq_first) / elapsed_s

    print("Benchmark results")
    print(f"- base_url: {base_url}")
    print(f"- elapsed_s: {elapsed_s:.2f}")
    print(f"- frame_requests: {request_count}")
    print(f"- request_fps: {request_fps:.2f}")
    print(f"- throughput_mbps: {mbps:.2f}")

    if seq_fps is not None:
        print(f"- delivered_seq_fps: {seq_fps:.2f}")

    if latencies_ms:
        sorted_latencies = sorted(latencies_ms)
        p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95) - 1))
        print(f"- latency_ms_mean: {statistics.mean(latencies_ms):.2f}")
        print(f"- latency_ms_p95: {sorted_latencies[p95_index]:.2f}")

    if health_capture_fps:
        print(f"- health_capture_fps_mean: {statistics.mean(health_capture_fps):.2f}")

    if health_frame_age_ms:
        print(f"- health_frame_age_ms_mean: {statistics.mean(health_frame_age_ms):.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark backend frame delivery")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--interval", type=float, default=0.02)
    args = parser.parse_args()

    run_benchmark(
        base_url=args.base_url.rstrip("/"),
        duration_s=max(1.0, args.duration),
        request_interval_s=max(0.005, args.interval),
    )


if __name__ == "__main__":
    main()

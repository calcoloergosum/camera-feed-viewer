from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

import websockets


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


async def collect_sync_samples(
    ws_url: str,
    frame_url: str,
    samples: int,
    probe_every: int,
    jitter_ms: float,
    burst_every: int,
    burst_pause_ms: float,
) -> tuple[list[MetadataSample], list[FrameProbe]]:
    metadata_samples: list[MetadataSample] = []
    frame_probes: list[FrameProbe] = []

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

    return metadata_samples, frame_probes


def summarize(metadata_samples: list[MetadataSample], frame_probes: list[FrameProbe]) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate backend video/metadata synchronization")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument("--probe-every", type=int, default=12)
    parser.add_argument("--jitter-ms", type=float, default=60.0)
    parser.add_argument("--burst-every", type=int, default=30)
    parser.add_argument("--burst-pause-ms", type=float, default=180.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    ws_url = f"{base_url.replace('http://', 'ws://').replace('https://', 'wss://')}/ws/metadata"
    frame_url = f"{base_url}/frame.jpg"

    metadata_samples, frame_probes = asyncio.run(
        collect_sync_samples(
            ws_url=ws_url,
            frame_url=frame_url,
            samples=max(20, args.samples),
            probe_every=max(1, args.probe_every),
            jitter_ms=max(0.0, args.jitter_ms),
            burst_every=max(0, args.burst_every),
            burst_pause_ms=max(0.0, args.burst_pause_ms),
        )
    )

    summarize(metadata_samples, frame_probes)


if __name__ == "__main__":
    main()

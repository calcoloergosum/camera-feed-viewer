# Camera feed viewer

Lightweight plugin-first camera streaming with synchronized overlay rendering.

## Motivation

- I wanted a realtime feedback viewer on my ML project, but existing camera streaming solutions (e.g. rerun) felt too heavylifting for my needs.
- I wanted to utilize agentic AI techniques to help design and implement the system.
- I wanted to learn more about WebRTC video delivery and synchronization techniques by building a custom solution from the ground up.
- I wanted to build a reusable backend runtime with a clear plugin contract for ingesting frames from external camera owners, and a simple frontend that can render video plus synchronized overlays.
- From here is written by code agents :P

## Quickstart

Launch Frontend: `npm run dev`

Launch backend: `python -m backend.scripts.main`

The project delivers a React + PixiJS frontend and a FastAPI backend where camera ownership stays external to the API process. The backend serves WebRTC video, JPEG diagnostic snapshots, and websocket metadata with sequence/timestamp context for sync.

## What This Delivers

- Backend-first video delivery for frontend playback (no browser `getUserMedia` path).
- Plugin callback ingestion contract for external camera owners.
- Synchronization contract across frame headers and metadata messages.
- Frontend reconnect/backoff behavior with diagnostics and manual retry control.
- Health, smoke, sync, and benchmark tooling for repeatable validation.

## Architecture

### Data Flow

1. External owner captures RGB frames.
2. Owner pushes each frame into backend callback (`on_camera_frame`).
3. Backend stores latest frame context and serves WebRTC tracks with H.264-preferred codec negotiation.
4. Frontend performs SDP offer/answer against backend WebRTC signaling and renders remote video track in `<video>`.
5. Frontend reads `WS /ws/metadata` for overlays.
6. Frontend sync selector matches metadata frame timing to video timeline.

### Core Interfaces

- `GET /health`
- `GET /frame.jpg` (diagnostics, optional)
- `POST /webrtc/offer`
- `DELETE /webrtc/{peer_id}`
- `WS /ws/metadata`
- `backend.app.plugin_api.get_frame_callback(...)`

## Repository Layout

- `src/`: React frontend and PixiJS overlay rendering.
- `backend/app/`: FastAPI runtime, frame store, telemetry, plugin API.
- `backend/scripts/`: harness, smoke check, sync validator, benchmark.

## Runbook

## 1) Frontend

Install dependencies:

```bash
npm install
```

Run dev server:

```bash
npm run dev
```

Optional frontend overrides:

```bash
VITE_BACKEND_BASE_URL=http://127.0.0.1:8000 npm run dev
VITE_SYNC_TOLERANCE_MS=220 VITE_SYNC_MAX_AGE_MS=2500 npm run dev
```

Build and lint:

```bash
npm run lint && npm run build
```

## 2) Backend

Install backend dependencies:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
```

Run backend API (plugin mode):

```bash
.venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Important behavior in plugin mode:

- Backend does not own camera capture.
- Before first frame ingestion, `/frame.jpg` returns `503` when enabled.
- Set `FRAME_JPEG_ENABLED=0` to disable `/frame.jpg` in long-running WebRTC-only deployments.
- Metadata websocket waits for first valid frame.
- WebRTC signaling is available at `/webrtc/offer`.

## 3) Legacy Owner Harness (for local integration)

Use the harness to emulate an external owner process that captures and feeds frames:

```bash
.venv/bin/python backend/scripts/main.py --source auto --host 127.0.0.1 --port 8000
```

Optional source modes:

```bash
.venv/bin/python backend/scripts/main.py --source cv2 --camera-index 0 --width 1280 --height 720 --fps 30 --host 127.0.0.1 --port 8000
.venv/bin/python backend/scripts/main.py --source random --width 1280 --height 720 --fps 30 --host 127.0.0.1 --port 8000
```

## Plugin Contract

External camera owner calls:

```python
on_camera_frame(frame, seq=None, timestamp_ms=None, stream_id="default")
```

External metadata owner calls:

```python
on_metadata_payload(payload)
```

Contract details:

- `frame`: RGB `numpy.ndarray`, shape `(height, width, 3)`.
- `seq`: monotonically increasing sequence id from owner.
- `timestamp_ms`: capture time as epoch milliseconds.
- `stream_id`: optional source identifier.

Get callback from backend runtime:

```python
from backend.app.plugin_api import get_frame_callback, get_metadata_callback
from backend.app.server import app

frame_callback = get_frame_callback(app)
metadata_callback = get_metadata_callback(app)
```

Metadata payload must include:

- `timestampMs`
- `serverTimestampMs`
- `frameSeq`
- `sourceWidth`
- `sourceHeight`
- `items`

## Synchronization Contract

### Frame Endpoint

`GET /frame.jpg` includes:

- `X-Frame-Seq`
- `X-Frame-Timestamp-Ms`

### Metadata Websocket

`WS /ws/metadata` emits:

- `timestampMs`
- `serverTimestampMs`
- `frameSeq`
- `items`

Metadata messages are emitted only after a frame has been ingested and at least one metadata payload has been provided through `on_metadata_payload(payload)`.

Frontend uses these fields to align metadata to the current video timeline and choose the nearest valid overlay frame within configurable tolerance and max-age windows.

### WebRTC Signaling

Frontend sends local offer SDP:

`POST /webrtc/offer`

Request body:

- `sdp`
- `type` (`offer`)
- `peerId` (optional for reconnect)

Response body:

- `peerId`
- `sdp`
- `type` (`answer`)

## Stream Profiles And Env Tuning

Profiles:

- `low`: `960x540`, `24 fps`, JPEG quality `70`
- `balanced`: `1280x720`, `30 fps`, JPEG quality `75`
- `high`: `1920x1080`, `30 fps`, JPEG quality `82`

Example profile startup:

```bash
STREAM_PROFILE=balanced .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Common backend env overrides:

- `STREAM_FPS`
- `FRAME_WIDTH`
- `FRAME_HEIGHT`
- `JPEG_QUALITY`
- `FRAME_JPEG_ENABLED`
- `METADATA_FPS`
- `METRICS_LOG_INTERVAL_SEC`
- `LOG_LEVEL`

## Validation Toolkit

Smoke checks:

```bash
.venv/bin/python backend/scripts/smoke_check.py --base-url http://127.0.0.1:8000
.venv/bin/python backend/scripts/smoke_check.py --mode plugin-empty --base-url http://127.0.0.1:8000 --metadata-timeout 1.5
```

Sync validation:

```bash
.venv/bin/python backend/scripts/validate_sync.py --base-url http://127.0.0.1:8000 --samples 100 --probe-every 10 --jitter-ms 90 --burst-every 25 --burst-pause-ms 220
.venv/bin/python backend/scripts/validate_sync.py --mode plugin-empty --base-url http://127.0.0.1:8000 --metadata-timeout 1.5
.venv/bin/python backend/scripts/validate_sync.py --base-url http://127.0.0.1:8000 --probe-every 0
.venv/bin/python backend/scripts/validate_sync.py --mode harness --base-url http://127.0.0.1:8000 --require-webrtc-active --min-webrtc-frames-pushed 20 --min-webrtc-frames-emitted 5
```

Benchmarking:

```bash
.venv/bin/python backend/scripts/benchmark_stream.py --base-url http://127.0.0.1:8000 --duration 20 --interval 0.25
.venv/bin/python backend/scripts/benchmark_stream.py --base-url http://127.0.0.1:8000 --duration 15 --signaling-cycles 5
```

Bytecode compile checks:

```bash
.venv/bin/python -m compileall backend/app backend/scripts
```

## Operational Runbook

Preferred startup order:

1. Start external owner (or harness).
2. Start backend API.
3. Run smoke check.
4. Start frontend with matching backend base URL.

Port recovery pattern:

```bash
.venv/bin/python backend/scripts/main.py --source auto --host 127.0.0.1 --port 8014
VITE_BACKEND_BASE_URL=http://127.0.0.1:8014 npm run dev
```

## Troubleshooting

If frontend shows reconnect loops:

- Confirm backend health endpoint responds.
- Confirm frontend `VITE_BACKEND_BASE_URL` matches backend port.
- Check browser console for WebRTC signaling/connection errors.

If metadata count stays zero:

- Confirm frames are being ingested by owner process.
- In plugin-empty mode, this is expected until first frame ingestion.
- Reinstall backend requirements if websocket dependency is missing.

If playback feels choppy:

- Lower profile (`STREAM_PROFILE=low`) or lower frame dimensions/fps.
- Inspect `/health` fields:
  - `capture_fps_estimate`
  - `latest_frame_age_ms`
  - `delivery` throughput metrics
  - `overlay_lag_proxy_ms_estimate`
  - `webrtc_runtime` counters (`media_pipeline`, `frames_pushed`, `track_frames_emitted_total`, `track_drop_count`)

If `/frame.jpg` returns 404:

- `FRAME_JPEG_ENABLED=0` is active and diagnostics endpoint is intentionally disabled.
- Use `GET /health` for runtime state and WebRTC counters.
- For sync runs in this mode, use `backend/scripts/validate_sync.py --probe-every 0`.

## Version

Current local baseline: plugin-first runtime with synchronized overlay diagnostics and validation tooling.

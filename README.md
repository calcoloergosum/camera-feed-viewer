# Video Server

React + PixiJS frontend and Python backend for camera video plus metadata overlays.

## Frontend

Frontend no longer opens a local browser camera. It reads camera frames only from
the backend endpoints.

Frontend resilience features:

- Automatic reconnect with exponential backoff when backend feed is unavailable.
- Diagnostics panel with backend URL, reconnect attempts, and last successful frame time.
- In-stage fallback state with manual `Retry now` action.

Install dependencies:

```bash
npm install
```

Run development server:

```bash
npm run dev
```

Optional backend URL override:

```bash
VITE_BACKEND_BASE_URL=http://127.0.0.1:8000 npm run dev
```

Optional sync tuning overrides:

```bash
VITE_SYNC_TOLERANCE_MS=220 VITE_SYNC_MAX_AGE_MS=2500 npm run dev
```

Build for production:

```bash
npm run build
```

## Backend (Plugin Mode: External Camera Owner)

Core backend runtime no longer owns camera capture. It expects an external owner
script/process to capture frames and push them through the in-process callback
returned by `backend.app.plugin_api.get_frame_callback(...)`.

Legacy behavior can still be emulated with the harness script:

```bash
.venv/bin/python backend/scripts/main.py --source auto --host 127.0.0.1 --port 8000
```

Endpoints:

- `GET /health`
- `GET /frame.jpg` (single JPEG)
- `GET /stream.mjpeg` (continuous MJPEG stream)
- `WS /ws/metadata` (overlay metadata stream)

Synchronization fields:

- `GET /frame.jpg` includes `X-Frame-Seq` and `X-Frame-Timestamp-Ms` headers.
- `WS /ws/metadata` emits `timestampMs`, `serverTimestampMs`, and `frameSeq` for alignment.

Current frontend display path uses `GET /stream.mjpeg` with `fps=30` target.

Plugin callback contract (external owner -> backend):

- `on_camera_frame(frame, seq=None, timestamp_ms=None, stream_id='default')`
- `frame`: RGB NumPy array shaped `(height, width, 3)`
- `seq`: monotonically increasing frame sequence number from owner
- `timestamp_ms`: epoch ms timestamp for capture time
- `stream_id`: optional source identifier

One-shot stream profile presets:

```bash
STREAM_PROFILE=low .venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

```bash
STREAM_PROFILE=balanced .venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

```bash
STREAM_PROFILE=high .venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

Profile defaults:

- `low`: 960x540, 24fps, JPEG quality 70
- `balanced`: 1280x720, 30fps, JPEG quality 75
- `high`: 1920x1080, 30fps, JPEG quality 82

Optional backend FPS override:

```bash
STREAM_FPS=30 .venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

Metadata stream FPS override:

```bash
METADATA_FPS=10 .venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

Legacy harness source options:

```bash
.venv/bin/python backend/scripts/main.py --source cv2 --camera-index 0 --width 1280 --height 720 --fps 30 --host 127.0.0.1 --port 8000
```

```bash
.venv/bin/python backend/scripts/main.py --source auto --host 127.0.0.1 --port 8000
```

Legacy harness smoothness tuning (when feed feels choppy):

```bash
.venv/bin/python backend/scripts/main.py --source cv2 --camera-index 0 --width 960 --height 540 --fps 24 --host 127.0.0.1 --port 8000
```

Notes:

- `Metadata frames dropped` in UI is metadata channel drop count, not video drop count.
- Check `/health` for `capture_fps_estimate`, `delivery`, and `latest_frame_age_ms` when diagnosing choppy playback.
- Check `/health` field `latest_frame_timestamp_ms` when validating sync alignment.

Benchmark script (effective frame delivery and latency):

```bash
.venv/bin/python backend/scripts/benchmark_stream.py --base-url http://127.0.0.1:8000 --duration 20 --interval 0.02
```

Synchronization validation (induced jitter + burst pauses):

```bash
.venv/bin/python backend/scripts/validate_sync.py --base-url http://127.0.0.1:8000 --samples 100 --probe-every 10 --jitter-ms 90 --burst-every 25 --burst-pause-ms 220
```

Plugin-empty synchronization preflight (no owner attached):

```bash
.venv/bin/python backend/scripts/validate_sync.py --mode plugin-empty --base-url http://127.0.0.1:8000 --metadata-timeout 1.5
```

Backend smoke check (health + frame headers + metadata WS contract):

```bash
.venv/bin/python backend/scripts/smoke_check.py --base-url http://127.0.0.1:8000
```

Plugin-empty smoke check (expects waiting state + frame 503 + no metadata messages):

```bash
.venv/bin/python backend/scripts/smoke_check.py --mode plugin-empty --base-url http://127.0.0.1:8000 --metadata-timeout 1.5
```

Install backend dependencies:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
```

If `ws/metadata` does not connect, reinstall requirements to ensure `websockets`
is present in the environment.

Run backend API:

```bash
.venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

Important: running backend API alone in plugin mode does not ingest frames.
If no external owner feeds `on_camera_frame`, `/frame.jpg` returns `503` and
metadata stream waits for first frame.

Quick checks:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/frame.jpg -o /tmp/frame.jpg
open http://127.0.0.1:8000/stream.mjpeg
```

If the frontend shows `Metadata frames received: 0`, verify:

- Backend is running (`uvicorn` command above).
- `http://127.0.0.1:8000/health` returns JSON.
- Frontend uses the same backend URL (set `VITE_BACKEND_BASE_URL` when needed).

If `Backend feed` stays on `connecting`, the backend is usually not running or not
reachable at the configured URL. Start it with:

```bash
.venv/bin/python -m uvicorn backend.app.server:app --reload --host 0.0.0.0 --port 8000
```

## Operational Runbook

Preferred local startup sequence:

1. Start external owner (for example `main.py`) so callback ingestion is active.
2. Start backend API.
3. Run smoke check once backend starts.
4. Start frontend with matching `VITE_BACKEND_BASE_URL`.

Port recovery strategy:

- If `8000` is busy, start backend on another port (for example `8014`) and export matching frontend base URL.
- Example:

```bash
.venv/bin/python backend/scripts/main.py --source auto --host 127.0.0.1 --port 8014
VITE_BACKEND_BASE_URL=http://127.0.0.1:8014 npm run dev
```

Key Stage E health metrics:

- `capture_read_ms_estimate`
- `capture_encode_ms_estimate`
- `delivery.snapshot|mjpeg|metadata.throughput_mbps_estimate`
- `overlay_lag_proxy_ms_estimate`

Periodic backend metric logs can be tuned with:

```bash
METRICS_LOG_INTERVAL_SEC=10 LOG_LEVEL=INFO .venv/bin/python -m uvicorn backend.app.server:app --host 127.0.0.1 --port 8000
```

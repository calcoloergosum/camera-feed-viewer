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

## Backend (Camera via cv2.VideoCapture)

The backend captures frames from camera using OpenCV (`cv2.VideoCapture`) when available.
If camera open fails and `CAMERA_SOURCE=auto`, it falls back to random NumPy frames.

Endpoints:

- `GET /health`
- `GET /frame.jpg` (single JPEG)
- `GET /stream.mjpeg` (continuous MJPEG stream)
- `WS /ws/metadata` (overlay metadata stream)

Synchronization fields:

- `GET /frame.jpg` includes `X-Frame-Seq` and `X-Frame-Timestamp-Ms` headers.
- `WS /ws/metadata` emits `timestampMs`, `serverTimestampMs`, and `frameSeq` for alignment.

Current frontend display path uses `GET /stream.mjpeg` with `fps=30` target.

One-shot stream profile presets:

```bash
STREAM_PROFILE=low .venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
STREAM_PROFILE=balanced .venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
STREAM_PROFILE=high .venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Profile defaults:

- `low`: 960x540, 24fps, JPEG quality 70
- `balanced`: 1280x720, 30fps, JPEG quality 75
- `high`: 1920x1080, 30fps, JPEG quality 82

Optional backend FPS override:

```bash
STREAM_FPS=30 .venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Metadata stream FPS override:

```bash
METADATA_FPS=10 .venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Camera source options:

```bash
CAMERA_SOURCE=cv2 CAMERA_INDEX=0 FRAME_WIDTH=1280 FRAME_HEIGHT=720 STREAM_FPS=30 \
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
CAMERA_SOURCE=auto .venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

Smoothness tuning (when feed feels choppy):

```bash
CAMERA_SOURCE=cv2 CAMERA_INDEX=0 FRAME_WIDTH=960 FRAME_HEIGHT=540 STREAM_FPS=24 JPEG_QUALITY=70 \
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
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

Install backend dependencies:

```bash
.venv/bin/python -m pip install -r backend/requirements.txt
```

If `ws/metadata` does not connect, reinstall requirements to ensure `websockets`
is present in the environment.

Run backend API:

```bash
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

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
.venv/bin/python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

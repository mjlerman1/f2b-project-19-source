# Deployment

BenchLink v1 runs as one container built from `Dockerfile` (standard library
only; `requirements.lock` is comment-only).

- Start command: `python src/main.py`. The kit reads `APP_DATA_DIR` (required),
  `PORT` (8080) and `HOST` (0.0.0.0 in the container).
- Mount a dedicated durable volume at `APP_DATA_DIR` (`/data` in the image). The
  code directory holds no state and can be replaced at any time.
- Behind HTTPS set `APPKIT_SECURE_COOKIES=1` (set in the image) and
  `APPKIT_PUBLIC_ORIGIN` to the public origin (for example
  `https://benchlink.example`) so the kit's CSRF origin check matches.
- The container runs as the unprivileged `app` user. No secrets are required.
- The `FROM` digest is the app kit placeholder; the operator pins the reviewed
  base image digest in the Dockerfile and the manifest before deploying.
- Health check: `GET /health` returns `{"status": "ok"}`.

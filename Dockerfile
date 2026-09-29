# BenchLink v1. The base image digest is the app kit placeholder; the operator
# pins the reviewed digest here and in the manifest at deployment.
FROM python:3.13.0-slim@sha256:0000000000000000000000000000000000000000000000000000000000000000
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --chown=root:root . /app
ENV HOST=0.0.0.0 PORT=8080 APP_DATA_DIR=/data APPKIT_SECURE_COOKIES=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8080
CMD ["python", "src/main.py"]

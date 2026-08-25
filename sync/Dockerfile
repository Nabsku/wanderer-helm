FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --gid 65532 wanderer-sync \
    && useradd --uid 65532 --gid 65532 --create-home --shell /usr/sbin/nologin wanderer-sync

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --requirement /app/requirements.txt
COPY wanderer_sync /app/wanderer_sync

USER 65532:65532
ENTRYPOINT ["python", "-m", "wanderer_sync.main"]

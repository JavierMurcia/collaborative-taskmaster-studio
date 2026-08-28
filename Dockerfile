# syntax=docker/dockerfile:1

FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN python -m venv "${VIRTUAL_ENV}"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
COPY studio ./studio
COPY agents ./agents
COPY infrastructure ./infrastructure
COPY adapters ./adapters
COPY sandbox ./sandbox
COPY schemas ./schemas

RUN python -m pip install --no-cache-dir ".[vertex,firestore,laboratory]"
RUN python -m pip install --no-cache-dir ".[storage]"


FROM python:3.13-slim AS runtime

ENV HOME=/tmp \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STUDIO_HOST=0.0.0.0

RUN groupadd --gid 10001 studio \
    && useradd --uid 10001 --gid studio --no-create-home --shell /usr/sbin/nologin studio \
    && mkdir -p /app/.studio-data /app/generated /app/projects \
    && chown -R 10001:10001 /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=10001:10001 app ./app
COPY --chown=10001:10001 studio ./studio
COPY --chown=10001:10001 agents ./agents
COPY --chown=10001:10001 infrastructure ./infrastructure
COPY --chown=10001:10001 adapters ./adapters
COPY --chown=10001:10001 sandbox ./sandbox
COPY --chown=10001:10001 schemas ./schemas

EXPOSE 8080

USER 10001:10001

CMD ["python", "-m", "app.main"]

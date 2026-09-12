FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/opt/agentic-plc-platform/src
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
        libc6-dev \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

COPY --from=conpot-src . /opt/conpot
COPY . /opt/agentic-plc-platform

RUN python -m pip install --no-cache-dir --upgrade pip uv \
    && uv pip install --system --no-cache \
        /opt/conpot \
        /opt/agentic-plc-platform \
        pytest

WORKDIR /opt/agentic-plc-platform

EXPOSE 5020/tcp
EXPOSE 8080/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/state', timeout=2).read()"

CMD ["python", "tools/run_agentic_honeypot.py", "--host", "0.0.0.0", "--modbus-port", "5020", "--hmi-port", "8080"]

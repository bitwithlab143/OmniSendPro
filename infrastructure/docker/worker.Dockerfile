# Delivery worker (data plane). Build context: repository root. No inbound ports (§39).
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY worker/pyproject.toml ./pyproject.toml
RUN python -c "import tomllib;print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/req.txt \
 && pip install -r /tmp/req.txt
COPY worker/ ./
RUN useradd --system --uid 10001 --home /app omnisend && chown -R omnisend /app
USER omnisend
STOPSIGNAL SIGTERM
CMD ["python", "worker.py"]

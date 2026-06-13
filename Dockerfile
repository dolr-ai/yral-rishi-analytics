FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/dolr-ai/yral-rishi-analytics"
LABEL org.opencontainers.image.description="Yral Analytics — read-only internal analytics service"

RUN groupadd --system --gid 1001 appuser && \
    useradd  --system --uid 1001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Mirror the chat service: app/ contents land at WORKDIR root so imports are
# bare (`import config`). SYMMETRY across projects.
COPY --chown=appuser:appuser app/ .

USER appuser

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]

# AgentCore Runtime requires an ARM64 container listening on port 8080 with a
# GET /ping health check. Alpine base to keep the image small and avoid the
# Debian userland CVEs a slim image would carry.

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-alpine AS builder

WORKDIR /app

RUN apk add --no-cache gcc musl-dev libffi-dev

COPY requirements.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-alpine

WORKDIR /app

RUN apk add --no-cache curl && rm -rf /var/cache/apk/*

COPY --from=builder /install /usr/local
COPY app/ /app/app/

RUN addgroup -g 1000 agentcore && \
    adduser -D -u 1000 -G agentcore agentcore && \
    chown -R agentcore:agentcore /app
USER agentcore

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/ping').read()" || exit 1

CMD ["python", "-m", "app.entrypoint"]

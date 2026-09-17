FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Node.js + git:Drivesoid 情绪 sidecar(随 zeabur-start.sh 首次启动时克隆到 /data 并常驻)
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm git \
    && rm -rf /var/lib/apt/lists/*

# Eventide 身体涨落引擎(纯本地 Python,无模型调用;git 依赖上面已装;
# 装不上时 api_loop 自动降级跳过,不阻塞构建)
RUN pip install --no-cache-dir "git+https://github.com/chuli1122/Eventide.git" || echo "[warn] eventide install failed, body engine disabled"

COPY backend /app/backend
COPY examples /app/examples
COPY web /app/web
COPY zeabur-start.sh /app/zeabur-start.sh
RUN chmod +x /app/zeabur-start.sh

ENV PYTHONUNBUFFERED=1
ENV RELAY_DB=/data/relay.db
ENV RELAY_UPLOAD_DIR=/data/uploads
ENV RELAY_BRAIN_FILE=/data/brain_target
ENV LOOP_CONFIG=/data/api_loop.config.json
ENV RELAY_WEB_DIR=/app/web

EXPOSE 8080

CMD ["/app/zeabur-start.sh"]

FROM python:3.11-slim

# 作用：不生成 .pyc、日志实时输出、pip 不留缓存包。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖单独一层：改业务代码不会触发重新安装依赖。
# ONNX 路线下 requirements.txt 不含 PyTorch，镜像比 sentence-transformers 方案小一个数量级。
COPY requirements.txt .
# 构建时可换 PyPI 镜像源。国内主机（如阿里云 ECS）建议：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt

COPY gateway ./gateway
COPY radar ./radar
COPY scripts ./scripts
COPY .env.example ./.env.example

# 建好运行目录，并创建非 root 用户；容器内进程不再具备提权能力。
RUN mkdir -p /app/data /app/models \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000 8001

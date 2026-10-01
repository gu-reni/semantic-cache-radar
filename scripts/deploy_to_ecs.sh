#!/usr/bin/env bash
#
# 把本地代码同步到阿里云 ECS，并重建雷达容器。
#
# 为什么要有这个脚本：ECS 上的 /opt/semantic-cache-radar 不是 git 仓库
# （整个项目是 rsync 过去的），所以部署这件事一直是靠手敲命令。
# 而手敲的最大风险是漏掉排除项 —— 那个目录里有三样东西在本地是没有的：
#
#   .env      root 600，线上唯一一份，里面有 LLM_API_KEY
#   data/     线上真实采集数据（radar.db）与向量库（chroma/）
#   models/   113MB 的 ONNX 模型，线上早就下好了
#
# 任何一样被本地版本覆盖（本地 .env 是模板、本地 data/ 是测试库），
# 线上的服务当场坏掉。所以排除项写死在这里，不依赖执行时的心情。
#
# 用法：
#   DRY=1 scripts/deploy_to_ecs.sh     # 只说会改什么，不动任何东西
#   scripts/deploy_to_ecs.sh           # 真的同步 + 重建 + 重启
#
set -euo pipefail

HOST="${HOST:-root@47.95.252.95}"
TARGET="${TARGET:-/opt/semantic-cache-radar}"
DRY="${DRY:-0}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 排除项分三类，缺一不可：
#   线上独有的数据  —— 覆盖即事故
#   本地构建产物    —— 传过去没用，还占带宽
EXCLUDES=(
  --exclude='.env'
  --exclude='.env.*'
  --exclude='data/'
  --exclude='models/'
  --exclude='.venv/'
  --exclude='.git/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='.pytest_cache/'
  --exclude='.ruff_cache/'
  --exclude='*.db'
  --exclude='*.db.before-migration'
)

RSYNC_FLAGS=(-rlpt --human-readable --itemize-changes)
if [ "$DRY" = "1" ]; then
  RSYNC_FLAGS+=(--dry-run)
  echo "════ DRY RUN：只报告会改什么，不做任何改动 ════"
fi

# 注意不加 --delete：这次部署只新增与更新文件，没有被删掉的文件需要同步。
# 开 --delete 一旦排除项写漏，就是线上数据被删；收益不抵风险。
echo "── 1/4 同步源码到 ${HOST}:${TARGET} ──"
rsync "${RSYNC_FLAGS[@]}" "${EXCLUDES[@]}" ./ "${HOST}:${TARGET}/"

if [ "$DRY" = "1" ]; then
  echo
  echo "DRY RUN 结束。确认上面没有 .env / data/ / models/ 出现，再去掉 DRY=1 执行。"
  exit 0
fi

echo
echo "── 2/4 确认线上受保护的文件仍在（这三样不该被上面那步碰过）──"
ssh "$HOST" "set -e
  test -f ${TARGET}/.env && echo '  .env        在（'\"\$(stat -c%s ${TARGET}/.env)\"' 字节）'
  test -f ${TARGET}/data/radar.db && echo '  radar.db    在（'\"\$(stat -c%s ${TARGET}/data/radar.db)\"' 字节）'
  test -d ${TARGET}/models/multilingual-e5-small && echo '  模型目录    在'
"

echo
echo "── 3/4 重建雷达镜像 ──"
ssh "$HOST" "cd ${TARGET} && docker compose build radar"

echo
echo "── 4/4 重启并等健康检查 ──"
ssh "$HOST" "cd ${TARGET} && docker compose up -d && sleep 8 && docker compose ps --format 'table {{.Name}}\t{{.Status}}'"

echo
echo "完成。下一步核验："
echo "  ssh ${HOST} \"docker compose -f ${TARGET}/docker-compose.yml exec radar python -m radar.app.runner --once\""
echo "  （重新采集一次，新条目才会带上指标；旧条目在当时就没存，不是丢了）"

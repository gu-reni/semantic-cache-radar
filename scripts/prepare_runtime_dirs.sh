#!/usr/bin/env bash
# 作用：准备容器运行所需的数据目录，并把属主交给容器内进程使用的 uid。
#
# 为什么需要这一步：
#   容器内进程以非 root 运行（uid 1000 的 appuser）。bind 挂载进来的目录，
#   属主取决于宿主机上的创建者——如果用 root 创建，容器内就写不进去，报：
#     PermissionError: [Errno 13] Permission denied: '/app/data/chroma'
#   模型目录同理会读不出来，报 FileNotFoundError 或 Permission denied。
#
#   本机恰好因为普通用户 uid 就是 1000 而侥幸可用，换一台机器（如以 root 部署的
#   云主机）就会失败，所以两个目录都要显式处理。
#
# 用法：bash scripts/prepare_runtime_dirs.sh
#
# 可用环境变量覆盖：
#   CONTAINER_UID / CONTAINER_GID   容器内进程的 uid/gid，默认都是 1000
set -euo pipefail

CONTAINER_UID="${CONTAINER_UID:-1000}"
CONTAINER_GID="${CONTAINER_GID:-1000}"
TARGET_OWNER="${CONTAINER_UID}:${CONTAINER_GID}"

echo "  容器内进程的属主设定：$TARGET_OWNER"
echo

failed=0
for dir in data models; do
  mkdir -p "$dir"
  owner="$(stat -c '%u:%g' "$dir")"

  if [ "$owner" = "$TARGET_OWNER" ]; then
    echo "  ✓ $dir 属主已是 $owner，无需调整"
    continue
  fi

  if chown -R "$TARGET_OWNER" "$dir" 2>/dev/null; then
    echo "  ✓ $dir 属主 $owner → $TARGET_OWNER"
  else
    echo "  ✗ $dir 属主为 $owner，调整失败（当前用户权限不足）"
    echo "      请以 root 执行： chown -R $TARGET_OWNER $dir"
    failed=1
  fi
done

echo
if [ "$failed" -ne 0 ]; then
  echo "  存在未处理项，容器启动后会因权限不足而报错。"
  exit 1
fi

if [ ! -f models/multilingual-e5-small/model.onnx ]; then
  echo "  ⚠️ 模型尚未下载，接下来请运行："
  echo "       bash scripts/download_embedding_model.sh"
  echo "     否则网关启动后会报 FileNotFoundError。"
  echo
else
  echo "  ✓ 模型已就位"
fi

echo "  目录准备完成。"

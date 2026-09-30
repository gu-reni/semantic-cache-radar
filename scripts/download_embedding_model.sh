#!/usr/bin/env bash
# 作用：下载语义缓存所需的 Embedding 模型（ONNX int8 量化版）。
#
# 为什么不用 sentence-transformers：
#   它会把 PyTorch 一起拉进来（镜像 1GB+，纯 CPU 机器上大半是浪费）。
#   ONNX 运行时的内存占用约为 torch 的 1/3，镜像也小得多。
#
# 为什么选 int8 量化版：
#   官方 intfloat 仓库的量化版要求 CPU 支持 AVX512-VNNI，普通云主机没有；
#   Xenova 这个量化版只需 AVX2，通用性好。
#
# 为什么走 hf-mirror：
#   国内直连 huggingface.co 不通（实测 000），hf-mirror.com 通（实测 200）。
#
# 用法：
#   bash scripts/download_embedding_model.sh [目标目录]
#   默认目标目录：models/multilingual-e5-small
#
# 可用环境变量覆盖：
#   EMBEDDING_REPO   模型仓库，默认 Xenova/multilingual-e5-small
#   HF_MIRROR        镜像地址，默认 https://hf-mirror.com
set -euo pipefail

# 默认落到项目根下的 models/，而不是调用者当前所在的目录。
# 否则从 /root 里执行会把 130MB 模型下到 /root/models 去，
# 脚本报成功，网关启动却找不到模型。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

REPO="${EMBEDDING_REPO:-Xenova/multilingual-e5-small}"
MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
TARGET="${1:-$PROJECT_ROOT/models/multilingual-e5-small}"

# 源文件路径:落盘文件名
FILES=(
  "onnx/model_quantized.onnx:model.onnx"
  "tokenizer.json:tokenizer.json"
  "config.json:config.json"
  "special_tokens_map.json:special_tokens_map.json"
  "tokenizer_config.json:tokenizer_config.json"
)

echo "  模型仓库：$REPO"
echo "  镜像地址：$MIRROR"
echo "  目标目录：$TARGET"
echo

mkdir -p "$TARGET"

for pair in "${FILES[@]}"; do
  src="${pair%%:*}"
  dst="${pair##*:}"
  if [ -s "$TARGET/$dst" ]; then
    echo "  跳过（已存在）：$dst"
    continue
  fi
  echo "  下载：$src → $dst"
  # 先写 .part 再改名，避免下载中断留下半截文件被当成可用模型
  curl -fL --retry 3 --retry-delay 2 --connect-timeout 20 \
       -o "$TARGET/$dst.part" "$MIRROR/$REPO/resolve/main/$src"
  mv "$TARGET/$dst.part" "$TARGET/$dst"
done

echo
echo "  产物校验："
for f in "$TARGET"/*; do
  [ -f "$f" ] || continue
  printf "    %-26s %8s  sha256:%s\n" \
    "$(basename "$f")" "$(du -h "$f" | cut -f1)" "$(sha256sum "$f" | cut -c1-16)"
done

echo
echo "  完成。模型目录：$TARGET"

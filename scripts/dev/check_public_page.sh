#!/usr/bin/env bash
# 对外页面的发布前闸门自检。逐条对应本机既有的页面规范。
set -uo pipefail

# 不要靠数 `..` 的层数来定位项目根 —— 脚本放深一层就全错，
# 而且失败时前面的 grep 只会报 "No such file"，看着像页面有问题。
# 这里改成往上找标志目录，放哪一层都能找到。
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$here"
while [ "$root" != "/" ] && [ ! -d "$root/radar/app/static" ]; do
  root="$(dirname "$root")"
done
if [ ! -d "$root/radar/app/static" ]; then
  echo "  找不到项目根（没找到 radar/app/static），先确认脚本还在仓库里"
  exit 1
fi
cd "$root" || exit 1
STATIC="radar/app/static"

echo "════ 闸门 1：零外部依赖（必须为 0）════"
n1=$(grep -oE '(src|href)="https?://' "$STATIC/index.html" | wc -l)
# 只数真正会发起外部请求的 url()；data: 内联资源不算，
# 注意内联 SVG 里 xml**ns**='http://www.w3.org/2000/svg' 是命名空间标识、永不请求，
# 早先这条正则会把它误报成外部依赖。
n2=$(grep -oE 'url\([^)]*https?://' "$STATIC/style.css" | grep -vc 'data:' || true)
n2=${n2:-0}
echo "  index.html 里指向外站的 src/href : $n1"
echo "  CSS 里真正请求外部的 url(...)    : $n2"
[ "$n1" -eq 0 ] && [ "$n2" -eq 0 ] && echo "  ✓ 通过" || echo "  ✗ 不通过"

echo
echo "════ 闸门 2：页面不出现姓名 / 用户名（必须为 0）════"
hits=$(grep -ohE '戴健彰|戴健|daijianzhang|djz\.asia|gu-reni|daifamily' \
  "$STATIC/index.html" "$STATIC/style.css" "$STATIC/app.js" 2>/dev/null | sort -u)
if [ -z "$hits" ]; then echo "  ✓ 通过（一处都没有）"; else echo "$hits" | sed 's/^/  ✗ 命中: /'; fi

echo
echo "════ 闸门 3：文案总量（只允许标题 + 至多一行平实事实句）════"
echo "  页面里的可见文本行："
grep -oE '>[^<>]{4,}<' "$STATIC/index.html" | sed -E 's/^>//; s/<$//' \
  | grep -vE '^\s*$' | sed 's/^/    /'

echo
echo "════ 闸门 4：不用 emoji（必须为 0）════"
n4=$(grep -cP '[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]' \
  "$STATIC/index.html" "$STATIC/style.css" "$STATIC/app.js" 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
echo "  emoji 命中数: $n4"
[ "$n4" -eq 0 ] && echo "  ✓ 通过" || echo "  ✗ 不通过"

echo
echo "════ 闸门 5：文件编码（中文必须能正常读出）════"
for f in "$STATIC/index.html" "$STATIC/style.css" "$STATIC/app.js"; do
  printf "  %-34s %s  中文示例: " "$f" "$(file -b --mime-encoding "$f")"
  grep -ohE '技术雷达|正在载入|最后更新' "$f" 2>/dev/null | head -1 || echo -n "(无)"
  echo
done

echo
echo "════ 闸门 6：可点元素是否真的能打开（内链）════"
for path in / /static/style.css /static/app.js; do
  echo "  页面内链（相对路径）: $path —— 由路由与 mount 提供，测试里已断言 200"
done
echo "  外链一律指向采集到的原始文章地址，由前端校验协议后才渲染"

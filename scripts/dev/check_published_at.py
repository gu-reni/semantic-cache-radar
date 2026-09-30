"""验证 published_at 的语义修复：必须是可被 Date 解析的 ISO 字符串。

这个 bug 只有真的把页面渲染出来、逐条看时间才会发现 —— 接口返回 200，
字段也在，只是内容前端解析不了，时间是空白的。
"""
import asyncio
import sys
from pathlib import Path

import httpx

# 不要把「从哪个目录调用」变成脚本能跑的前提：
# 这里自己往上找到项目根塞进 sys.path，从任何目录调用都能跑。
_root = Path(__file__).resolve().parent
while _root != _root.parent and not (_root / "radar" / "app").is_dir():
    _root = _root.parent
sys.path.insert(0, str(_root))

from radar.app.collectors import HackerNewsCollector  # noqa: E402


async def main() -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        items = await HackerNewsCollector(client, story_limit=3).fetch()

    print(f"  取到 {len(items)} 条")
    bad = 0
    for item in items:
        value = item.published_at
        if value is None:
            print(f"    {value!r:<34} (无时间)")
            continue
        # 模拟前端 new Date(value) 会怎么处理
        looks_like_unix = value.isdigit()
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(value)
            status = f"✓ ISO，解析为 {parsed.astimezone().strftime('%m-%d %H:%M')}"
        except ValueError:
            parsed = None
            status = "✗ 不是 ISO，前端会得到 Invalid Date"
            bad += 1
        if looks_like_unix:
            status = "✗ 是裸的 Unix 秒，前端 new Date() 会当成「年份」而失效"
            bad += 1
        print(f"    {value!r:<34} {status}")

    print()
    print(f"  不合格条目: {bad} 条" + ("  ✓ 全部通过" if bad == 0 else "  ✗ 仍有问题"))


asyncio.run(main())

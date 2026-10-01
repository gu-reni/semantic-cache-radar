"""决定对称比较该用哪个前缀 —— 在真实数据上实测，不照文档抄。

背景：multilingual-e5 要求两侧加不同前缀（query: / passage:）。
而「标题 ↔ 标题」两边同质，谁也不是对方的查询。前缀选错不会报错，
只会让相似度整体偏低，表现成「明明是一回事却判成不相似」。

判据不是「平均相似度是多少」，而是「正例与负例分得开不开」：
分离区间越宽，阈值越好定，误判越少。

正例来源：GitHub Trending 卡片本身就同时写着两种说法 ——
         标题是「owner repo」，同一行描述写着这个项目是干什么的。
         两者指同一个项目、用词完全不同，正是「同一件事的两种说法」。
         之所以现取而不是读库：库里那两样被合并进了同一个字段，
         描述早就被摘要覆盖掉了。
负例来源：本地库里真实存在的不同标题，随机配对。
"""

import asyncio
import random
import sqlite3
import sys
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve()
while not (_ROOT / "gateway").is_dir():
    _ROOT = _ROOT.parent
sys.path.insert(0, str(_ROOT))

from gateway.app.config import Settings
from gateway.app.embeddings import EmbeddingService
from radar.app.collectors import GitHubTrendingCollector

PREFIXES = {"query:": "query:", "passage:": "passage:", "(无前缀)": ""}
THRESHOLD = 0.92


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


async def collect_trending_pairs() -> list[tuple[str, str]]:
    """取真实卡片：标题是「owner repo」，描述写的是这个项目干什么。"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        items = await GitHubTrendingCollector(client, repository_limit=10).fetch()
    return [(item.title, item.summary) for item in items if item.summary and len(item.summary) > 25]


def load_pairs() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    positives = asyncio.run(collect_trending_pairs())

    db = _ROOT / "data" / "radar.db"
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    rows = list(connection.execute("SELECT title FROM radar_items"))

    titles = [row["title"] for row in rows if row["title"]]
    rng = random.Random(20261001)
    negatives = [(rng.choice(titles), rng.choice(titles)) for _ in range(200)]
    negatives = [(a, b) for a, b in negatives if a != b]
    return positives, negatives


def main() -> None:
    positives, negatives = load_pairs()
    print(f"正例 {len(positives)} 对（仓库标题 ↔ 真实描述）")
    print(f"负例 {len(negatives)} 对（互不相关的真实标题随机配对）")
    print()

    settings = Settings()
    service = EmbeddingService(settings)

    print(f"{'前缀':<10} {'正例最低':>9} {'负例最高':>9} {'分离区间':>9} {f'过 {THRESHOLD} 阈值':>14}")
    print("-" * 60)
    results = {}
    for label, prefix in PREFIXES.items():
        def encode(text: str, _prefix: str = prefix) -> list[float]:
            return service._encode(f"{_prefix} {text}".strip())

        pos = [cosine(encode(a), encode(b)) for a, b in positives]
        neg = [cosine(encode(a), encode(b)) for a, b in negatives]
        gap = min(pos) - max(neg)
        results[label] = (min(pos), max(neg), gap)
        verdict = "正例全部命中" if min(pos) > THRESHOLD else f"{sum(1 for p in pos if p > THRESHOLD)}/{len(pos)}"
        print(f"{label:<10} {min(pos):>9.4f} {max(neg):>9.4f} {gap:>9.4f} {verdict:>14}")

    best = max(results, key=lambda k: results[k][2])
    print()
    print(f"分离区间最宽的是：{best}（{results[best][2]:.4f}）")
    print("⇒ EmbeddingService.SYMMETRIC_PREFIX 应设为这个值")


if __name__ == "__main__":
    main()

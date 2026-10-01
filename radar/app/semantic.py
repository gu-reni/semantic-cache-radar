"""跨源语义归并：调用网关的 embedding 接口，把「疑似同一事件」的条目配对记下来。

为什么只记候选、不自动合并：

  雷达原先只能靠 URL 归一化（dedup_key）认出「两个源在说同一个项目」，
  抓不到「同一件事的两种说法」。网关已经能对标题算 384 维向量，
  于是一个自然的想法是：算两条标题的相似度，超过阈值就合并。

  但这个阈值现在拍不出来。实测已知：在标题这种短文本上，
  **互不相关的标题之间**余弦相似度就已经有 0.85~0.91，
  而网关现有的 0.92 阈值是为长提示词定的，不能照搬；
  库里目前也没有任何一对已标注的「同一事件」样本可以用来标定。

  所以正确做法是「先观测、不判断」：把相似度够得着观察下限的配对，
  连同相似度数值一起记进数据库，供将来用真实样本标定阈值。
  这里不做任何自动合并，页面上也只呈现事实（「另有 N 条疑似同一事件」），
  不下「已合并 / 就是同一件事」的断言。
"""

import math

import httpx

from radar.app.repository import RadarRepository

# 观察下限，不是判定阈值。
# 为什么是这个数：0.80 明显宽松，宽松到几乎只有「完全不沾边」的标题才会落在这
# 下面。这样做的目的是**宁可多记、不要漏记**（观测阶段偏召回），把「够得着观察」
# 的配对连同相似度一起留下来，让后续能画出相似度分布、再用真实样本标定阈值。
# 已知互不相关标题的相似度在 0.85~0.91，所以这个下限会记下大量噪声 ——
# 这是有意为之：噪声本身就是标定阈值所需的基线分布的一部分。
# 再次强调：这个值不是判定阈值，也不该被拿去自动合并。
OBSERVATION_FLOOR = 0.80

# 单次请求给网关算向量的批量上限，与网关接口的 input 上限（schemas.py 里
# max_length=64）对齐。为什么分块：一次塞几百条会让单次请求把 CPU 占满。
EMBED_BATCH_SIZE = 64

# 比较窗口：只与「最近 7 天、最多 200 条」的近期条目比。
# 为什么 7 天 / 200 条：同一事件在资讯流里的生命周期很短，比更久远的没有意义；
# 200 条是给相似度计算的硬上限，避免库大了以后每写一条都要全表算一遍。
RECENT_WINDOW_DAYS = 7
RECENT_ITEMS_LIMIT = 200


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """两个等长向量的余弦相似度，用标准库手写（不引新依赖）。

    网关返回的向量已经 L2 归一化，但这里不依赖这个前提，自己再做一遍归一化，
    免得哪天网关换实现、或有人传入未归一化的向量时结果悄悄变错。
    """
    if len(left) != len(right):
        raise ValueError("向量维度不一致，无法比较")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


class SemanticLinker:
    """给新条目算它与近期条目的语义相似度，记下疑似同源候选。

    与 GatewayEnricher 一样走网关、带共享令牌。算不出相似度时必须降级：
    让异常向上抛，由 RadarPipeline 兜住，条目照常落库 ——
    语义归并是观测性增强，不是落库的前置条件。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        gateway_url: str,
        repository: RadarRepository,
        auth_token: str | None = None,
    ) -> None:
        self._client = client
        self._gateway_url = gateway_url.rstrip("/")
        self._headers = {"X-Gateway-Token": auth_token} if auth_token else {}
        self._repository = repository

    async def link(self, item_id: int, title: str) -> None:
        """把 item_id 与近期条目两两比较，>= 观察下限的配对记入语义候选表。

        只算「新条目 vs 已有条目」：新条目的向量只算一次，已有条目按批算，
        这样每次写入的开销与近期条目数成正比，而不是平方。
        """
        recent = self._repository.recent_titles(
            exclude_id=item_id,
            limit=RECENT_ITEMS_LIMIT,
            days=RECENT_WINDOW_DAYS,
        )
        if not recent:
            return

        # 新标题 + 近期标题一起批量算向量；标题对标题是同质比较，走 symmetric。
        texts = [title] + [recent_title for _, recent_title in recent]
        embeddings = await self._embed_symmetric(texts)
        new_vector = embeddings[0]

        candidates: list[tuple[int, int, float]] = []
        for index, (other_id, _other_title) in enumerate(recent):
            similarity = cosine_similarity(new_vector, embeddings[index + 1])
            if similarity >= OBSERVATION_FLOOR:
                candidates.append((item_id, other_id, similarity))
        self._repository.record_semantic_links(candidates)

    async def _embed_symmetric(self, texts: list[str]) -> list[list[float]]:
        """批量调用 /v1/embeddings，按输入顺序返回向量。"""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            chunk = texts[start : start + EMBED_BATCH_SIZE]
            response = await self._client.post(
                f"{self._gateway_url}/v1/embeddings",
                headers=self._headers,
                json={"input": chunk, "input_type": "symmetric"},
            )
            response.raise_for_status()
            payload = response.json()
            # 按 index 还原顺序，而不是信返回的先后 —— 万一网关不保证顺序，
            # 这里也不会把「相似度」算到错配的两条标题头上。
            by_index = {entry["index"]: entry["embedding"] for entry in payload["data"]}
            vectors.extend(by_index[i] for i in range(len(chunk)))
        return vectors

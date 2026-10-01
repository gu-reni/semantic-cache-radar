from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RadarItem:
    source: str
    external_id: str
    title: str
    url: str
    published_at: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    metrics: dict[str, Any] | None = field(default=None, hash=False)
    """采集当时从源站原样取到的可量化信号。

    各源给的东西不一样，所以不强行统一成一套字段，只做「原样保留」：
      hackernews       points（点数）· comments（评论数）· author
      v2ex             replies（回复数）· node（分类）· author · last_touched
      github-trending  stars（总星数）· stars_today（今日新增星数）· language

    为什么要存下来：这些数字源站本来就在返回，原先采集器只留标题和链接、
    其余全丢，于是「这个项目最近是不是在涨」这类问题在数据层就再也答不了。
    这里只存事实，不做解释 —— 任何换算（热度、排名）都留给展示层算，
    这样口径改了不需要重采历史。
    """

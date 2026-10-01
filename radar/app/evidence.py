"""证据等级：由可测量的信号实时算出，规则公开、可复算。

四档：证据充分 / 信号在积累 / 刚出现 / 有红旗。

为什么等级必须由信号算、不能是模型的印象：
  本项目是证据驱动的情报工具，不做「你该用什么」这类人的判断。
  模型会按它见过的语料「感觉」一条资讯重不重要，那种感觉既不透明也不稳定，
  换一个模型、换一次采样结果就变了。而指标（点数、回复数、星数）与
  疑似同源候选数都是可复算的：同样的库、同样的规则，任何人重跑都得到同一档。
  所以等级不落库，每次读取时从「指标 + 候选数」实时推导 —— 规则一改，全部条目
  立刻按新规则重算，不必重采历史数据。
"""

from typing import Any

LEVEL_SUFFICIENT = "证据充分"
LEVEL_ACCUMULATING = "信号在积累"
LEVEL_EMERGING = "刚出现"
LEVEL_RED_FLAG = "有红旗"

# 各源的「够强」阈值：该源的主指标达到这个量级，才算「有分量的信号」。
# 为什么是这些数：它们是「数量级上有话题度」的粗分界线，不是标定值 ——
#   hackernews：points >= 100。HN 首页前列通常上百点，几十点还只是小范围传播。
#   v2ex：replies >= 20。V2EX 帖子大多只有个位数回复，20 楼以上已算有讨论。
#   github-trending：stars_today >= 100。Trending 榜上仓库一天涨上百星很常见，
#     一天只涨几十星的往往还只是刚起步。
# 这些数可以按后续观测调，但改数就是改规则，必须留痕、可复算。
STRONG_METRIC_BY_SOURCE: dict[str, tuple[str, int]] = {
    "hackernews": ("points", 100),
    "v2ex": ("replies", 20),
    "github-trending": ("stars_today", 100),
}

# 参与「有没有量化信号」判定的字段。author / node / language / last_touched
# 是身份或标签信息，不是量化信号，不参与红旗判定。
_QUANTITATIVE_FIELDS = ("points", "comments", "replies", "stars", "stars_today")


def evidence_level(source: str, metrics: dict[str, Any] | None) -> str:
    """由源站指标 + 疑似同源候选数推出证据等级。"""
    metrics = metrics or {}

    quantitative = {
        key: metrics[key]
        for key in _QUANTITATIVE_FIELDS
        if key in metrics and metrics[key] is not None
    }

    # 红旗：指标异常。三种判据，都写死在这里保证可复算：
    #  1. 一个量化指标都没有 —— 源站没给任何信号，没法基于证据做判断，必须标出来，
    #     不能和其它条目摆在一起假装有同等的证据基础；
    #  2. 任一量化指标不是数字或为负 —— 正常源站不会给出负点数/负回复/负星数，
    #     出现这种值说明解析出错或源站改版，数据本身不可信；
    #  3. stars_today > stars —— 今日新增星数不可能超过总星数，出现即数据自相矛盾。
    if not quantitative:
        return LEVEL_RED_FLAG
    for value in quantitative.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return LEVEL_RED_FLAG
        if value < 0:
            return LEVEL_RED_FLAG
    if (
        "stars" in quantitative
        and "stars_today" in quantitative
        and quantitative["stars_today"] > quantitative["stars"]
    ):
        return LEVEL_RED_FLAG

    strong = _is_strong(source, quantitative)
    cross_source = _cross_source_count(metrics)

    # 为什么这样分层：
    #  「多个独立来源同时说」是比任何单一指标都硬的信号 —— 所以只要互证达到 2 处，
    #   无论自身指标强弱，直接判「证据充分」；
    #  自身指标够强 + 至少一处互证，两个信号都到位，也算「证据充分」；
    #  只有其一（够强但没人互证，或有人互证但自己还不强）→「信号在积累」；
    #  有量化信号但很弱、且无人互证 →「刚出现」，只是被观测到，还没有证据说它重要。
    if cross_source >= 2 or (strong and cross_source >= 1):
        return LEVEL_SUFFICIENT
    if strong or cross_source >= 1:
        return LEVEL_ACCUMULATING
    return LEVEL_EMERGING


def _is_strong(source: str, quantitative: dict[str, Any]) -> bool:
    threshold = STRONG_METRIC_BY_SOURCE.get(source)
    if threshold is None:
        # 未知源：没给它定过阈值，就不强加「够不够强」的判断，一律当作不强 ——
        # 宁可保守，也不给没定义的源编一个数。
        return False
    field, floor = threshold
    value = quantitative.get(field)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= floor


def _cross_source_count(metrics: dict[str, Any]) -> int:
    """「有多少个不同来源在说同一件事」的粗计数。

    两个信号相加：
      corroborating_sources —— dedup_key 精确撞上（硬信号，URL 归一化命中别的源）；
      semantic_peer_count   —— 任务 1 记下的疑似同源候选数（软信号，语义相似）。
    相加得到的是上界：精确互证 + 语义疑似。分开标在注释里，避免误读成精确值。
    """
    exact = metrics.get("corroborating_sources")
    exact_count = len(exact) if isinstance(exact, list) else 0
    peer_count = metrics.get("semantic_peer_count", 0)
    if isinstance(peer_count, bool) or not isinstance(peer_count, int):
        peer_count = 0
    return exact_count + peer_count

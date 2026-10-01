"""证据等级的单元测试。

等级由「源站指标 + 疑似同源候选数」实时算出，规则写死在 radar.app.evidence 里。
这里把每一条判据都钉住，保证将来改规则时要么是有意为之、要么被测试拦住。
"""

from radar.app.evidence import (
    LEVEL_ACCUMULATING,
    LEVEL_EMERGING,
    LEVEL_RED_FLAG,
    LEVEL_SUFFICIENT,
    evidence_level,
)


def test_missing_metrics_is_red_flag() -> None:
    """一个量化指标都没有：没法基于证据做判断，必须标红旗，不能混在正常条目里。"""
    assert evidence_level("v2ex", None) == LEVEL_RED_FLAG
    assert evidence_level("hackernews", {}) == LEVEL_RED_FLAG
    assert evidence_level("github-trending", {"language": "Python"}) == LEVEL_RED_FLAG


def test_negative_metric_is_red_flag() -> None:
    """正常源站不会给负点数，出现负值说明数据异常。"""
    assert evidence_level("hackernews", {"points": -3}) == LEVEL_RED_FLAG


def test_stars_today_exceeding_stars_is_red_flag() -> None:
    """今日新增星数不可能超过总星数，出现即数据自相矛盾。"""
    assert evidence_level("github-trending", {"stars": 10, "stars_today": 50}) == LEVEL_RED_FLAG


def test_weak_metric_with_no_peers_is_emerging() -> None:
    """有信号但很弱、也没人互证：只是被观测到。"""
    assert evidence_level("hackernews", {"points": 5, "comments": 2}) == LEVEL_EMERGING


def test_strong_metric_with_no_peers_is_accumulating() -> None:
    """自身指标够强，但还没有别的来源互证。"""
    assert evidence_level("hackernews", {"points": 200}) == LEVEL_ACCUMULATING
    assert evidence_level("v2ex", {"replies": 30}) == LEVEL_ACCUMULATING
    assert evidence_level("github-trending", {"stars_today": 150}) == LEVEL_ACCUMULATING


def test_weak_metric_with_one_peer_is_accumulating() -> None:
    """自身不强，但有一处互证：信号在积累。"""
    assert (
        evidence_level("hackernews", {"points": 5, "semantic_peer_count": 1})
        == LEVEL_ACCUMULATING
    )


def test_strong_metric_with_one_peer_is_sufficient() -> None:
    """自身够强 + 一处互证，两个信号都到位。"""
    assert (
        evidence_level("hackernews", {"points": 200, "semantic_peer_count": 1})
        == LEVEL_SUFFICIENT
    )


def test_two_peers_is_sufficient_even_when_weak() -> None:
    """多个独立来源互证是比单一指标更硬的信号，弱指标也能判充分。"""
    assert (
        evidence_level("v2ex", {"replies": 3, "semantic_peer_count": 2})
        == LEVEL_SUFFICIENT
    )


def test_exact_corroboration_counts_as_cross_source() -> None:
    """dedup_key 精确互证也算「有别的来源在说同一件事」。"""
    assert (
        evidence_level(
            "github-trending",
            {"stars_today": 50, "corroborating_sources": ["hackernews"]},
        )
        == LEVEL_ACCUMULATING
    )


def test_unknown_source_is_never_strong() -> None:
    """没定过阈值的源不强加判断，一律当作不强，宁可保守。"""
    assert evidence_level("unknown", {"points": 9999}) == LEVEL_EMERGING

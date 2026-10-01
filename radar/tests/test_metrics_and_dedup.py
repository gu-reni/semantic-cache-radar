import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from radar.app.models import RadarItem
from radar.app.pipeline import GatewayEnricher, RadarPipeline
from radar.app.repository import RadarRepository


def _item(
    source: str,
    external_id: str,
    title: str,
    url: str,
    metrics: dict | None = None,
    published_at: str | None = "2026-10-01T00:00:00+00:00",
) -> RadarItem:
    return RadarItem(
        source=source,
        external_id=external_id,
        title=title,
        url=url,
        published_at=published_at,
        metrics=metrics,
    )


def test_upsert_stores_metrics_and_records_a_snapshot(tmp_path: Path) -> None:
    """每次写入都要留一份快照。

    radar_items 上是 UNIQUE(source, external_id) 的覆盖式更新，只留得下
    「现在什么样」。而雷达的价值在于「在动」—— 涨得快不快、什么时候第一次
    出现 —— 这些全都要时间序列。快照表是只增不改的那一半。
    """
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(
        _item("github-trending", "a/b", "a b", "https://github.com/a/b", {"stars": 10})
    )
    repository.upsert(
        _item("github-trending", "a/b", "a b", "https://github.com/a/b", {"stars": 42})
    )

    assert repository.count() == 1, "同一个仓库不该变成两条"
    assert repository.snapshot_count() == 2, "两次写入应留下两份快照"
    assert repository.list_items()[0].metrics["stars"] == 42, "指标应被更新为最新值"


def test_snapshots_keep_metric_history(tmp_path: Path) -> None:
    """快照要留下历史值，而不是跟着主表一起被覆盖。"""
    path = tmp_path / "radar.db"
    repository = RadarRepository(str(path))
    repository.upsert(
        _item("github-trending", "a/b", "a b", "https://github.com/a/b", {"stars": 10})
    )
    repository.upsert(
        _item("github-trending", "a/b", "a b", "https://github.com/a/b", {"stars": 42})
    )

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT metrics_json FROM radar_snapshots ORDER BY id"
        ).fetchall()
    assert [json.loads(row[0])["stars"] for row in rows] == [10, 42]


def test_legacy_database_is_migrated_without_losing_rows(tmp_path: Path) -> None:
    """线上还躺着一个有真实数据的老库，升级不能靠重建。

    老库没有 metrics_json / dedup_key 这几列。加列必须只加不删，
    而且 dedup_key 要回填 —— 它是后来才有的概念，历史行留空的话，
    跨源归并查询会把它们全部当作「无归属」而漏掉。
    """
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE radar_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT,
                summary TEXT,
                tags_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, external_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO radar_items (source, external_id, title, url) VALUES (?, ?, ?, ?)",
            ("hackernews", "1", "老数据", "https://github.com/psf/requests"),
        )

    repository = RadarRepository(str(path))

    assert repository.count() == 1, "老数据不能丢"
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT dedup_key, metrics_json FROM radar_items"
        ).fetchone()
    assert row[0] == "github:psf/requests", "历史行的 dedup_key 必须回填"
    assert row[1] is None, "老数据本来就没有指标，不要编一个出来"


def test_corroboration_is_visible_from_both_sides(tmp_path: Path) -> None:
    """同一个项目被两个源同时提到时，两侧都要看到对方。

    「多源互证」是这个项目里最硬的信号之一，但它只在跨行看的时候才存在。
    """
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(_item("github-trending", "psf/requests", "psf requests", "https://github.com/psf/requests"))
    repository.upsert(
        _item("hackernews", "42", "Requests 4.0 released", "https://github.com/psf/requests/blob/main/NEWS.md")
    )

    by_source = {item.source: item for item in repository.list_items()}
    assert by_source["github-trending"].metrics["corroborating_sources"] == ["hackernews"]
    assert by_source["hackernews"].metrics["corroborating_sources"] == ["github-trending"]


def test_single_source_item_has_no_corroboration_field(tmp_path: Path) -> None:
    """只有一处提到时不要写出「互证来源：空」这种噪音字段。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(_item("v2ex", "9", "某话题", "https://www.v2ex.com/t/9", {"replies": 3}))

    item = repository.list_items()[0]
    assert "corroborating_sources" not in item.metrics
    assert item.metrics["replies"] == 3


def test_heat_sort_ranks_within_source_not_across_sources(tmp_path: Path) -> None:
    """热度排序按「源内名次」，不把不同量纲的数字直接比大小。

    GitHub 的 1,179 个今日新星和 HN 的 153 点不是一回事：直接比大小会让
    某一个源恒定霸占前排。
    """
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(
        _item("github-trending", "a/low", "low", "https://github.com/a/low", {"stars_today": 5})
    )
    repository.upsert(
        _item("github-trending", "a/high", "high", "https://github.com/a/high", {"stars_today": 900})
    )
    repository.upsert(_item("hackernews", "1", "hn low", "https://ex.com/1", {"points": 10}))
    repository.upsert(_item("hackernews", "2", "hn high", "https://ex.com/2", {"points": 500}))

    items = repository.list_items(limit=10, sort="heat")
    ranks = {(item.source, item.external_id): item.metrics["heat_rank_in_source"] for item in items}

    assert ranks[("github-trending", "a/high")] == 1
    assert ranks[("github-trending", "a/low")] == 2
    assert ranks[("hackernews", "2")] == 1
    assert ranks[("hackernews", "1")] == 2


def test_heat_sort_puts_each_source_at_the_top_of_its_own_ranking(tmp_path: Path) -> None:
    """两个源的第一名都应该排在各自源的第二名前面。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(
        _item("github-trending", "a/low", "low", "https://github.com/a/low", {"stars_today": 5})
    )
    repository.upsert(_item("hackernews", "1", "hn low", "https://ex.com/1", {"points": 10}))

    items = repository.list_items(limit=10, sort="heat")
    assert {item.source for item in items[:2]} == {"github-trending", "hackernews"}


def test_items_without_metrics_do_not_break_heat_sort(tmp_path: Path) -> None:
    """没有指标的条目照样要能排出来，不能把整次查询搞崩。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(_item("v2ex", "1", "无指标", "https://www.v2ex.com/t/1"))
    repository.upsert(_item("v2ex", "2", "有指标", "https://www.v2ex.com/t/2", {"replies": 7}))

    items = repository.list_items(limit=10, sort="heat")
    assert len(items) == 2
    assert items[0].external_id == "2"


def test_time_sort_remains_the_default(tmp_path: Path) -> None:
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(
        _item("v2ex", "old", "旧", "https://www.v2ex.com/t/old", published_at="2026-01-01T00:00:00+00:00")
    )
    repository.upsert(
        _item("v2ex", "new", "新", "https://www.v2ex.com/t/new", published_at="2026-09-01T00:00:00+00:00")
    )

    assert repository.list_items(limit=10)[0].external_id == "new"


@pytest.mark.asyncio
async def test_metrics_survive_enrichment(tmp_path: Path) -> None:
    """富化之后指标必须还在。

    原先 enrich() 是把字段一个个重新拼一遍的，加了新字段而忘了加进那段拼接，
    不会报错、只会让字段静默消失 —— 指标差一点就是这么丢的。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"summary":"摘要","tags":["标签"]}'}}
                ]
            },
        )

    repository = RadarRepository(str(tmp_path / "radar.db"))
    item = _item(
        "github-trending", "a/b", "a b", "https://github.com/a/b", {"stars": 1234, "language": "Rust"}
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await RadarPipeline(
            repository, GatewayEnricher(client, "http://gateway:8000")
        ).process([item])

    saved = repository.list_items()[0]
    assert saved.summary == "摘要"
    assert saved.metrics["stars"] == 1234
    assert saved.metrics["language"] == "Rust"


def test_metrics_absent_when_source_provides_nothing(tmp_path: Path) -> None:
    """源站没给指标就留空，不要写 0 —— 0 会把「没有信号」伪装成「信号为零」。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(_item("v2ex", "1", "无指标", "https://www.v2ex.com/t/1", None))
    assert repository.list_items()[0].metrics is None

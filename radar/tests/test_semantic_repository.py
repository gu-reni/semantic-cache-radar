"""语义候选表的仓库层测试：写入配对、按条目聚合疑似同源候选数。"""

import sqlite3
from pathlib import Path

from radar.app.models import RadarItem
from radar.app.repository import RadarRepository


def _item(source: str, external_id: str, title: str) -> RadarItem:
    return RadarItem(source, external_id, title, f"https://example.com/{external_id}")


def test_recent_titles_excludes_self_and_returns_others(tmp_path: Path) -> None:
    repository = RadarRepository(str(tmp_path / "radar.db"))
    first_id = repository.upsert(_item("hackernews", "1", "A"))
    second_id = repository.upsert(_item("v2ex", "2", "B"))

    recent = repository.recent_titles(exclude_id=first_id, limit=10, days=7)

    assert {item_id for item_id, _ in recent} == {second_id}, "不能包含自己"


def test_record_semantic_links_are_visible_from_both_sides(tmp_path: Path) -> None:
    """疑似同源候选数要双向可查：新条目看到旧条目，旧条目也看到新条目。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    old_id = repository.upsert(_item("hackernews", "1", "A"))
    new_id = repository.upsert(_item("github-trending", "a/b", "B"))
    repository.record_semantic_links([(new_id, old_id, 0.93)])

    by_external = {item.external_id: item for item in repository.list_items()}
    assert by_external["1"].metrics["semantic_peer_count"] == 1
    assert by_external["a/b"].metrics["semantic_peer_count"] == 1


def test_semantic_peer_count_aggregates_both_directions(tmp_path: Path) -> None:
    """中间那条既被旧条目指向、又指向更新条目时，候选数要两边都算上。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    a_id = repository.upsert(_item("hackernews", "1", "A"))
    b_id = repository.upsert(_item("v2ex", "2", "B"))
    c_id = repository.upsert(_item("github-trending", "x/y", "C"))
    repository.record_semantic_links([(b_id, a_id, 0.9), (c_id, b_id, 0.95)])

    by_external = {item.external_id: item for item in repository.list_items()}
    assert by_external["2"].metrics["semantic_peer_count"] == 2
    assert by_external["1"].metrics["semantic_peer_count"] == 1
    assert by_external["x/y"].metrics["semantic_peer_count"] == 1


def test_record_semantic_links_deduplicates_same_pair(tmp_path: Path) -> None:
    """同一对反复记录只保留一行（覆盖更新相似度），别把表刷爆。"""
    path = tmp_path / "radar.db"
    repository = RadarRepository(str(path))
    a_id = repository.upsert(_item("hackernews", "1", "A"))
    b_id = repository.upsert(_item("v2ex", "2", "B"))

    repository.record_semantic_links([(b_id, a_id, 0.9)])
    repository.record_semantic_links([(b_id, a_id, 0.97)])

    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT similarity FROM semantic_links").fetchall()
    assert len(rows) == 1
    assert round(rows[0][0], 6) == 0.97

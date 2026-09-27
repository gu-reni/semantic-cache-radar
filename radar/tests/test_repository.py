from pathlib import Path

from radar.app.models import RadarItem
from radar.app.repository import RadarRepository


def test_repository_upserts_and_deduplicates_items(tmp_path: Path) -> None:
    repository = RadarRepository(str(tmp_path / "radar.db"))
    item = RadarItem(
        source="hackernews",
        external_id="123",
        title="First title",
        url="https://example.com/1",
        summary="A summary",
        tags=["AI", "Backend"],
    )

    first_id = repository.upsert(item)
    second_id = repository.upsert(
        RadarItem(
            source="hackernews",
            external_id="123",
            title="Updated title",
            url="https://example.com/1",
            summary="Updated summary",
            tags=["AI"],
        )
    )

    items = repository.list_items()
    assert first_id == second_id
    assert repository.count() == 1
    assert items[0].title == "Updated title"
    assert items[0].tags == ["AI"]


def test_repository_filters_by_source_and_keyword(tmp_path: Path) -> None:
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(RadarItem("v2ex", "1", "Python news", "https://example.com/1"))
    repository.upsert(RadarItem("github", "2", "Rust news", "https://example.com/2"))

    assert len(repository.list_items(source="v2ex")) == 1
    assert len(repository.list_items(keyword="Python")) == 1
    assert repository.list_items(source="github")[0].title == "Rust news"

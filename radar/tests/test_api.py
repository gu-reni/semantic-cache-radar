from pathlib import Path

from fastapi.testclient import TestClient

from radar.app.main import app, get_repository
from radar.app.models import RadarItem
from radar.app.repository import RadarRepository


def test_radar_items_api_returns_saved_items(tmp_path: Path) -> None:
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(RadarItem("github", "1", "AI project", "https://example.com"))
    app.dependency_overrides[get_repository] = lambda: repository

    try:
        response = TestClient(app).get("/radar/items?source=github")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()[0]["title"] == "AI project"


def test_radar_items_response_includes_evidence_level(tmp_path: Path) -> None:
    """每条响应都要带证据等级；它是读取时实时算出来的，不是存下来的。"""
    repository = RadarRepository(str(tmp_path / "radar.db"))
    # 强指标（HN points >= 100）→ 信号在积累。
    repository.upsert(
        RadarItem(
            "hackernews",
            "1",
            "A hot story",
            "https://example.com/1",
            metrics={"points": 500, "comments": 40},
        )
    )
    app.dependency_overrides[get_repository] = lambda: repository

    try:
        response = TestClient(app).get("/radar/items")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    first = response.json()[0]
    assert first["evidence_level"] == "信号在积累"
    assert first["metrics"]["points"] == 500


def test_radar_items_response_marks_red_flag_when_metrics_are_missing(tmp_path: Path) -> None:
    repository = RadarRepository(str(tmp_path / "radar.db"))
    repository.upsert(RadarItem("v2ex", "9", "无指标条目", "https://example.com/9"))
    app.dependency_overrides[get_repository] = lambda: repository

    try:
        response = TestClient(app).get("/radar/items")
    finally:
        app.dependency_overrides.clear()

    assert response.json()[0]["evidence_level"] == "有红旗"

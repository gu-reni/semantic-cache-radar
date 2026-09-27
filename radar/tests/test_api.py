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

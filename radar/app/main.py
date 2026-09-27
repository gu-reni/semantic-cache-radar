import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel

from radar.app.models import RadarItem
from radar.app.repository import RadarRepository

app = FastAPI(title="Technical Radar API", version="0.1.0")


class RadarItemResponse(BaseModel):
    source: str
    external_id: str
    title: str
    url: str
    published_at: str | None
    summary: str | None
    tags: list[str]
    created_at: str | None


def _database_path() -> str:
    return os.getenv("RADAR_DATABASE_PATH", "./data/radar.db")


@lru_cache
def get_repository() -> RadarRepository:
    return RadarRepository(_database_path())


def _to_response(item: RadarItem) -> RadarItemResponse:
    return RadarItemResponse(
        source=item.source,
        external_id=item.external_id,
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        summary=item.summary,
        tags=item.tags or [],
        created_at=item.created_at,
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/radar/items", response_model=list[RadarItemResponse])
async def list_radar_items(
    repository: Annotated[RadarRepository, Depends(get_repository)],
    limit: int = Query(default=20, ge=1, le=100),
    source: str | None = Query(default=None, min_length=1),
    keyword: str | None = Query(default=None, min_length=1),
) -> list[RadarItemResponse]:
    items = repository.list_items(limit=limit, source=source, keyword=keyword)
    return [_to_response(item) for item in items]

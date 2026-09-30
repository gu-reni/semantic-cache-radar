import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from radar.app.models import RadarItem
from radar.app.repository import RadarRepository
from radar.app.scheduler import start_scheduler

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """随接口进程一起启停定时采集任务。"""
    scheduler = start_scheduler()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(title="Technical Radar API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    """给页面与静态资源发 Cache-Control。

    Starlette 默认只发 ETag/Last-Modified，浏览器会启用启发式缓存自行决定新鲜度，
    于是改了前端而访客仍看到旧页面，服务端却怎么看都正常。
    只给 text/html 与 /static/ 加；JSON 接口不加，免得监控读到过期数据。
    """
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if request.url.path.startswith("/static/") or content_type.startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """对外展示的页面。真正的数据仍走 /radar/items。"""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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

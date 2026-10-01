import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from radar.app.evidence import evidence_level
from radar.app.models import RadarItem
from radar.app.repository import RadarRepository
from radar.app.scheduler import start_scheduler

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def configure_logging() -> None:
    """让应用自己的日志在 uvicorn 下真的能被看见。

    uvicorn 只配置它自己的 logger（`uvicorn.*` 自带 handler 且不向上传播），
    根 logger 仍停在 WARNING 且没有 handler。后果是应用里 `logger.info(...)`
    写的东西全部被静默丢弃 —— 定时采集每 6 小时才跑一次，
    日志一旦没了，「它到底跑没跑、采了多少、哪条源挂了」就无从查证。
    """
    app_logger = logging.getLogger("radar")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """随接口进程一起启停定时采集任务。"""
    # 必须在 uvicorn 配好日志之后再做，否则会被它覆盖掉。
    configure_logging()
    scheduler = start_scheduler()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


# /docs 与 /openapi.json 关掉：这个服务有一份绑在公网上对外展示的实例，
# 没有鉴权，不该顺带把接口文档一起露出去。页面和 /radar/items 都不依赖它们。
app = FastAPI(
    title="Technical Radar API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


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
    metrics: dict[str, Any] = {}
    """源站原样给的量化信号（点数/评论数/星数…）。

    除了 `heat_rank_in_source`（本库按源内动量算的名次）与
    `corroborating_sources`（还有哪些源提到同一个东西）与
    `semantic_peer_count`（疑似同源候选数，任务 1 记下的语义配对）之外，
    其余都是抓来就有的值，不做换算 —— 口径要改时不必重采历史数据。
    """
    evidence_level: str
    """证据等级：由 metrics + 疑似同源候选数实时算出，不落库。

    四档取值见 radar.app.evidence（证据充分 / 信号在积累 / 刚出现 / 有红旗）。
    放在这里而不是 metrics 里，是因为它是「结论」不是「信号」，
    而且每次请求都重新推导，规则一改即对全部条目重算。
    """


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
        metrics=item.metrics or {},
        evidence_level=evidence_level(item.source, item.metrics),
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
    sort: str = Query(default="time", pattern="^(time|heat)$"),
) -> list[RadarItemResponse]:
    items = repository.list_items(limit=limit, source=source, keyword=keyword, sort=sort)
    return [_to_response(item) for item in items]

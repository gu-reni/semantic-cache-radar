"""技术雷达的定时执行。

放在 API 进程内用 AsyncIOScheduler，而不是另起一个容器：
ECS 内存只剩 1.5G，多一个容器就多一份常驻开销，
而采集任务是轻量的 IO 密集型工作，不会拖累查询接口。

采集失败绝不能把查询接口一起带崩，所以任务体整体兜住异常。
"""
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from radar.app.runner import run_configured_once

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_MINUTES = 360
DEFAULT_JOB_ID = "radar-collect"


def interval_minutes() -> int:
    """读取采集间隔，配置非法时退回默认值而不是启动失败。"""
    raw = os.getenv("RADAR_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES))
    try:
        value = int(raw)
    except ValueError:
        logger.warning("RADAR_INTERVAL_MINUTES=%r 不是整数，改用默认 %s 分钟", raw, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    if value < 1:
        logger.warning("RADAR_INTERVAL_MINUTES=%s 小于 1，改用默认 %s 分钟", value, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return value


def scheduler_enabled() -> bool:
    """允许用环境变量关掉定时任务（本地调试或测试时用）。"""
    return os.getenv("RADAR_SCHEDULER_ENABLED", "true").strip().lower() not in {"0", "false", "no"}


async def run_collection_job() -> None:
    """执行一次采集；无论成败都不向外抛异常。"""
    try:
        result = await run_configured_once()
    except Exception:
        # 定时任务必须兜住所有异常，否则一次网络抖动就会让整个进程退出。
        logger.exception("雷达采集任务异常终止")
        return

    logger.info(
        "雷达采集完成 fetched=%s processed=%s stored=%s failed=%s "
        "source_failures=%s failed_sources=%s",
        result.fetched,
        result.processed,
        result.stored,
        result.failed,
        result.source_failures,
        ",".join(result.failed_sources) or "-",
    )


def start_scheduler() -> AsyncIOScheduler | None:
    """启动定时采集；未启用或启动失败时返回 None，不影响接口可用性。"""
    if not scheduler_enabled():
        logger.info("RADAR_SCHEDULER_ENABLED 为假，不启动定时采集")
        return None

    minutes = interval_minutes()
    try:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            run_collection_job,
            "interval",
            minutes=minutes,
            id=DEFAULT_JOB_ID,
            # 上一轮还没跑完就跳过本轮，避免任务堆积。
            max_instances=1,
            # 进程重启后错过多次触发时只补跑一次。
            coalesce=True,
        )
        scheduler.start()
    except Exception:
        logger.exception("定时采集启动失败，查询接口仍可正常使用")
        return None

    logger.info("雷达定时采集已启用：每 %s 分钟执行一次", minutes)
    return scheduler

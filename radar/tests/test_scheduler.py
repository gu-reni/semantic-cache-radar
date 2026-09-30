"""定时采集的配置解析与异常兜底测试。

这里要钉住两点：
  1. 间隔配置写错时退回默认值，而不是让进程启动失败。
  2. 采集任务抛异常时不能向外扩散，否则一次网络抖动会带崩查询接口。
"""
import pytest

from radar.app import scheduler
from radar.app.runner import RadarRunResult

DEFAULT = scheduler.DEFAULT_INTERVAL_MINUTES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("60", 60),
        ("1", 1),
        ("1440", 1440),
        (" 120 ", 120),
        ("abc", DEFAULT),
        ("", DEFAULT),
        ("0", DEFAULT),
        ("-5", DEFAULT),
        ("3.5", DEFAULT),
    ],
)
def test_interval_minutes_falls_back_safely(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    monkeypatch.setenv("RADAR_INTERVAL_MINUTES", raw)
    assert scheduler.interval_minutes() == expected


def test_interval_minutes_uses_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RADAR_INTERVAL_MINUTES", raising=False)
    assert scheduler.interval_minutes() == DEFAULT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("false ", False),
    ],
)
def test_scheduler_enabled_parses_common_forms(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("RADAR_SCHEDULER_ENABLED", raw)
    assert scheduler.scheduler_enabled() is expected


@pytest.mark.asyncio
async def test_collection_job_logs_and_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> RadarRunResult:
        raise RuntimeError("upstream unreachable")

    monkeypatch.setattr(scheduler, "run_configured_once", boom)

    # 关键断言：不抛异常。定时任务里一个未捕获异常会终止整个进程。
    await scheduler.run_collection_job()


@pytest.mark.asyncio
async def test_collection_job_passes_through_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok() -> RadarRunResult:
        return RadarRunResult(fetched=7, processed=7, stored=6, failed=1, source_failures=0)

    monkeypatch.setattr(scheduler, "run_configured_once", ok)

    await scheduler.run_collection_job()


def test_start_scheduler_returns_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_SCHEDULER_ENABLED", "false")

    assert scheduler.start_scheduler() is None

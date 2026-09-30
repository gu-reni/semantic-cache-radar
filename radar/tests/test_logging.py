"""应用日志可见性测试。

uvicorn 只配置它自己的 logger（`uvicorn.*` 自带 handler 且设了不向上传播），
根 logger 仍停在 WARNING 且没有 handler。如果应用不自己配一遍，
`logger.info(...)` 写的东西会被静默丢弃 —— 而这个项目最需要日志的地方是
每 6 小时才跑一次的定时采集：日志一旦没有，「跑没跑、采了多少、哪条源挂了」
就完全无从查证，页面安静地停在旧数据上也不会有任何提示。
"""
import logging

from fastapi.testclient import TestClient

from radar.app.main import app


def test_app_logger_can_emit_info_after_startup() -> None:
    """启动之后，应用自己的 logger 必须真的能写出 INFO。"""
    app_logger = logging.getLogger("radar")
    scheduler_logger = logging.getLogger("radar.app.scheduler")

    saved_app = (app_logger.level, list(app_logger.handlers))
    try:
        # 模拟「uvicorn 已经配过日志、但没管应用 logger」的初始状态
        app_logger.handlers.clear()
        app_logger.setLevel(logging.WARNING)

        with TestClient(app):
            pass  # 进入即触发 lifespan

        assert app_logger.handlers, "必须真的挂上 handler，否则照样写不出去"
        assert scheduler_logger.isEnabledFor(logging.INFO), (
            "定时采集的日志必须可见，否则每 6 小时一次的采集会变成黑盒"
        )
    finally:
        app_logger.handlers[:] = saved_app[1]
        app_logger.setLevel(saved_app[0])

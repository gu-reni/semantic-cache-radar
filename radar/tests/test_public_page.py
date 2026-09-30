"""对外展示页面的路由与缓存头测试。

两件容易出问题的事：
  1. 缓存头只该给页面和静态资源，不该给 JSON 接口 —— 写反了监控会读到过期数据。
  2. 静态资源必须发对 Content-Type，否则浏览器会拒绝应用 CSS/JS。
"""
import pytest
from fastapi.testclient import TestClient

from radar.app.main import app


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试里不要真的起定时采集任务。"""
    monkeypatch.setenv("RADAR_SCHEDULER_ENABLED", "false")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_index_page_is_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "技术雷达" in response.text


def test_index_page_has_no_external_resource_references(client: TestClient) -> None:
    """零外部依赖：页面里不能出现指向外站的 link/script/img。

    服务器在国内，跨境 CDN 会慢甚至加载失败；而且页面必须断网也能渲染。
    """
    html = client.get("/").text

    for marker in ('src="http', "src='http", 'href="http', "href='http"):
        assert marker not in html, f"页面引用了外部资源: {marker}"


@pytest.mark.parametrize(
    ("path", "expected_type"),
    [
        ("/static/style.css", "text/css"),
        ("/static/app.js", "javascript"),
    ],
)
def test_static_assets_have_correct_content_type(
    client: TestClient, path: str, expected_type: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert expected_type in response.headers["content-type"], (
        f"{path} 的 Content-Type 是 {response.headers['content-type']}，浏览器会拒绝应用"
    )


@pytest.mark.parametrize("path", ["/", "/static/style.css", "/static/app.js"])
def test_page_and_assets_send_cache_control(client: TestClient, path: str) -> None:
    """页面与静态资源必须显式发 Cache-Control。

    只发 ETag/Last-Modified 时浏览器会启用启发式缓存，
    结果是改了前端而访客仍看到旧页面，服务端却怎么看都正常。
    """
    response = client.get(path)

    assert response.headers.get("cache-control") == "no-cache, must-revalidate"


def test_json_endpoint_does_not_send_cache_control(client: TestClient) -> None:
    """JSON 接口故意不加缓存头，免得监控与前端读到过期数据。"""
    response = client.get("/radar/items")

    assert response.status_code == 200
    assert "cache-control" not in {key.lower() for key in response.headers}

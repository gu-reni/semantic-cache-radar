"""对外展示页面的路由与缓存头测试。

三件容易出问题的事：
  1. 缓存头只该给页面和静态资源，不该给 JSON 接口 —— 写反了监控会读到过期数据。
  2. 静态资源必须发对 Content-Type，否则浏览器会拒绝应用 CSS/JS。
  3. 这份服务有一份无鉴权、绑在公网上的实例，接口文档不能跟着一起露出去。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from radar.app.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_api_docs_are_disabled(client: TestClient, path: str) -> None:
    """接口文档不能在公网实例上暴露。

    这个服务有一份无鉴权、绑在公网展示的实例，FastAPI 默认打开的 /docs
    会把全部接口与数据结构一并露出去。页面与 /radar/items 都不依赖它们。
    """
    assert client.get(path).status_code == 404


def test_compose_keeps_gateway_out_of_the_collector_proxy() -> None:
    """采集器走代理时，NO_PROXY 必须包含 gateway。

    radar 调本机网关走的是服务名 http://gateway:8000。这条一旦也被代理接管，
    代理不认识这个名字，语义缓存整条链路会当场全断 —— 而且现象只是"采集全部失败"，
    不会提示跟代理有关。
    """
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "NO_PROXY: gateway,127.0.0.1,localhost" in compose


def test_app_js_never_uses_inner_html() -> None:
    """第三方标题/摘要/标签必须走 textContent，绝不能 innerHTML 拼字符串。

    一旦放开 innerHTML，一个带 <script> 的标题就能在本站执行脚本。这是防注入的
    硬约束，不能只靠注释，要有测试守着。注释里可以出现「innerHTML」这个词，
    所以这里只盯真正的属性访问（`.innerHTML`）与赋值（`innerHTML=`）。
    """
    script = (PROJECT_ROOT / "radar" / "app" / "static" / "app.js").read_text(encoding="utf-8")

    assert ".innerHTML" not in script, "app.js 里对元素做了 innerHTML 属性访问，第三方文本不可信"
    assert "innerHTML=" not in script, "app.js 里对 innerHTML 赋值，第三方文本不可信"


def test_index_page_has_evidence_level_filters() -> None:
    """页面要有按证据等级筛选的入口，等级四档一个不少。"""
    html = (PROJECT_ROOT / "radar" / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )

    for level in ("证据充分", "信号在积累", "刚出现", "有红旗"):
        assert level in html, f"页面缺少证据等级筛选档：{level}"

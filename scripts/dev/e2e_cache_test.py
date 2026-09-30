"""语义缓存网关的端到端联调。

验证的不是「接口能返回 200」，而是缓存的核心承诺：
  1. 没鉴权打不进来（网关持有上游 Key，不能变成公开代理）。
  2. 首问打到上游，重复问与改写问命中缓存、不再打上游。
  3. 不相干的问题不会被误命中（宁可漏，不可错）。
  4. /cache/stats 报出的 Token 节省数，能对上上游真实调用次数。

注意响应头的大小写：
  Starlette 发出去的响应头名一律是小写（HTTP 头本就大小写不敏感），
  所以这里统一转小写后再查，否则 headers.get("X-Cache") 永远拿到 None。

前置：
  bash scripts/prepare_runtime_dirs.sh
  docker compose -f docker-compose.yml -f docker-compose.e2e.yml up -d
  # 等网关与雷达都 healthy

用法：python3 scripts/dev/e2e_cache_test.py [网关地址]
  默认 http://127.0.0.1:18000
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18000"
TOKEN = "e2e-token"
# 假上游的调用计数接口；它只把端口发布到宿主机回环。
UPSTREAM_STATUS = "http://127.0.0.1:9099"

# 与 scripts/dev/fake_upstream.py 中的常量保持一致。
PROMPT_TOKENS = 42
COMPLETION_TOKENS = 18
PER_CALL_TOKENS = PROMPT_TOKENS + COMPLETION_TOKENS

passed = 0
failed = 0


def _lower_headers(headers) -> dict[str, str]:
    """转成小写键的字典，避免大小写不一致导致取不到值。"""
    return {key.lower(): value for key, value in headers.items()}


def call(method: str, path: str, body: dict | None = None, token: str | None = TOKEN):
    """返回 (状态码, 小写键的响应头, 响应体)。"""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Gateway-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw, status = response.read().decode(), response.status
            headers = _lower_headers(response.headers)
    except urllib.error.HTTPError as exc:
        raw, status = exc.read().decode(), exc.code
        headers = _lower_headers(exc.headers)
    try:
        return status, headers, json.loads(raw)
    except json.JSONDecodeError:
        return status, headers, {"_raw": raw[:200]}


def ask(question: str, token: str | None = TOKEN):
    return call(
        "POST",
        "/v1/chat/completions",
        {"messages": [{"role": "user", "content": question}]},
        token=token,
    )


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
    print(f"  {'✓' if condition else '✗'} {label}" + (f"   {detail}" if detail else ""))


print("═" * 76)
print(f"目标网关：{BASE}")
print("═" * 76)

print("\n一、探活与鉴权")
status, _, _ = call("GET", "/health", token=None)
check("GET /health 免鉴权可访问（编排层健康检查需要）", status == 200, f"HTTP {status}")

status, _, _ = ask("鉴权测试", token=None)
check("不带令牌的对话请求被拒", status == 401, f"HTTP {status}")

status, _, _ = ask("鉴权测试", token="wrong-token")
check("令牌错误被拒", status == 401, f"HTTP {status}")

status, _, _ = ask("鉴权测试", token=TOKEN)
check("令牌正确被放行", status == 200, f"HTTP {status}")

print("\n二、缓存命中行为")
question = "什么是语义缓存"

status, headers, body = ask(question)
first_answer = (body.get("choices") or [{}])[0].get("message", {}).get("content")
check("首问未命中（回源生成）", headers.get("x-cache") == "MISS", f"x-cache={headers.get('x-cache')}")

status, headers, body = ask(question)
second_answer = (body.get("choices") or [{}])[0].get("message", {}).get("content")
check("原样重复命中缓存", headers.get("x-cache") == "HIT", f"x-cache={headers.get('x-cache')}")
check(
    "命中时返回与首问相同的答案",
    first_answer is not None and first_answer == second_answer,
    f"相似度={headers.get('x-cache-similarity')}",
)

status, headers, _ = ask("语义缓存是什么意思")
check(
    "改写问法命中缓存",
    headers.get("x-cache") == "HIT",
    f"相似度={headers.get('x-cache-similarity')}",
)

status, headers, _ = ask("语义缓存是啥")
check(
    "口语化问法命中缓存",
    headers.get("x-cache") == "HIT",
    f"相似度={headers.get('x-cache-similarity')}",
)

status, headers, _ = ask("今天北京天气怎么样")
check(
    "无关问题不误命中",
    headers.get("x-cache") == "MISS",
    f"相似度={headers.get('x-cache-similarity')}",
)

status, headers, _ = ask("向量数据库的索引原理是什么")
check(
    "相关但不同的问题不误命中",
    headers.get("x-cache") == "MISS",
    f"相似度={headers.get('x-cache-similarity')}",
)

print("\n三、统计与 Token 对账")
status, _, stats = call("GET", "/cache/stats", token=None)
print(f"     {json.dumps(stats, ensure_ascii=False)}")

check("统计接口免鉴权可读（监控需要）", status == 200, f"HTTP {status}")
check("请求数统计正确", stats.get("requests") == 7, f"requests={stats.get('requests')} 期望 7")
check("命中数统计正确", stats.get("hits") == 3, f"hits={stats.get('hits')} 期望 3")
check("未命中数统计正确", stats.get("misses") == 4, f"misses={stats.get('misses')} 期望 4")
check(
    "命中率计算正确",
    abs((stats.get("hit_rate") or 0) - 3 / 7) < 1e-6,
    f"hit_rate={stats.get('hit_rate')}",
)
check("缓存条目数正确", stats.get("stored_entries") == 4, f"entries={stats.get('stored_entries')} 期望 4")

try:
    with urllib.request.urlopen(UPSTREAM_STATUS, timeout=10) as response:
        upstream = json.loads(response.read().decode())
except Exception as exc:  # noqa: BLE001
    upstream = {}
    print(f"     ⚠️ 取不到上游计数：{exc}")

if upstream:
    calls = upstream.get("upstream_calls")
    check(
        "上游真实调用次数 == 未命中次数",
        calls == stats.get("misses"),
        f"上游 {calls} 次 / 未命中 {stats.get('misses')} 次",
    )
    check(
        "节省 Token 数与命中次数自洽",
        stats.get("saved_tokens") == 3 * PER_CALL_TOKENS,
        f"saved={stats.get('saved_tokens')} 期望 {3 * PER_CALL_TOKENS}",
    )
    check(
        "实际消耗 Token 与上游调用自洽",
        stats.get("total_tokens") == calls * PER_CALL_TOKENS,
        f"消耗={stats.get('total_tokens')} 期望 {calls * PER_CALL_TOKENS}",
    )
    saved, spent = stats.get("saved_tokens", 0), stats.get("total_tokens", 0)
    if spent:
        print(
            f"\n  省下的 Token = {saved}，真实消耗 = {spent}，"
            f"相当于少花 {saved / (saved + spent) * 100:.1f}%"
        )

print("\n四、清空缓存接口的鉴权")
status, _, _ = call("DELETE", "/cache", token=None)
check("不带令牌清空缓存被拒", status == 401, f"HTTP {status}")

print()
print("═" * 76)
print(f"结果：{passed} 项通过，{failed} 项失败")
print("═" * 76)
sys.exit(1 if failed else 0)

"""假的上游大模型接口，仅用于端到端联调。

为什么需要它：
  网关的完整链路是「客户端 → 网关 → 上游模型」。要验证语义缓存到底省没省 Token，
  就必须知道上游被真实调用了多少次，所以这里用一个假上游冒充 OpenAI 兼容接口，
  并记录调用次数与累计 Token。

为什么放进 compose 网络而不是跑在宿主机上：
  跑在宿主机上需要容器通过 host.docker.internal 回连宿主机端口，
  而生产主机的防火墙（如 ufw）默认只放行显式开过的端口，
  容器回连未放行的端口会被静默丢包，表现为超时而不是拒绝，很难排查。
  放进同一个 compose 网络后走 Docker 内建 DNS，不经过宿主机防火墙。

用法（由 docker-compose.e2e.yml 调用）：python fake_upstream.py [端口]
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CALL_COUNT = 0
TOKEN_COUNT = 0
LOCK = threading.Lock()

# 每次回答都不同：这样一旦缓存返回了旧答案，就能一眼看出来。
PROMPT_TOKENS = 42
COMPLETION_TOKENS = 18


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        global CALL_COUNT, TOKEN_COUNT
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

        with LOCK:
            CALL_COUNT += 1
            n = CALL_COUNT
            TOKEN_COUNT += PROMPT_TOKENS + COMPLETION_TOKENS

        # 返回雷达能解析的 JSON 结构，这样整条「采集 → 网关摘要 → 落库」链路
        # 才算真正验证过。内容里带序号，一旦缓存返回旧答案就能立刻看出来。
        enriched = json.dumps(
            {"summary": f"这是第 {n} 次真实生成的摘要", "tags": ["假上游", f"第{n}次"]},
            ensure_ascii=False,
        )

        body = json.dumps(
            {
                "id": f"fake-{n}",
                "object": "chat.completion",
                "model": payload.get("model") or "fake-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": enriched,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": PROMPT_TOKENS,
                    "completion_tokens": COMPLETION_TOKENS,
                    "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS,
                },
            }
        ).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """上报累计调用次数，供联调脚本对账。"""
        with LOCK:
            body = json.dumps(
                {"upstream_calls": CALL_COUNT, "upstream_tokens": TOKEN_COUNT}
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """静音默认的逐请求日志，只保留调用计数。"""
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"假上游已启动：0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

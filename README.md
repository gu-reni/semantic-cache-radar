# Semantic Cache Radar

面向大模型应用的语义缓存网关与 AI 技术雷达系统。

## 项目目标

- 通过本地 Embedding 和向量检索复用相似问题的历史回答，减少大模型 Token 消耗。
- 提供兼容 OpenAI Chat Completions 格式的 FastAPI 网关，可直接替换现有客户端的接口地址。
- 定时聚合技术社区热点，调用网关生成摘要和技术标签。
- 一个 Docker Compose 文件同时起两个服务，可在普通 CPU 云主机上运行。

## 架构与数据流

```text
客户端 ──POST /v1/chat/completions──▶ 网关
                                      │
                                      ├─ 1. 用本地 ONNX 模型编码问题
                                      ├─ 2. 在 ChromaDB 里做余弦检索
                                      │
                          ┌───────────┴────────────┐
                    相似度够高                  相似度不够
                          │                        │
                    直接返回缓存答案          转调上游大模型
                    （省一次 Token）               │
                          │                 存回向量库
                          └────────────┬───────────┘
                                       ▼
                                    返回答案

技术雷达 ──定时任务──▶ 采集 HN / V2EX / GitHub Trending
                       └─▶ 经网关生成摘要与标签 ──▶ SQLite ──▶ GET /radar/items
```

## 模块

- `gateway/`：LLM 语义缓存网关。
- `radar/`：AI 技术雷达，含采集器、管道、SQLite 存储与定时任务。
- `scripts/download_embedding_model.sh`：下载 Embedding 模型。
- `data/`：SQLite 与 ChromaDB 的运行数据，不提交到 Git。
- `models/`：本地 Embedding 模型，不提交到 Git。

## 关键设计选择

### Embedding 用 ONNX Runtime，不用 sentence-transformers

sentence-transformers 会连带装上 PyTorch，镜像 1GB 起步；纯 CPU 机器上这些 CUDA 依赖完全用不到。
改用 ONNX Runtime 后镜像与内存都小一个量级，编码延迟也在毫秒级。

**代价**：导出的 ONNX 只提供 `last_hidden_state`（逐 token 的隐状态），不是句向量。
需要按 e5 官方做法自行做 **mean pooling + L2 归一化**，否则检索结果是错的。
这段逻辑在 `gateway/app/embeddings.py` 的 `_mean_pool()`。

### 模型选 int8 量化版

`Xenova/multilingual-e5-small` 的 `model_quantized.onnx` 只需 AVX2 指令集；
官方 `intfloat` 仓库的量化版要求 CPU 支持 AVX512-VNNI，普通云主机没有。
int8 版 113 MB，fp32 版 448 MB。

### 相似度阈值保持 0.92

用真实数据测过跨侧（查询侧向量 vs 被动侧存量向量）的相似度分布：

| 场景 | 相似度 | 期望 |
| --- | --- | --- |
| 完全相同 | 0.9339 | 命中 |
| 口语化「语义缓存是啥」 | 0.9415 | 命中 |
| 同意改写 | 0.9350 | 命中 |
| 相关但不同 | 0.8397 | 不命中 |
| 完全无关 | 0.8008 | 不命中 |

命中组最低 0.9325，未命中组最高 0.8397，分离区间宽 0.0928，0.92 落在其中。

取这个偏保守的值是因为两种错误的代价不对称：
**误命中**会让用户拿到别的问题的答案（可见的错误），
**漏命中**只是多调一次上游（用户无感）。

若更看重命中率，可下调到 0.88（仍在分离区间内），代价是误命中风险上升。

### 批量任务必须用 exact 模式，否则会整批共用同一个答案

语义缓存按向量相似度复用答案，前提是「问题不同则语义不同」。这个前提在下面这种情况会失效：

雷达的提示词里模板占了绝大部分篇幅，只有标题在变——

```text
请分析下面的技术资讯标题，只返回 JSON，不要 Markdown。格式必须是 {...}。
标题：Pi.dev: You Said No MCP        ← 只有这一行不同
```

整批提示词的余弦相似度都会越过 0.92，于是 **20 条不同的资讯共用第一条的摘要**。
实测过这个故障：命中率 19/20，抽查三条摘要完全相同。

这不是调高阈值能解决的，是语义缓存用错了场景。调用方可以声明复用策略：

```text
X-Cache-Mode: semantic   默认。按向量相似度复用，适合自然提问。
X-Cache-Mode: exact      仅当文本完全相同时复用。模板占比高的批量任务必须用它。
```

雷达走 exact，实测结果：

| | 命中 | 不同摘要数 |
| --- | --- | --- |
| semantic（错误用法） | 19/20 | 1 |
| exact（正确用法） | 0/20 | 20 |

同一个标题后续再次采集时仍会命中（文本相同），所以并不损失去重能力。

### 服务只绑回环，对外经反向代理并带令牌

网关持有上游大模型的 API Key。若端口绑 `0.0.0.0` 又放通安全组，
任何人都能拿它当免费模型接口刷 Token——而 `/v1/chat/completions` 是标准 OpenAI 格式，
扫到端口就能直接打，连文档都不用看。

因此：两个服务的端口都只绑 `127.0.0.1`，外部访问一律经 nginx 反代，
并携带 `GATEWAY_AUTH_TOKEN`。校验用恒定时间比较，避免通过响应耗时逐字节猜令牌。

`/health` 与 `/cache/stats` 不校验身份，否则编排层的健康检查和监控会失败。

## 雷达：从「列表」走向「证据」

采集本身不构成雷达。ThoughtWorks 的技术雷达之所以是雷达，在于那四个环
（Adopt / Trial / Assess / Caution）表达的是**判断**，而判断来自经验 ——
官方对 Trial 环的门槛写得很直白：「只有当我们真的在生产软件里用过它，
才能放进这个环」。

这条机器走不通。所以这里不去模仿那套环，只做**可复现的证据**：

| | ThoughtWorks 的环 | 本项目 |
|---|---|---|
| 表达 | 我们的意见 | 证据的强度 |
| 依据 | 人做过项目 | 可复现的公开数据 |
| 谁说了算 | 评审会 | 公开规则，谁都能复算 |

每个阶段都只加可验证的东西：

1. **指标**（已完成）—— 采集时保留源站本来就在返回的量化信号：
   HN 的点数与评论数、V2EX 的回复数与节点、GitHub 的星数与今日新增星数。
   此前这些数字在采集环节被丢弃，导致「这个项目最近是不是在涨」
   在数据层就无法回答。
2. **跨源归并**（已完成）—— 同一个项目若同时出现在多个源，视为一处证据。
   归并只影响读取时的分组，原始行一律保留：去重去出信息缺失比不去重更糟。
3. **历史**（记录已开始）—— 每次采集追加一份快照。动量需要时间序列，
   所以这一项的产出必然滞后于代码：表建好了，但要等积累。
4. **证据等级**（未开始）—— 按公开规则分档，并给出触发该档位的具体数字。
   输出的是「凭什么」，而不是「你该用什么」。

明确不做的事：不生成「建议采用 / 不推荐」这类结论。机器没有用过任何一项
技术，替一段不存在的经历发言，比不给结论更糟。

## 实测性能

编码部分在本机以 2 线程（模拟 2 核机器）测得：

| 指标 | 数值 |
| --- | --- |
| 单次中文查询编码延迟（中位数） | 5.3 ms |
| 编码延迟 P95 | 5.8 ms |
| 模型加载耗时 | 761 ms |
| 输出维度 / L2 范数 | 384 / 1.000000 |
| Embedding 模型体积（int8） | 113 MB |
| Embedding 模型体积（fp32 对比） | 448 MB |

容器部分在 Docker Compose 下测得：

| 指标 | 数值 |
| --- | --- |
| 网关镜像体积 | 757 MB（含 onnxruntime，不含 torch） |
| 网关常驻内存（模型已加载） | 501 MiB |
| 网关常驻内存（模型未加载） | 79 MiB |
| 雷达常驻内存 | 45 MiB |
| 容器内进程用户 | appuser（uid 1000，非 root） |
| 端到端联调结果 | 21 项全通过 |

网关内存主要花在 onnxruntime 的推理内存池上。compose 里给它限了 1200m，
所以在 3.4G 内存、已跑着其他服务的机器上也不会挤兑别人。

雷达单次采集的实测吞吐（20 条条目）：

| 指标 | 数值 |
| --- | --- |
| 采集条目 | 20 条（Hacker News 10 + GitHub Trending 10，V2EX 源失败被隔离） |
| 生成摘要 | 20 条全部成功 |
| 网关命中 | 0 次（exact 模式，每条各自生成） |
| 消耗 Token | 1200（prompt 840 + completion 360） |

### 三个采集源的可达性（2026-09-30 实测）

同一份代码在两种网络下的结果不同，且**失败是「超时」不是「报错」**，
从日志里只看到源挂了、看不到为什么。实测记录如下，部署前先照这张表核对：

| 采集源 | 家宽直连（daifamily） | 阿里云 ECS 直连 | 走代理后 |
| --- | --- | --- | --- |
| Hacker News API | ✓ 1.0s | ✓ 1.0s | ✓ |
| GitHub Trending 页面 | ✓ | ⚠️ **时通时断**（20s 超时） | ✓ |
| V2EX topics API | ✗ 20s 超时 | ✗ 10s 超时 | ✓ 0.7s，返回 34KB 真实 JSON |

两条结论：

1. **V2EX 不是接口下线，是被网络挡住** —— 不挂代理时两地都连不上，挂上代理后
   0.7 秒返回正常 JSON。**别因为直连不通就下结论说接口死了**（我差点这么判）。
2. **GitHub Trending 在云主机上是概率性超时**，不是稳定可用也不是稳定失败；
   同一台机器连续两次采集，一次挂一次成。要稳定采集就得给容器配代理，
   或在 `RADAR_INTERVAL_MINUTES` 之外再加重试。

单源失败不会影响其他源，也不会让整轮采集失败（`failed_sources` 会记下是谁），
所以上述任一源不通时，采集仍能出结果，只是条目变少。

## 快速开始（本地）

```bash
# 1. 建虚拟环境并安装依赖
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 下载 Embedding 模型（国内走 hf-mirror 镜像）
bash scripts/download_embedding_model.sh

# 3. 配置环境变量
cp .env.example .env
#    必填 LLM_API_KEY；本地开发可不设 GATEWAY_AUTH_TOKEN

# 4. 启动网关
.venv/bin/uvicorn gateway.app.main:app --host 127.0.0.1 --port 8000

# 5. 另开一个终端，启动雷达查询接口
.venv/bin/uvicorn radar.app.main:app --host 127.0.0.1 --port 8001
```

单跑一次雷达采集：

```bash
.venv/bin/python -m radar.app.runner --once
```

## 部署（Docker Compose）

```bash
cp .env.example .env
#    编辑 .env：填 LLM_API_KEY，并设置
#    GATEWAY_AUTH_TOKEN=$(openssl rand -hex 32)

bash scripts/download_embedding_model.sh   # 模型挂载进容器，不打进镜像
docker compose up -d --build
```

网关绑在宿主机的 `127.0.0.1:8000`。雷达绑在 `8085:8001`
—— 那是**唯一一处故意对外的监听**（公开的只读展示页面，无鉴权）。

### 对外暴露

分两件事，不要混在一起：

**① 公开的雷达页面（本项目当前的对外形态）**

只读、无鉴权、面向陌生人：标题 + 中文摘要 + 标签 + 原始链接。
服务端没有任何写接口（只有 `GET /`、`/static/*`、`/health`、`/radar/items`），
所以直接对公网开放的风险面很小。**FastAPI 默认打开的 `/docs`、`/redoc`、
`/openapi.json` 在代码里显式关掉** —— 否则会顺带把全部接口与数据结构露出去。

部署这套的完整过程（含云主机安全组、采集代理、踩到的坑）见 `DESIGN.md`；要点是：

- 云主机安全组需要放行该端口，这一步只能在云控制台做
- 页面与静态资源发 `Cache-Control: no-cache, must-revalidate`，JSON 接口故意不发
- 采集源里 V2EX 与 GitHub 从大陆云主机直连不通，需要给**只有雷达**注入代理：
  `.env` 里设 `COLLECTOR_HTTP_PROXY`，compose 会把它转成 `HTTP_PROXY`/`HTTPS_PROXY`。
  ⚠️ **`NO_PROXY` 必须包含 `gateway`** —— 否则雷达调本机网关也会被绕去代理，
  代理不认识这个服务名，整条采集链路会当场全断，而现象只是「采集全部失败」。

**② 网关 API：默认不对外。** 网关持有上游大模型的 Key，绑回环就够了 ——
雷达与网关在同一 Docker 网络内直连，不需要经过公网。
确有需要时再经反向代理加一层，并在代理侧注入令牌头：

```nginx
location /v1/ {
    proxy_set_header X-Gateway-Token "<GATEWAY_AUTH_TOKEN>";
    proxy_set_header Host $host;
    proxy_pass http://127.0.0.1:8000;
}
```

### 内存限制

compose 里给网关限了 `1200m`、雷达限了 `256m`，避免在小内存机器上与已有服务互相挤兑。
按实际可用内存调整 `docker-compose.yml` 里的 `mem_limit`。

## 定时采集

雷达服务内置定时采集（`radar/app/scheduler.py`），随接口进程一起启停，默认每 360 分钟一次。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RADAR_INTERVAL_MINUTES` | 360 | 采集间隔（分钟）；配置非法时退回默认值 |
| `RADAR_SCHEDULER_ENABLED` | true | 设为 false 可关闭定时采集 |
| `RADAR_ITEM_LIMIT` | 10 | 每个来源抓取条数上限 |

采集任务整体兜住异常：单次失败只记日志，不会带崩查询接口。
上一轮未跑完时跳过本轮，进程重启后错过多次触发只补跑一次。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 上游大模型的 OpenAI 兼容地址 |
| `LLM_MODEL` | `deepseek-chat` | 上游模型名 |
| `LLM_API_KEY` | 空 | 上游密钥，只能写入 `.env` |
| `EMBEDDING_MODEL_PATH` | `./models/multilingual-e5-small` | 模型目录 |
| `EMBEDDING_THREADS` | 2 | ONNX 推理线程数 |
| `VECTOR_STORE_PATH` | `./data/chroma` | ChromaDB 持久化目录 |
| `CACHE_SIMILARITY_THRESHOLD` | 0.92 | 命中所需最低相似度 |
| `CACHE_TTL_SECONDS` | 86400 | 缓存有效期 |
| `GATEWAY_AUTH_TOKEN` | 空 | 共享令牌；为空则不校验（仅限本地） |
| `GATEWAY_BASE_URL` | `http://127.0.0.1:8000` | 雷达调用网关的地址 |

## API

网关：

```text
POST   /v1/chat/completions   兼容 OpenAI 格式；响应头 X-Cache 为 HIT / MISS
GET    /health                探活
GET    /cache/stats           命中率与 Token 统计
DELETE /cache                 清空缓存（需要令牌）
```

雷达：

```text
GET /radar/items                     最近条目
GET /radar/items?source=hackernews   按来源过滤
GET /radar/items?keyword=Python      按标题关键字过滤
GET /radar/items?sort=heat           按动量排在源内的名次（默认按时间）
```

响应里的 `metrics` 是采集当时从源站原样取到的量化信号，字段随源而异
（`points` / `comments` / `replies` / `node` / `stars` / `stars_today` /
`language`）。其中两个是本库算出来、不是抓来的：

- `heat_rank_in_source` —— 按本源动量排的名次。用名次而不是直接比数值，
  是因为 GitHub 的今日新增星数与 HN 的点数不是同一量纲，
  直接比大小会让某个源恒定霸占前排。
- `corroborating_sources` —— 还有哪些源提到了同一个东西（多源互证）。
  只有一处提及时不会出现这个字段。

指标只存事实、不做换算：展示口径要改时不需要重采历史数据。

## 测试

```bash
.venv/bin/python -m pytest
```

只收集 `radar/tests` 与 `gateway/tests`（见 `pytest.ini`）。不加这个限制的话，
`scripts/dev/e2e_cache_test.py` 会被当成测试收集起来 —— 那个文件是给人手动
跑的端到端脚本，导入时就会去连真实网关，于是「跑一次全量测试」会变成
「跑一次 e2e，参数还是 pytest 自己的命令行参数」。

覆盖网关注解缓存行为、鉴权、向量存储、配置解析，以及雷达的采集器、管道、存储、
定时任务的配置容错与异常兜底。

## 进度

- [x] 本地项目目录与 Python 3.11 虚拟环境初始化
- [x] Git 仓库初始化
- [x] Git 忽略规则配置
- [x] 项目目录骨架创建
- [x] Python 依赖安装
- [x] 语义缓存网关
- [x] AI 技术雷达
- [x] Docker Compose 部署
- [x] Embedding 模型上线（ONNX int8）
- [x] 定时采集
- [x] 生产安全配置（回环绑定 + 共享令牌）
- [x] 自动化测试
- [x] 采集指标（点数 / 评论数 / 回复数 / 星数）
- [x] 跨源归并（多源互证）
- [x] 采集历史快照
- [ ] 证据等级（公开规则分档）
- [ ] 页面改为按证据展示

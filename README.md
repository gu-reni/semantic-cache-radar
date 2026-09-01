# Semantic Cache Radar

面向大模型应用的语义缓存网关与 AI 技术雷达系统。

## 项目目标

- 通过本地 Embedding 和向量检索复用相似问题的历史回答，减少大模型 Token 消耗。
- 提供兼容 OpenAI Chat Completions 格式的 FastAPI 网关。
- 定时聚合技术社区热点，调用网关生成摘要和技术标签。
- 使用 Docker Compose 部署到阿里云 ECS。

## 模块

- `gateway/`：LLM 语义缓存网关。
- `radar/`：AI 技术雷达。
- `shared/`：模块间必要的共享代码。
- `data/`：SQLite 与 ChromaDB 的本地运行数据，不提交到 Git。
- `models/`：本地 Embedding 模型，不提交到 Git。

## 当前进度

- [x] 本地项目目录与 Python 3.11 虚拟环境初始化
- [x] Git 仓库初始化
- [x] Git 忽略规则配置
- [x] 项目目录骨架创建
- [ ] Python 依赖安装
- [ ] 语义缓存网关
- [ ] AI 技术雷达
- [ ] Docker Compose 部署
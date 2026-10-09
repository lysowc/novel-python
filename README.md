# novel-python

AI 小说工坊的 **Python 3.12 + FastAPI** 后端 + 自带前端（Vue 3 + Tailwind，源码在 `web/`）。API 契约与 PHP/webman 版一致、共用同一套 MySQL 表结构，可无缝切换后端。

## 架构

- **Web**：FastAPI + uvicorn（同步端点自动跑线程池）
- **前端**：本项目 `web/`（Vue 3 + TypeScript + Tailwind v4 + shadcn-vue），构建产物输出到 `static/`，由后端直接服务——前后端同仓自包含
- **数据库**：SQLAlchemy 2 + PyMySQL（MySQL 8，表结构与 PHP 版一致，可共用同一库）
- **会话**：签名 Cookie（SessionMiddleware），bcrypt 校验（兼容 PHP `password_hash` 的哈希，管理员账号两版通用）
- **AI**：httpx 流式调用任意 OpenAI 兼容接口；Redis List 队列 + 独立 worker 进程消费；SSE 任务流
- **三大升级**：
  - **检索式上下文**：jieba + BM25，生成每章前按相关性召回已被滚动窗口丢弃的历史章节（`app/services/retrieval.py`）
  - **结构化记忆 v2**：8 个记忆槽（剧情状态/人物/伏笔 open-resolved/世界观/时间线…），旧格式自动迁移（`app/services/memory.py`）
  - **一致性审校**：`consistency_check` 任务 + 报告落库 + 每 N 章自动触发
- **连续续写**：章节页「连续续写 N 章」逐章排队；中途某章失败**跳过继续**，坏章可重新生成/重试
- **多进程 worker**：默认 3 个进程并行（同一本小说串行、不同小说并行），`WORKERS=N` 调整；启动时自动恢复卡死任务
- **AI 日志记录 Prompt**：每次调用把实际使用的 system prompt 写入日志（`ai_log.prompt`），后台「AI 日志」可查看

## 环境要求

- Python 3.12（推荐 `uv` 管理；也可用 Homebrew `python@3.12` + venv）
- MySQL 8、Redis
- 前端改动需要 Node 20+ / pnpm（**不改前端则不需要**，直接使用仓库内的构建产物）

## 快速开始

```bash
cd novel-python

# 1. 创建项目内虚拟环境并安装依赖
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
# （不用 uv 的话：python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt）

# 2. 配置环境
cp .env.example .env    # 填 MySQL/Redis 连接、SESSION_SECRET

# 3. 初始化数据库：迁移 + 管理员/分类/Prompt/系统配置
#    （默认管理员 admin / admin123，可用 -u -p 自定义）
.venv/bin/python -m commands.install

# 4. 启动全部服务（API + AI worker + mock AI）
bash scripts/start.sh

# 5. 停止
bash scripts/stop.sh
```

启动后访问 **http://127.0.0.1:8800**（API 与前端页面同一个地址），后台登录 admin / admin123。

## 数据库迁移

```bash
# 应用未执行的迁移（migrations/*.sql，执行记录在 migrations 表）
.venv/bin/python -m commands.migrate

# 清库重建（⚠ 删除全部表与数据后重跑所有迁移）
.venv/bin/python -m commands.migrate fresh
```

- `commands.install` 内部会先执行迁移再写入种子数据；重复执行安全（管理员密码会重置为参数值；Prompt 只按指纹升级旧版默认内容、不动自定义修改）
- 与 PHP 版共用同一库时**不要跑 `fresh`**

## 启动 / 停止

```bash
bash scripts/start.sh                  # API(8800) + worker + mock AI(8899)
MOCK=0 bash scripts/start.sh           # 不启动 mock（已接真实 AI）
PORT=8800 bash scripts/start.sh        # 自定义端口
WORKERS=5 bash scripts/start.sh        # 自定义 AI worker 进程数（默认 3）

bash scripts/stop.sh                   # 全部停止
```

三个进程分别等价于：

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8800  # API
.venv/bin/python -m app.worker                             # AI 任务消费进程（必须）
.venv/bin/uvicorn tests.mock_ai_server:app --port 8899     # Mock AI（联调用）
```

日志在 `runtime/`（api.log / worker.log / mock.log）。脚本按 `$PYTHON` 环境变量 → 项目内 `.venv` → 系统 `python3` 的顺序选择解释器。

## 接入真实 AI

后台「AI 配置」页（或直接改 `ai_provider` / `ai_model` 表）：

1. **Provider**：`name` 随意；`base_url` 填服务商地址（如 `https://api.deepseek.com`，OpenAI 兼容即可，自动补 `/v1/chat/completions`）；`api_key` 填密钥；设为默认
2. **Model**：`name` 填模型名（如 `deepseek-chat`），设为默认
3. 之后所有 AI 调用（点子聊天/设定/大纲/章节/摘要/记忆/审校）自动走真实模型，任务状态在后台「AI 任务」与「AI 日志」可查

没有 Key 时用 mock 跑通全链路：保持 Provider `base_url = http://127.0.0.1:8899`（start.sh 默认启动 mock）。

## 前端（web/）

- 仓库内已含构建产物（`static/`），**不改前端则无需任何操作**
- 改前端源码时：

```bash
cd web
pnpm install          # 首次
pnpm build            # 构建产物直接输出到 ../static/（vite 已配置）
# 开发模式：pnpm dev（5173，/api 已代理到 127.0.0.1:8800）
```

- 前端技术栈与页面结构见 `web/README.md`；API 契约与 SSE 事件格式与 PHP 版完全一致

## 验收与自测

```bash
# 三大升级回归自测（检索召回/记忆 v2/审校规整，自动造数并清理）
.venv/bin/python -m commands.verify

# 全链路验收（登录/CRUD/鉴权/AI 任务/SSE 流式/记忆 v2/审校报告）
# 前提：服务已启动（start.sh），且 Provider 指向 mock 或真实可用 AI
bash tests/acceptance.sh http://127.0.0.1:8800
```

## 线上部署

- **前端构建产物（`static/`）已随仓库提交**：clone 后无需 Node/pnpm，装好 Python 依赖、初始化数据库、`bash scripts/start.sh` 即可访问完整页面
- 如果修改了前端源码：`cd web && pnpm install && pnpm build`，然后把 `static/` 的变更一并提交
- 后端启动方式与本地一致（`.env` 配好线上数据库/Redis，`SESSION_SECRET` 换随机值）
- 常见报错 `Failed to load module script ... MIME type "text/html"`：说明服务器上的 `static/` 缺失或过期，拉最新代码（或重新构建提交）即可；后端已对 `/assets/*` 缺失返回显式 404，不会再拿 HTML 冒充 JS

## 常见问题

- **端口 8800 被占**：`PORT=8801 bash scripts/start.sh`（改端口后验收脚本、web/vite.config.ts 代理要同步改）
- **AI 任务一直 pending**：worker 没启动（start.sh 已包含；单独跑 `.venv/bin/python -m app.worker`）
- **登录提示 401**：`.env` 的 `SESSION_SECRET` 变更会使旧会话失效，重新登录即可
- **admin 密码忘了**：`.venv/bin/python -m commands.install -u admin -p 新密码` 重置
- **想完全独立于 PHP 版**：`.env` 换新库名，然后 `migrate fresh` + `install` 全新初始化

## 目录结构

```
app/                    FastAPI 后端
  main.py               入口（会话中间件/鉴权/SPA 静态兜底）
  config.py             pydantic-settings（读 .env）
  db.py / models.py     SQLAlchemy 引擎 / 全部 15 张表模型
  auth.py helpers.py    会话鉴权 / 响应包装与工具
  routers/              front（前台 API）/ admin（后台 API + 点子聊天 SSE）/ ai_tasks（任务 + SSE 流）
  services/             ai_client / ai_service / context_builder / retrieval / memory /
                        prompt_service / prompt_defaults / task_service
  worker.py             AI 任务消费进程（独立进程）
commands/               migrate（迁移）/ install（初始化）/ verify（回归自测）
migrations/             0001_init.sql / 0002_consistency.sql
web/                    Vue 3 + Tailwind 前端源码（构建产物 → static/）
static/                 前端构建产物（后端直接服务，gitignore）
scripts/                start.sh / stop.sh（一键启动/停止）
tests/                  mock_ai_server.py / acceptance.sh
runtime/                运行日志与 pid（gitignore）
requirements.txt        全部 Python 依赖（精确锁定）
```

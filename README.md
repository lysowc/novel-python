# novel-python

AI 小说工坊的 **Python 3.12 + FastAPI** 后端。API 契约与 PHP/webman 版完全一致，与 PHP 版**共用同一个 MySQL 库**，可无缝替换后端——前端（Vue 3 + Tailwind，位于 `../php/webman/web`）零改动。

## 架构

- **Web**：FastAPI + uvicorn（同步端点自动跑线程池）
- **数据库**：SQLAlchemy 2 + PyMySQL（MySQL 8，表结构与 PHP 版一致）
- **会话**：签名 Cookie（SessionMiddleware），bcrypt 校验（兼容 PHP `password_hash` 的哈希，管理员账号两版通用）
- **AI**：httpx 流式调用任意 OpenAI 兼容接口；Redis List 队列 + 独立 worker 进程消费；SSE 任务流
- **三大升级**（与 PHP 版同步）：
  - **检索式上下文**：jieba + BM25，生成每章前按相关性召回已被滚动窗口丢弃的历史章节（`app/services/retrieval.py`）
  - **结构化记忆 v2**：8 个记忆槽（剧情状态/人物/伏笔 open-resolved/世界观/时间线…），旧格式自动迁移（`app/services/memory.py`）
  - **一致性审校**：`consistency_check` 任务 + 报告落库 + 每 N 章自动触发

## 环境要求

- Python 3.12（用 `uv` 管理；也可用 Homebrew `python@3.12` + venv）
- MySQL 8、Redis（与 PHP 版共用即可）
- 前端只需 Node 20+/pnpm（仅在**改前端**时需要；不改前端则后端直接服务构建产物）

## 快速开始

```bash
cd /Users/sora/python/novel-python

# 1. 创建虚拟环境并安装依赖（Python 3.12）
uv venv --python 3.12 /Users/sora/uv/novel-python
uv pip install --python /Users/sora/uv/novel-python/bin/python -r requirements.txt

# 2. 配置环境
cp .env.example .env    # 填 MySQL/Redis 连接、SESSION_SECRET

# 3. 初始化数据库：迁移 + 管理员/分类/Prompt/系统配置
#    （默认管理员 admin / admin123，可用 -u -p 自定义）
/Users/sora/uv/novel-python/bin/python -m commands.install

# 4. 启动全部服务（API + AI worker + mock AI）
bash scripts/start.sh

# 5. 停止
bash scripts/stop.sh
```

启动后访问 **http://127.0.0.1:8800**（API 与前端页面同一个地址），后台登录 admin / admin123。

## 数据库迁移

```bash
# 应用未执行的迁移（migrations/*.sql，记录在 migrations 表）
/Users/sora/uv/novel-python/bin/python -m commands.migrate

# 清库重建（⚠ 删除全部表与数据后重跑所有迁移）
/Users/sora/uv/novel-python/bin/python -m commands.migrate fresh
```

- `commands.install` 内部会先执行迁移，再写入种子数据；重复执行是安全的（已存在的管理员会重置密码为参数值，Prompt 只按指纹升级旧版默认内容、不动自定义修改）
- 与 PHP 版共用同一库时**不要跑 `fresh`**，否则 PHP 版数据也被清掉

## 启动 / 停止（scripts/）

```bash
bash scripts/start.sh                  # API(8800) + worker + mock AI(8899)
MOCK=0 bash scripts/start.sh           # 不启动 mock（已接真实 AI）
PORT=8800 bash scripts/start.sh        # 自定义端口

bash scripts/stop.sh                   # 全部停止
```

三个进程分别等价于：

```bash
/Users/sora/uv/novel-python/bin/uvicorn app.main:app --host 0.0.0.0 --port 8800  # API
/Users/sora/uv/novel-python/bin/python -m app.worker                             # AI 任务消费进程（必须）
/Users/sora/uv/novel-python/bin/uvicorn tests.mock_ai_server:app --port 8899     # Mock AI（联调用）
```

日志在 `runtime/`（api.log / worker.log / mock.log）。

## 接入真实 AI

后台「AI 配置」页（或直接改 `ai_provider` / `ai_model` 表）：

1. **Provider**：`name` 随意；`base_url` 填服务商地址（如 `https://api.deepseek.com`，OpenAI 兼容即可，会按约定自动补 `/v1/chat/completions`）；`api_key` 填密钥；设为默认
2. **Model**：`name` 填模型名（如 `deepseek-chat`），设为默认
3. 之后所有 AI 调用（点子聊天/设定/大纲/章节/摘要/记忆/审校）自动走真实模型，任务状态在后台「AI 任务」与「AI 日志」可查

没有 Key 时用 mock 跑通全链路：保持 Provider `base_url = http://127.0.0.1:8899`（start.sh 默认会启动 mock）。

## 前端

- **不改前端**：什么都不用做。`.env` 的 `STATIC_DIR` 默认指向 `../php/webman/public`（Vue 构建产物），后端直接服务页面与 `/assets`
- **改前端（开发）**：在 `../php/webman/web` 跑 `pnpm dev`，把 `vite.config.ts` 的代理目标从 `8787` 改成 `8800`；改完 `pnpm build` 产物回到 `public/`
- 前端源码、组件、API 契约说明见 `../php/webman/web` 与 `../php/webman/README.md`

## 验收与自测

```bash
# 三大升级回归自测（检索召回/记忆 v2/审校规整，自动造数并清理）
/Users/sora/uv/novel-python/bin/python -m commands.verify

# 全链路验收（登录/CRUD/鉴权/AI 任务/SSE 流式/记忆 v2/审校报告）
# 前提：服务已启动（start.sh），且 Provider 指向 mock 或真实可用 AI
bash tests/acceptance.sh http://127.0.0.1:8800
```

## 常见问题

- **端口 8800 被占**：`PORT=8801 bash scripts/start.sh`（改端口后验收/前端代理也要同步改）
- **AI 任务一直 pending**：worker 没启动（`bash scripts/start.sh` 里包含它；单独跑 `python -m app.worker`）
- **登录提示 401**：Cookie 的 `SESSION_SECRET` 变了会导致旧会话失效，重新登录即可
- **admin 密码忘了**：`python -m commands.install -u admin -p 新密码` 重置
- **想完全独立于 PHP 版**：把 `.env` 的 `DB_DATABASE` 换新库名并 `python -m commands.migrate fresh` + `python -m commands.install` 全新初始化

## 目录结构

```
app/
  main.py                FastAPI 入口（会话中间件/鉴权/SPA 静态兜底）
  config.py              pydantic-settings（读 .env）
  db.py                  SQLAlchemy engine/session
  models.py              全部 15 张表模型（与 PHP 版表结构一致）
  auth.py                会话鉴权
  helpers.py             ok/fail 响应包装、字数统计
  routers/
    front.py             前台 API（/api：首页/分类/列表/详情/目录/阅读）
    admin.py             后台 API（/api/admin：登录/CRUD/点子聊天 SSE/配置）
    ai_tasks.py          任务 CRUD + SSE 任务流
  services/
    ai_client.py         OpenAI 兼容客户端（流式/非流式 + 调用日志）
    ai_service.py        创作编排（设定/大纲/章节/摘要/记忆/审校）
    context_builder.py   分层上下文组装（设定+记忆+摘要+召回+最近正文+大纲）
    retrieval.py         相关章节检索（jieba + BM25）
    memory.py            结构化记忆 v2（解析/渲染/规整）
    prompt_service.py    Prompt 渲染（{{变量}}）
    prompt_defaults.py   默认 Prompt / 配置种子
    task_service.py      Redis 队列 + 任务执行
  worker.py              AI 任务消费进程（独立进程）
commands/
  migrate.py             数据库迁移（run / fresh）
  install.py             初始化（迁移 + 管理员/分类/Prompt/配置）
  verify.py              三大升级回归自测
migrations/              0001_init.sql / 0002_consistency.sql
scripts/                 start.sh / stop.sh（一键启动/停止）
tests/                   mock_ai_server.py / acceptance.sh
runtime/                 运行日志与 pid（gitignore）
```

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
  - **记忆按章快照**：每章写完保存"第 N 章结束时"的记忆（`novel_memory_snapshot`）；重写第 N 章时上下文只取第 N 章之前的章节和第 N-1 章的记忆快照，重写中间章节只更新该章快照、不改全书最新记忆（`app/services/memory_store.py`）
  - **一致性审校**：`consistency_check` 任务 + 报告落库 + 每 N 章自动触发
- **连续续写**：章节页「连续续写 N 章」逐章排队；中途某章失败**跳过继续**，坏章可重新生成/重试；遇到 AI 配置/鉴权类错误（未配置、Key 错误、余额不足、模型不存在）或连续 3 章失败则自动停止
- **失败重试**：章节页列出需要处理的失败任务，一键重试。正文已保存但摘要/记忆失败的任务，重试只补做摘要/记忆（不会重新生成或多写一章）；摘要/记忆偶发失败会先自动再试一次；被停止的连续续写重试后接着写完剩余章节
- **取消任务**：排队中的任务直接取消；执行中的任务在流式输出时 1 秒内中止；连续续写可选「写完本章后停止」
- **章节历史版本**：重新生成 / 手动编辑 / 恢复 / 删除前自动保存旧版本（每章最近 20 个），可与当前正文逐段对比、一键恢复；删除的章节可在「已删除」里找回（`chapter_version`）
- **草稿审阅**：系统设置 `chapter_auto_publish=0` 时 AI 新章节先存为草稿，读者不可见（AI 上下文照常使用），在章节页单章或批量发布
- **用量统计**：AI 日志记录所属小说、费用（模型单价，元/百万 tokens）；「用量统计」页按天、小说、任务类型、模型汇总。接口未返回 usage 时按字数估算并标注
- **整本导出**：TXT / Markdown / EPUB 3（纯标准库生成），可选是否包含草稿
- **记忆增量合并**：AI 重写记忆时漏掉的未回收伏笔、重要物品、世界观事实自动补回；被移出的人物进入「其他已登场人物」名册
- **审校读正文**：一致性审校额外抽查最近 3 章正文，检查细节矛盾
- **检索正文片段**：除摘要外，对历史正文按段做 BM25 检索并与摘要排名融合（RRF），召回时附上相关原文片段
- **上下文总长度预算**：`context_max_chars`（默认 60000 字符）超出时依次压缩最近正文、召回章节、摘要窗口
- **记忆重建**：「记忆」页可从第 N 章起逐章重算记忆（重写中间章节后让后续跟上；旧小说从第 1 章完整重建）
- **审校定位**：审校报告里的章号可点开查看，并「按建议重写本章」（旧版本自动进历史版本）
- **按模型上下文窗口**：在「AI 配置」填写模型上下文窗口后，自动按「窗口 − 输出 − 预留」限制上下文
- **安全**：登录失败限流（同 IP+账号 10 分钟 5 次）；默认 SESSION_SECRET / admin123 密码在仪表盘和启动日志里提醒
- **与 PHP 版共用 Redis**：PHP 的 worker 不认 Python 版的小说锁与取消信号，两版 worker 同时运行时可在 `.env` 设 `AI_QUEUE_KEY` 使用独立队列
- **小说回收站**：删除小说先进回收站（`novel.deleted_at`），可恢复；保留天数（默认 30 天）到期后自动彻底删除
- **操作记录**：后台所有新建/修改/删除/登录自动记录（谁、何时、做了什么、是否成功、IP），「操作记录」页可搜索
- **模型分工**：「AI 配置」里指定写作模型（正文/设定/大纲）和辅助模型（摘要/记忆/审校/质检）
- **费用上限**：系统设置里设每日总上限、每本小说每日上限，超出后不再发起新任务（连续续写会停下，第二天自动恢复）
- **定时续写**：小说「自动化」页设置每天几点续写几章（按 `TIMEZONE`，默认 Asia/Shanghai）；由 worker 内的调度线程执行
- **大纲续写**：大纲快用完时提醒；开启「自动续写大纲」后写下一章前自动往后规划；也可手动续写
- **质量检查**：字数明显不足、截断、和上一章大段重复、段落重复时自动重写一次，保留较好的一版并在章节页提醒
- **重写中间章节后自动重建后续记忆**（可在系统设置关闭）
- **取消更及时**：流式读取放在后台线程，模型还没吐出第一个字时也能在约 1 秒内取消
- **人物关系图 / 伏笔一览**：「记忆」页新增两个视图
- **读者书签**：阅读页可加书签，小说详情页查看；书签与阅读进度存在读者自己的浏览器里
- **多进程 worker**：默认 3 个进程并行，`WORKERS=N` 调整；同一本小说由 Redis 锁（`ai:lock:novel:{id}`，心跳续期）保证串行、不同小说并行，任务领取为原子操作不会重复执行；启动时自动恢复卡死任务并清除残留锁
- **长篇资料库**（小说详情页「资料库」）：AI 每章写完自动维护**人物档案**（外貌、性格、能力、境界及其变化、关系、现状）、**名词表**（地名/功法/物品/势力的统一写法）和**分段总结**（每 `arc_summary_size` 章一段前情提要）。生成新章节时只带上本章相关的人物和名词，早期剧情用分段总结代替逐章摘要；作者可手动修改并「锁定」，锁定的条目 AI 不再改
- **增量记忆**：记忆更新改为补丁格式，AI 只输出本章变化，没提到的内容原样保留；活跃人物、时间线、伏笔等有上限，超出的自动归档
- **重复桥段检测**：每章摘要与前 30 章比对，情节太像时在章节页提醒（`repetition_check_enabled`）
- **一致性审校默认每 10 章自动运行一次**，并检查人设漂移、境界倒退、名词不一、套路重复
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

> 升级到新版本后先执行一次 `.venv/bin/python -m commands.migrate`（0004 记忆快照、0005 章节版本、0006 用量统计、0007 模型上下文窗口、0008 回收站/操作记录/自动化、0009 长篇资料库），再重启服务。

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
# 单元/接口测试（sqlite + fakeredis + 假 AI，不需要 MySQL / Redis / 真实模型）
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

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
  routers/              front（前台 API）/ admin/（后台 API，按领域拆分）/ ai_tasks（任务 + SSE 流）
                        versions（章节历史版本）/ stats（用量统计）/ export（整本导出）
  services/             ai_client / ai_service / context_builder / retrieval / memory /
                        prompt_service / prompt_defaults / task_service
  worker.py             AI 任务消费进程（独立进程）
commands/               migrate（迁移）/ install（初始化）/ verify（回归自测）
migrations/             0001_init.sql … 0008_trash_audit_automation.sql
web/                    Vue 3 + Tailwind 前端源码（构建产物 → static/）
static/                 前端构建产物（后端直接服务，gitignore）
scripts/                start.sh / stop.sh（一键启动/停止）
tests/                  pytest 测试 / mock_ai_server.py / acceptance.sh
runtime/                运行日志与 pid（gitignore）
requirements.txt        全部 Python 依赖（精确锁定）
```

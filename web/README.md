# novel-python 前端

AI 小说工坊的前端：Vue 3 + TypeScript + Vite + Tailwind CSS v4 + shadcn-vue + Pinia。

- 构建产物输出到 `../static/`（vite 已配置 `outDir`），由 FastAPI 后端直接服务
- 开发模式 `pnpm dev`（5173 端口，`/api` 已代理到 `http://127.0.0.1:8800`）
- API 契约与 SSE 事件格式见后端 `app/routers/` 与根目录 README

```bash
pnpm install   # 首次
pnpm dev       # 开发
pnpm build     # 构建 → ../static/
```

页面结构：

```
src/views/admin/   后台（登录/仪表盘/小说/章节/点子聊天/AI 配置/任务/日志/Prompt/设置）
src/views/front/   前台（首页/分类/详情/阅读）
src/components/    admin / common / front / ui（shadcn-vue）
src/api/           http / sse / 接口定义 / mock（VITE_USE_MOCK=1 纯前端演示）
src/stores/        auth / reader / site / theme
```

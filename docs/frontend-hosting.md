# 同域前端托管

`domainsmanager-server` 在同一端口提供前端页面和后端接口：页面使用 `/`，API 使用 `/api/v1`，健康检查使用 `/health/live` 与 `/health/ready`。未知 API 和健康检查路径保持 JSON 404，不会回退到前端页面。

发布 wheel 前必须在源码根目录执行：

```powershell
cd frontend
npm ci
npm run build
cd ..
uv build
```

构建流程会将 `frontend/dist` 放入 wheel 的 `domainsmanager_api/frontend`。安装 release wheel 后，目标服务器只需安装 Python 依赖并运行 `domainsmanager-server`，无需安装 Node.js。若包内资源不存在，页面请求会返回明确的 `frontend_unavailable` 503 错误。

`/assets/*` 使用长期不可变缓存；HTML 入口使用 `no-cache` 以便发布后立即加载新的资源清单。生产环境默认关闭 `/docs`、`/redoc` 和 `/openapi.json`；如需启用，设置 `DOMAINSMANAGER_DOCS_ENABLED=true`。

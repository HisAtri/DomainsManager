# DomainsManager Web

基于 React、TypeScript、Tailwind CSS v4 和 Radix UI 的前端应用。它通过 Vite 代理调用后端的 `/api/v1` 接口，不使用演示数据。

## 启动

先启动后端（默认 `127.0.0.1:7920`），再执行：

```powershell
cd frontend
npm install
npm run dev
```

开发服务器运行在 `http://127.0.0.1:5173`；`vite.config.ts` 将 `/api` 请求代理至后端。

## 已对接的接口

- 本地登录、注册、Token 刷新、退出登录
- 当前用户资料、密码、监控偏好
- 域名列表、创建、详情、更新、软删除
- 域名刷新任务、任务轮询、检查历史
- 管理员用户查询、封禁、解封
- 管理员全局域名查询

刷新任务中心仅展示当前浏览器会话创建的任务，因为后端目前没有任务列表接口。

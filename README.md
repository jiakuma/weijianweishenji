# 审计工作平台

面向审计业务的多模块智能工作平台：制度互斥检测、合同审计、底稿审核、资料库与智能助手。

## 目录

```
server/                 FastAPI 后端（Python 3.12+）
  app/
    main.py             应用入口、路由注册、中间件
    config.py           环境变量集中读取（唯一配置入口）
    database.py         引擎、Session、schema 隔离、迁移引导
    db.py               业务数据模型与兼容访问函数（过渡层）
    deps.py             统一响应构造
    errors.py           业务异常与全局处理器
    agent_run.py        Agent 决策循环
    agent_tools.py      Agent 工具注册
    milvus_store.py     Milvus Lite 向量检索
    modules/            业务模块（conflict / contract / workpaper / assistant）
  migrations/           Alembic 版本化迁移
web/                    Vue 3 + Vite + TypeScript 前端
```

## 快速启动

```bash
# 后端
cd server
pip install -r requirements.txt
cp ../.env.template ../.env      # 填写 .env
python -m app.main               # 默认 5281

# 前端
cd web
npm install
npm run dev                      # 默认 5280
```

完整环境变量说明见 `.env.template`。`DB_BACKEND=postgres` 时连接 PostgreSQL 15 并执行 Alembic 迁移；`DB_BACKEND=sqlite` 为本地单机降级路径，仅用于开发验证。

Milvus Lite 向量库默认数据目录为 `%LOCALAPPDATA%/audit-work-platform/milvus.db`（纯 ASCII 路径）。faiss 的 C++ 文件写入在 Windows 中文代码页下无法打开含中文的 UTF-8 路径，会导致 HNSW 索引文件无法生成，因此默认目录避开项目中文路径。项目内旧目录 `server/data/milvus.db` 保留作为备份；可通过环境变量 `MILVUS_URI` 覆盖为独立部署的 Milvus 服务地址或其他路径。

## 验证

```bash
cd server && pytest -q
cd web && npm run build
```

## 业务约定

- 顶栏是平台门面，不显示模块名；模块特点只在工作区体现。
- 智能助手上传的文件进入隔离临时区，不入资料库；资料库只能从「导入资料」入口入库。
- 面向用户的输出只给结论与原因，不暴露文件 ID、比对过程、技术注释与内部统计。
- 报告以结果为主，支持导出。

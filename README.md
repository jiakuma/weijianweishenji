# 审计工作平台

面向审计业务的一体化智能工作平台。把资料管理、智能问答、制度互斥检测、合同审计、底稿审核整合到一个工作台，本地部署、数据不出内网。

当前版本：v0.2.0（详见 [CHANGELOG.md](CHANGELOG.md)）

## 功能模块

| 模块 | 说明 |
|------|------|
| 审计助手 | 基于知识库问答，带引用溯源；支持寒暄、统计、多轮工具调用 |
| 制度互斥检测 | 上传多份制度，比对条款冲突，给风险等级与定位 |
| 合同审计 | 合同文本问题检查与跨文件勾稽差异 |
| 底稿审核 | 以审计实施方案为上位文件，逐项核对底稿是否支撑结论 |
| 资料库 | 文件导入、解析、自动分类、切片、原文查看、目录管理 |
| 历史问题库 | 历史审计问题定性库，供问答与判定参考 |
| 反馈监控 | 用户提交改进建议，管理员按时间/模块/状态筛选处理 |

四个审计入口共用统一的对话式工作区，通过左侧导航和面包屑区分；资料库、历史问题库为基础模块；反馈监控仅管理员可见。

## 技术栈

- 前端：Vue 3 + Vite + TypeScript + Element Plus + Pinia
- 后端：Python 3.12+ / FastAPI + SQLAlchemy，当前使用 SQLite，预留 PostgreSQL + Alembic
- 检索：Milvus Lite（HNSW 索引）+ bge-small-zh-v1.5 本地向量，向量库不可用时自动降级词法检索
- 解析：PDF / Word / PPT / Excel / 图片，扫描件走 RapidOCR
- 大模型：OpenAI 兼容接口（当前 DeepSeek），配置存数据库、不写入代码
- 部署：Nginx + systemd，Ubuntu 服务器

## 目录结构

```
server/                 FastAPI 后端
  app/
    main.py             应用入口、路由注册、鉴权中间件
    config.py           环境变量集中读取
    db.py               数据模型与业务访问函数
    agent_run.py        Agent 决策循环
    agent_tools.py      Agent 工具注册
    milvus_store.py     Milvus Lite 向量检索
    modules/            业务模块 conflict / contract / workpaper / assistant
  migrations/           Alembic 迁移（SQLite 模式不启用）
  data/                 业务数据（上传、切片、数据库、向量库）
web/                    Vue 3 前端
deploy/                 部署、更新、备份、迁移脚本与 DEPLOY.md
```

## 快速启动

```bash
# 后端（默认 5281）
cd server
pip install -r requirements.txt
cp ../.env.template ../.env      # 按需修改
python run.py

# 前端（默认 5280）
cd web
npm install
npm run dev
```

首次登录：管理员 `admin` / `Admin@12345`，首次登录强制修改密码。

完整环境变量说明见 `.env.template`，服务器部署见 [deploy/DEPLOY.md](deploy/DEPLOY.md)，产品需求见 [PRD.md](PRD.md)。

## 验证

```bash
cd server && pytest -q
cd web && npm run build
```

## 约定

- 面向用户的输出只给结论与原因，不暴露文件 ID、比对过程、技术注释与内部统计；不下违规定性，交给审计人员判断。
- 智能助手上传的文件进入隔离临时区，不入资料库；资料库只能从「导入资料」入口入库。
- 界面不放解释性、注释性、防御性文字。
- 数据只迁移一次，后续更新只更新代码，不动业务数据。

## 相关文档

- [PRD.md](PRD.md) — 产品需求文档
- [CHANGELOG.md](CHANGELOG.md) — 版本变更记录
- [deploy/DEPLOY.md](deploy/DEPLOY.md) — 部署与运维手册

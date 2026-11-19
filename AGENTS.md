# AGENTS.md — 与 AI 协作时的项目约定

## 目标

在保持现有功能可用的前提下，把项目对齐到团队 Vibe Coding 标准：FastAPI + SQLAlchemy async + PostgreSQL 15 + Alembic（后端），Vue 3 + Vite + TS strict + Pinia + Tailwind + Element Plus + vue-i18n + axios（前端），中台 SSO + JWT 双 cookie（鉴权）。

## 必读顺序

1. `README.md` — 运行与目录
2. `.env.template` — 全部配置项
3. `server/app/config.py` — 配置唯一入口
4. `server/app/database.py` — 引擎与 schema 隔离
5. `server/app/errors.py` / `deps.py` — 错误与响应契约
6. `web/src/service/request/` — 前端请求层

## 硬性约定

### 后端

- 配置只读 `app.config.settings`；禁止在业务代码中硬编码连接串、地址、密钥。
- 成功响应统一 `success_response(data)`；业务错误抛 `AppError` / `BadRequestError` / `NotFoundError` / `UnauthorizedError`，由全局异常处理器兜底。
- 数据库结构变更只走 Alembic：`alembic revision --autogenerate -m "..."`；禁止 `create_all` 与手写 `ALTER TABLE`。
- 新模型模块必须在 `migrations/env.py` 顶部补充模型导入。
- `DB_SCHEMA` 禁止使用 `public`。
- 新增 IO 代码一律 async；PDF/OCR/向量化等 CPU 或阻塞任务保留线程池，但线程内不得使用 async session。
- 业务模块放 `app/modules/<name>/`，路由层只做参数与响应，业务下沉 service。

### 前端

- 组件内禁止裸 `fetch`；统一走 `src/service/request` 与 `src/service/api/api_<domain>.ts`。
- 用户可见文案必须走 i18n，`zh-cn` / `en-us` 成对维护。
- 通用 UI 只用 Element Plus；同一职责不引入第二套组件库。
- SFC 顺序：`<template>` → `<script setup lang="ts">` → `<style lang="scss" scoped>`。
- 文件名 kebab-case，API 方法 snake_case。
- 构建目标 Chrome 80+。

### 产品行为（不可回退）

- 顶栏固定为平台门面「审计工作平台」，不出现模块名。
- 智能助手附件不入资料库；资料库仅由导入资料入口入库。
- 单文件导入由用户手工分类；文件夹导入按目录层级分类。
- 助手回答下方提供点赞 / 点踩 / 重试。
- 用户可见结果不显示文件 ID、内部比对统计、技术注释、定性标签。
- 底稿审核按「上位文件要求点 → 逐点符合性 → 汇总」输出，不输出长问题清单。

## 改动前

- 确认工作区干净：`git status --short`
- 大改动先建分支，小改动直接改 `main`
- 涉及模型变更必须同时生成迁移脚本

## 完成定义（DoD）

- 后端：`pytest -q` 通过；`/health` 返回 `{"status":"OK"}`（鉴权白名单）
- 前端：`npm run build` 通过；新增文案双语齐全
- 业务：改动所涉模块至少手工回归一次

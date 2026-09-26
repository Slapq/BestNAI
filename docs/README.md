# BestNAI 文档总览

## 用户优先阅读

1. `API_PUBLIC.md` — **对外 API 文档**（给用户看的，不暴露后端）
2. `USER_GUIDE.md` — 用户使用手册
3. `AI_MASTER_GUIDE.md` — 一站式 AI / 开发 / 运维总文档
4. `KEY_MANAGEMENT.md` — Key 管理指南
5. `NEWAPI_GUIDE.md` — NewAPI / One API 接入指南（内部运维）
6. `DEV_GUIDE.md` — 接口开发指南（面向开发者/内部）
7. `UPLOAD_AND_IMAGE.md` — 图片上传与复用
8. `ANLAS.md` — 计费与估算
9. `API.md` — 原始接口文档（旧）
10. `OPENAPI.md` — 原始接口总表（旧）
11. `FRONTEND_GUIDE.md` — 前端说明（旧）
12. `ARCHITECTURE.md` — 系统结构（内部）
13. `SECURITY.md` — 安全建议

## 推荐阅读顺序

### 给普通用户
1. `API_PUBLIC.md`
2. `USER_GUIDE.md`

### 给 AI / 开发者
1. `AI_MASTER_GUIDE.md`
2. `DEV_GUIDE.md`
3. `NEWAPI_GUIDE.md`

### 给管理员
1. `KEY_MANAGEMENT.md`
2. `AI_MASTER_GUIDE.md`
3. `SECURITY.md`

## 说明

- 对外公开接口默认只推荐 `/v1/chat/completions`
- 对外默认只推荐 `-anlas-0` 免费模型
- 管理端支持查看、禁用/启用、备注 NovelAI API Key

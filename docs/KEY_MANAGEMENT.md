# BestNAI Key 管理指南

本文档说明如何管理 NovelAI API Key，包括：

- 查看 Key 状态
- 禁用 / 启用 Key
- 设置备注
- 设计简单管理前端

---

## 1. 认证

所有管理接口都需要管理员认证：

```http
Authorization: Bearer <admin_secret>
```

---

## 2. 查看系统状态

### 请求

```http
GET /admin/status
```

### 返回

```json
{
  "total_keys": 3,
  "busy_keys": 1,
  "idle_keys": 1,
  "disabled_keys": 1,
  "queued_requests": 0,
  "image_store_entries": 4
}
```

---

## 3. 查看所有 Key

### 请求

```http
GET /admin/keys
```

### 返回

```json
{
  "total_keys": 3,
  "busy_keys": 1,
  "idle_keys": 1,
  "disabled_keys": 1,
  "queued_requests": 0,
  "keys": [
    {
      "key_id": "2f6c9a1d3e10",
      "index": 0,
      "masked_key": "pst-snE...qofD",
      "enabled": true,
      "busy": false,
      "note": "主力 Key"
    }
  ]
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| `key_id` | Key 的稳定 ID，用于后续更新 |
| `index` | 启动时顺序 |
| `masked_key` | 脱敏显示的 API Key |
| `enabled` | 是否启用 |
| `busy` | 当前是否正被请求占用 |
| `note` | 管理员备注 |

---

## 4. 更新 Key

### 请求

```http
PATCH /admin/keys/{key_id}
Content-Type: application/json
```

### 禁用 Key

```json
{
  "enabled": false
}
```

### 启用 Key

```json
{
  "enabled": true
}
```

### 设置备注

```json
{
  "note": "晚高峰备用 Key"
}
```

### 同时修改

```json
{
  "enabled": true,
  "note": "已恢复"
}
```

### 返回

```json
{
  "key": {
    "key_id": "2f6c9a1d3e10",
    "index": 0,
    "masked_key": "pst-snE...qofD",
    "enabled": true,
    "busy": false,
    "note": "已恢复"
  }
}
```

---

## 5. 新增 Key

### 请求

```http
POST /admin/keys
Content-Type: application/json
```

```json
{
  "api_key": "pst-xxxxxxxxxxxxxxxx",
  "note": "新加的备用 Key"
}
```

### 返回

```json
{
  "key": {
    "key_id": "a1b2c3d4e5f6",
    "index": 3,
    "masked_key": "pst-xxx...abc123",
    "enabled": true,
    "busy": false,
    "note": "新加的备用 Key"
  }
}
```

### 说明
- 新增后立即进入调度池
- 重复的 Key 会返回 409
- 必须是 `pst-` 开头的 NovelAI Key

---

## 6. 删除 Key

### 请求

```http
DELETE /admin/keys/{key_id}
```

### 返回

```json
{
  "key": {
    "key_id": "a1b2c3d4e5f6",
    "index": 3,
    "masked_key": "pst-xxx...abc123",
    "enabled": false,
    "busy": false,
    "note": "新加的备用 Key"
  }
}
```

### 说明
- **忙碌中的 Key 不能删除**，会返回 409
- 删除后会立即移出调度池
- 删除不会暴露完整密钥

---

## 7. Key 管理前端

系统已经提供一个简单的管理页面：

```text
GET /admin/ui
```

### 访问方式

1. 浏览器打开 `/admin/ui`
2. 输入 `admin_secret`
3. 点击“保存”
4. 页面会自动拉取 `/admin/keys`

### 页面功能

- 显示总 Key / 空闲 / 忙碌 / 禁用 / 排队数
- 查看所有 Key 的脱敏信息
- 启用 / 禁用某个 Key
- 修改备注
- 每 5 秒自动刷新

### 页面安全

- `/admin/ui` 本身也需要 `admin_secret`
- 不会显示完整的 NovelAI Key
- 仅展示 `masked_key`

---

## 6. 安全建议

- 管理员密钥不要与普通调用密钥相同
- 不要在前端显示完整 NAI Key
- 备注中不要写完整真实密钥
- 建议反向代理层限制 `/admin/*` 的来源 IP
- 生产环境必须启用 HTTPS

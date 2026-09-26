# BestNAI 一站式 AI 接口总文档

> 本文档面向 **AI / 开发者 / 运维**，目标是提供一个单文件、可直接被 AI 阅读和理解的完整文档。
> 包含：
>
> - 对外公开接口（给用户调用）
> - 原生 BestNAI 接口
> - NewAPI / One API 接入方式
> - Key 管理机制
> - Key 管理前端与 API
> - 计费规则
> - 错误处理规范
> - 请求构造方法

---

# 1. 服务架构

BestNAI 提供三类能力：

1. **对外公开图片生成接口**
   - 通过 OpenAI Chat Completions 兼容端点暴露
   - 推荐所有用户只使用这一种

2. **原生 BestNAI 接口**
   - 更完整
   - 支持高层 / 低层 / 流式 / BYOK / 上传图片 / 估算
   - 通常给内部开发或高级集成使用

3. **管理员接口**
   - 管理 NovelAI API Key
   - 查看状态
   - 禁用 / 启用
   - 添加备注

---

# 2. 对外推荐接口（唯一推荐给用户）

## 端点

```http
POST /v1/chat/completions
```

## 鉴权

```http
Authorization: Bearer <client_secret>
```

## 说明

这是一个 **OpenAI Chat Completions 兼容接口**，但实际内容是 **NovelAI 图片生成请求**。

做法是：

- 外层是 OpenAI Chat Completions 请求结构
- `messages[0].content` 里面放一个 **JSON 字符串**
- 这个 JSON 字符串里就是 BestNAI / NovelAI 风格参数

## 请求格式

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "{\"prompt\":\"1girl, masterpiece\",\"size\":[832,1216],\"steps\":23}"
    }
  ]
}
```

## 核心规则

- `messages` 必须存在
- 至少有一个 `role=user`
- `content` 必须是文本，推荐为 JSON 字符串
- 内层 payload 若未提供 `model`，会继承外层 `model`
- `stream=true` 返回 OpenAI 风格 SSE
- `stream=false` 返回标准 chat completion JSON

---

# 3. 对外模型名规则

## 免费模型（推荐给用户）

这些模型名带 `-anlas-0` 后缀，表示 **最多只允许 0 点数请求**：

```text
nai-diffusion-4-5-full-anlas-0
nai-diffusion-4-5-curated-anlas-0
nai-diffusion-4-full-anlas-0
nai-diffusion-4-curated-anlas-0
nai-diffusion-3-anlas-0
nai-diffusion-3-furry-anlas-0
```

## 非免费模型

基础模型名（不带 `-anlas-0`）属于非免费模型：

```text
nai-diffusion-4-5-full
nai-diffusion-4-5-curated
nai-diffusion-4-full
nai-diffusion-4-curated
nai-diffusion-3
nai-diffusion-3-furry
```

是否允许由服务端默认点数限制决定。

---

# 4. 内层生成参数（最重要）

`messages[0].content` 中的 JSON 字符串支持以下参数：

## 必填

| 字段 | 类型 | 说明 |
|------|------|------|
| `prompt` | string | 正向提示词 |

## 常用

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | string | 继承外层 | 模型名 |
| `size` | [int,int] / string | [832,1216] | 分辨率 |
| `steps` | int | 23 | 步数 |
| `scale` | float | 5.0 | CFG |
| `sampler` | string | k_euler_ancestral | 采样器 |
| `n_samples` | int | 1 | 生成数量 |
| `negative_prompt` | string | — | 负面提示词 |
| `seed` | int | 随机 | 随机种子 |
| `quality` | bool | true | 质量标签 |
| `uc_preset` | string | light | 负面预设 |
| `noise_schedule` | string | karras | 噪声调度 |
| `cfg_rescale` | float | 0 | CFG 缩放 |
| `variety_boost` | bool | false | 多样性增强 |
| `image_format` | string | png | png / webp |

## size 的三种写法

### 1. 数组（推荐）
```json
"size": [832, 1216]
```

### 2. 字符串尺寸
```json
"size": "832x1216"
```

### 3. 预设名
```json
"size": "portrait"
```

支持的预设名：
- `portrait`
- `landscape`
- `square`
- `large_portrait`
- `large_landscape`

---

# 5. 支持的固定分辨率

## 免费可用分辨率（适用于 `-anlas-0` 模型）

| 分辨率 | 免费 |
|--------|:----:|
| 832×1216 | ✅ |
| 1216×832 | ✅ |
| 1024×1024 | ✅ |
| 512×768 | ✅ |
| 768×512 | ✅ |
| 640×640 | ✅ |

## 非免费分辨率

| 分辨率 | 免费 |
|--------|:----:|
| 1024×1536 | ❌ |
| 1536×1024 | ❌ |
| 1472×1472 | ❌ |
| 1088×1920 | ❌ |
| 1920×1088 | ❌ |

---

# 6. 免费规则

如果平台默认设置为：

```env
BESTNAI_DEFAULT_MAX_ANLAS=0
```

那么只允许 **0 点数请求**。

## 0 点数条件

必须同时满足：

- 图片面积 ≤ 1,048,576 像素
- steps ≤ 28

满足时第一张免费。

---

# 7. 基础点数公式

对于非免费请求，基础估算公式为：

```text
点数 = ceil(宽 × 高 × (步数 + 5.5) / 1,700,000)
```

---

# 8. 返回格式

## 非流式

```json
{
  "id": "bestnai-1712345678",
  "object": "chat.completion",
  "created": 1712345678,
  "model": "nai-diffusion-4-5-full",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "![image_0](data:image/png;base64,iVBOR...)"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

## 流式（SSE）

依次返回：
1. role chunk
2. content chunk
3. finish chunk
4. `[DONE]`

---

# 9. 图片提取方式

图片被包装在 `choices[0].message.content` 中，格式为：

```text
![image_0](data:image/png;base64,....)
```

如果多图，会连续返回多个 markdown image。

---

# 10. 错误返回规范

所有公开接口错误都尽量兼容 OpenAI 风格：

```json
{
  "error": {
    "message": "具体错误信息",
    "type": "invalid_request_error",
    "param": "",
    "code": "REQUEST_VALIDATION_ERROR"
  }
}
```

## 常见错误码

| HTTP | type | code | 说明 |
|------|------|------|------|
| 400 | invalid_request_error | REQUEST_VALIDATION_ERROR | 参数校验失败 |
| 400 | invalid_request_error | — | 缺 messages / model / prompt |
| 401 | — | AUTH_REQUIRED | 缺失或错误鉴权 |
| 402 | billing_error | ANLAS_EXCEEDED | 超出点数限制 |
| 429 | upstream_error | — | 上游限流 |
| 502 | upstream_error | — | 上游出错 |
| 502 | upstream_error | NO_IMAGE | 没收到最终图 |
| 503 | server_error | KEY_BUSY | 所有 NAI Key 都忙 |

---

# 11. 中文 Prompt 限制

为了避免 NovelAI 中文提示词效果差和混乱，本服务默认**禁止中文出现在提示词中**。

会检查的字段：

- `prompt`
- `negative_prompt`
- `characters[].prompt`
- `characters[].negative_prompt`

若检测到中文，返回 400：

```json
{
  "error": {
    "message": "Chinese characters are not allowed in 'prompt'. Please use English.",
    "type": "invalid_request_error",
    "param": "",
    "code": "REQUEST_VALIDATION_ERROR"
  }
}
```

---

# 12. 原生 BestNAI 接口（内部/高级接入）

## 高层接口

```http
POST /v1/generate/high-level
POST /v1/generate/high-level-stream
POST /v1/estimate/high-level
```

## 低层接口

```http
POST /v1/generate/low-level
POST /v1/generate/low-level-stream
POST /v1/estimate/low-level
```

## NAI 原生兼容接口

```http
POST /ai/generate-image
POST /ai/generate-image-stream
POST /ai/estimate-image
```

## BYOK 接口

```http
POST /v1/generate/byok
POST /v1/generate/byok-stream
POST /v1/estimate/byok
```

BYOK 额外请求头：

```http
X-NAI-Key: pst-...
```

---

# 13. 图片上传与复用

## 上传接口

```http
POST /v1/upload/image
Content-Type: multipart/form-data
```

返回：

```json
{
  "upload_id": "6e35e4c2d632339a",
  "sha256": "...",
  "width": 1024,
  "height": 1024,
  "size_bytes": 12345,
  "mime_type": "image/png"
}
```

## 复用格式

```text
upload_id:<id>
```

可用于：

- `i2i.image`
- `inpaint.image`
- `inpaint.mask`
- `character_references[].image`
- `controlnet.images[].image`

---

# 14. Key 管理机制（管理员）

BestNAI 现在支持完整的 NovelAI API Key 管理。

## Key 管理目标

管理员可以：

- 查看当前所有 NAI API Key
- 查看每个 Key 的状态（忙碌/空闲/禁用）
- 随时禁用某个 Key
- 随时重新启用某个 Key
- 给每个 Key 添加备注
- 查看系统整体 Key 统计信息

## 状态定义

| 状态 | 含义 |
|------|------|
| `enabled=true, busy=false` | 可用空闲 |
| `enabled=true, busy=true` | 正在使用 |
| `enabled=false` | 已禁用，不会再参与调度 |

## Key 标识

系统不会直接暴露完整 NovelAI Key。
每个 Key 会提供：

- `key_id`：稳定标识（由哈希生成）
- `masked_key`：脱敏展示（如 `pst-abc...xyz123`）
- `note`：管理员备注

---

# 15. Key 管理 API

## 15.1 查看系统状态

```http
GET /admin/status
Authorization: Bearer <admin_secret>
```

返回：

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

## 15.2 查看所有 Key

```http
GET /admin/keys
Authorization: Bearer <admin_secret>
```

返回：

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

## 15.3 更新 Key 状态或备注

```http
PATCH /admin/keys/{key_id}
Authorization: Bearer <admin_secret>
Content-Type: application/json
```

### 禁用某个 Key

```json
{
  "enabled": false
}
```

### 启用某个 Key

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

### 同时更新

```json
{
  "enabled": true,
  "note": "恢复正常使用"
}
```

返回：

```json
{
  "key": {
    "key_id": "2f6c9a1d3e10",
    "index": 0,
    "masked_key": "pst-snE...qofD",
    "enabled": true,
    "busy": false,
    "note": "恢复正常使用"
  }
}
```

---

# 16. Key 管理前端（建议）

建议提供一个简单的管理员前端页面，至少包含：

## 页面功能

1. **系统概览卡片**
   - 总 Key 数
   - 空闲 Key 数
   - 忙碌 Key 数
   - 禁用 Key 数
   - 排队请求数

2. **Key 列表表格**
   - index
   - key_id
   - masked_key
   - enabled
   - busy
   - note

3. **操作按钮**
   - 启用
   - 禁用
   - 修改备注
   - 刷新状态

4. **过滤功能**
   - 只看空闲
   - 只看忙碌
   - 只看禁用

## 最小实现建议

前端轮询：

```http
GET /admin/keys
```

然后对某个 key 做：

```http
PATCH /admin/keys/{key_id}
```

## 16.3 管理前端

系统已提供一个简单的 Key 管理页面：

```text
GET /admin/ui
```

访问该页面时需要管理员密钥。

页面功能：

- 显示总 Key / 空闲 / 忙碌 / 禁用 / 排队统计
- 列出所有 Key 的脱敏信息
- 启用 / 禁用某个 Key
- 编辑备注
- 自动刷新状态

---

# 17. 管理员安全建议

- `admin_secret` 必须与 `client_secret` 分开
- 管理接口不要对普通用户暴露
- 建议配合 HTTPS
- 建议在反代层限制管理员接口访问源 IP
- 备注中不要写完整真实密钥

---

# 18. 推荐文档拆分方式

如果你要给 AI 或开发者使用，建议优先阅读：

1. `docs/API_PUBLIC.md` —— 对外 API 文档（用户可直接用）
2. `docs/DEV_GUIDE.md` —— 原生接口开发文档
3. `docs/NEWAPI_GUIDE.md` —— NewAPI 接入说明
4. 本文档 —— 一站式总文档

---

# 19. AI 可直接使用的最小示例

## 非流式

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "{\"prompt\":\"1girl, standing, masterpiece\",\"size\":[832,1216],\"steps\":23,\"scale\":5,\"sampler\":\"k_euler_ancestral\",\"n_samples\":1,\"quality\":true,\"uc_preset\":\"light\",\"noise_schedule\":\"karras\",\"image_format\":\"png\"}"
    }
  ]
}
```

## 流式

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": "{\"prompt\":\"1girl, standing, masterpiece\",\"size\":[832,1216],\"steps\":23}"
    }
  ]
}
```

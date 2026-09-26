# BestNAI 接口开发指南

本文档面向希望对接 BestNAI API 的开发者。

---

## 1. 接口总览

BestNAI 提供三类接口：

| 类型 | 端点 | 说明 |
|------|------|------|
| **OpenAI 兼容** | `/v1/chat/completions` | 推荐。兼容 NewAPI / One API |
| **原生高层** | `/v1/generate/high-level` | BestNAI 原生格式 |
| **原生低层** | `/v1/generate/low-level` | NovelAI SDK 原生格式 |
| **原生流式** | `/v1/generate/high-level-stream` | SSE 流式返回 |
| **BYOK** | `/v1/generate/byok` | 用户自带 Key |
| **估算** | `/v1/estimate/high-level` | 预估 Anlas 消耗 |
| **上传** | `/v1/upload/image` | 上传图片供后续复用 |
| **健康检查** | `/health` | 检查服务状态 |
| **模型列表** | `/v1/models` | OpenAI 兼容模型列表 |

---

## 2. 认证

所有接口（除 /health）都需要认证：

```http
Authorization: Bearer <client_secret>
```

BYOK 接口额外需要：

```http
X-NAI-Key: pst-...
```

---

## 3. OpenAI Chat Completions 兼容接口

### 端点
```
POST /v1/chat/completions
```

### 这是做什么的
把 BestNAI 的原生生成参数作为 JSON 字符串，放进 Chat Completions 的 user message 里。
返回标准 OpenAI chat completion 格式，图片以 markdown data-URI 嵌入 content。

### 请求格式

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "{\"prompt\":\"1girl, masterpiece\",\"size\":[832,1216],\"steps\":23,\"scale\":5,\"sampler\":\"k_euler_ancestral\",\"n_samples\":1,\"quality\":true,\"uc_preset\":\"light\",\"noise_schedule\":\"karras\",\"image_format\":\"png\"}"
    }
  ]
}
```

### 参数优先级
- 内层 JSON（content 里的）优先
- 外层 model 作为备用

### 返回格式（非流式）

```json
{
  "id": "bestnai-1712345678",
  "object": "chat.completion",
  "created": 1712345678,
  "model": "nai-diffusion-4-5-full",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "![image_0](data:image/png;base64,iVBOR...)"
    },
    "finish_reason": "stop"
  }],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

### 返回格式（流式，stream=true）

标准 OpenAI SSE chunk 格式，共 4 个事件：
1. role chunk（`delta.role = "assistant"`）
2. content chunk（`delta.content = "![image_0](...)"` 完整图片一次性发送）
3. finish chunk（`finish_reason = "stop"`）
4. `[DONE]`

### 提取图片
从 content 中提取 base64：

```python
import re
pattern = r'!\[image_\d+\]\(data:image/(\w+);base64,([A-Za-z0-9+/=]+)\)'
matches = re.findall(pattern, content)
for fmt, b64 in matches:
    image_bytes = base64.b64decode(b64)
```

```javascript
const regex = /!\[image_\d+\]\(data:image\/(\w+);base64,([A-Za-z0-9+/=]+)\)/g;
let match;
while ((match = regex.exec(content)) !== null) {
    const [, fmt, b64] = match;
    // b64 即为图片的 base64 编码
}
```

### 错误返回

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

| HTTP | type | code | 场景 |
|------|------|------|------|
| 400 | invalid_request_error | — | 缺参数/格式错 |
| 400 | invalid_request_error | REQUEST_VALIDATION_ERROR | SDK 校验失败 |
| 401 | — | AUTH_REQUIRED | 认证缺失 |
| 402 | billing_error | ANLAS_EXCEEDED | 超点数限制 |
| 502 | upstream_error | — | NovelAI 返回错误 |
| 502 | upstream_error | NO_IMAGE | 没收到图 |
| 503 | server_error | KEY_BUSY | Key 全忙 |

---

## 4. 原生高层接口

### 端点
```
POST /v1/generate/high-level
```

### 请求格式

```json
{
  "prompt": "1girl, masterpiece",
  "model": "nai-diffusion-4-5-full-anlas-0",
  "size": [832, 1216],
  "steps": 23,
  "scale": 5,
  "sampler": "k_euler_ancestral",
  "n_samples": 1,
  "quality": true,
  "uc_preset": "light",
  "noise_schedule": "karras",
  "cfg_rescale": 0,
  "variety_boost": false,
  "image_format": "png"
}
```

### 查询参数
- `return_format`：`json`（默认）/ `binary` / `zip`
- `image_format`：`png`（默认）/ `webp`

### 返回（JSON 格式）

```json
{
  "requested_model": "nai-diffusion-4-5-full-anlas-0",
  "resolved_model": "nai-diffusion-4-5-full",
  "max_anlas": 0,
  "estimated_anlas": { ... },
  "image_format": "png",
  "images": ["base64字符串..."]
}
```

---

## 5. 估算接口

### 端点
```
POST /v1/estimate/high-level
```

### 请求格式
和生成接口完全一样，只是不会实际生成图片。

### 返回

```json
{
  "requested_model": "nai-diffusion-4-5-full-anlas-0",
  "resolved_model": "nai-diffusion-4-5-full",
  "max_anlas": 0,
  "allowed": true,
  "is_opus_assumed": true,
  "estimated_anlas": {
    "total": 0,
    "per_image": 17,
    "billable_samples": 0,
    "opus_discount_applied": true,
    "base": 0,
    "character_reference": 0,
    "vibe_encoding": 0,
    "vibe_reference": 0,
    "strength_factor": 1.0,
    "requested_samples": 1
  }
}
```

---

## 6. 流式接口

### 端点
```
POST /v1/generate/high-level-stream
```

### 返回
SSE 格式，每个事件是一行 `data: {...}`：

```json
{
  "event_type": "intermediate",
  "samp_ix": 0,
  "step_ix": 5,
  "gen_id": 0,
  "sigma": 1.23,
  "image": "base64..."
}
```

最后一个事件 `event_type` 为 `"final"`。

---

## 7. 图片上传与复用

### 上传
```
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

### 复用
在后续请求中用 `upload_id:6e35e4c2d632339a` 替代 base64 字符串。

适用字段：
- `i2i.image`
- `inpaint.image`
- `inpaint.mask`
- `character_references[].image`
- `controlnet.images[].image`

---

## 8. 生成参数完整表

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `prompt` | string | （必填） | 正向提示词 |
| `model` | string | （必填） | 模型名 |
| `size` | [int, int] | [832, 1216] | 分辨率 |
| `steps` | int | 23 | 步数 |
| `scale` | float | 5.0 | CFG Scale |
| `sampler` | string | k_euler_ancestral | 采样器 |
| `n_samples` | int | 1 | 生成数量 |
| `quality` | bool | true | 质量标签 |
| `uc_preset` | string | light | 负面预设 |
| `noise_schedule` | string | karras | 噪声调度 |
| `negative_prompt` | string | — | 负面提示词 |
| `seed` | int | 随机 | 随机种子 |
| `cfg_rescale` | float | 0 | CFG 缩放 |
| `variety_boost` | bool | false | 多样性增强 |
| `image_format` | string | png | 输出格式 |

### 图生图参数

```json
{
  "i2i": {
    "image": "base64或upload_id:xxx",
    "strength": 0.7,
    "noise": 0.0
  }
}
```

### 局部重绘参数

```json
{
  "inpaint": {
    "image": "base64或upload_id:xxx",
    "mask": "base64或upload_id:xxx",
    "strength": 1.0
  }
}
```

### 角色参考

```json
{
  "character_references": [
    {
      "image": "base64或upload_id:xxx",
      "type": "character&style",
      "fidelity": 1.0,
      "strength": 1.0
    }
  ]
}
```

### Vibe Transfer

```json
{
  "controlnet": {
    "images": [
      {
        "image": "base64或upload_id:xxx",
        "info_extracted": 0.7,
        "strength": 0.6
      }
    ]
  }
}
```

### 多角色定位

```json
{
  "characters": [
    {
      "prompt": "角色描述",
      "negative_prompt": "",
      "position": "C3",
      "enabled": true
    }
  ]
}
```

位置使用 A1-E5 的 5×5 网格预设。

---

## 9. 安全模型别名

模型名格式：`模型基础名-anlas-N`

- `N` = 最大允许 Anlas 点数
- `N = 0`：只允许免费请求
- 别名只能**降低**限制，不能提高到平台默认值以上

例：
- `nai-diffusion-4-5-full-anlas-0` → 只允许免费
- `nai-diffusion-4-5-full-anlas-50` → 最多 50 点（但如果平台设了 0，则仍为 0）
- `nai-diffusion-4-5-full` → 使用平台默认限制

---

## 10. 代码示例

### Python

```python
import requests, json, base64, re

BASE = "http://你的服务器:8010"
SECRET = "你的密钥"

# 方式 1：原生接口
resp = requests.post(
    f"{BASE}/v1/generate/high-level?return_format=json",
    headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
    json={
        "prompt": "1girl, masterpiece",
        "model": "nai-diffusion-4-5-full-anlas-0",
        "size": [832, 1216],
        "steps": 23,
    },
    timeout=120,
)
data = resp.json()
for i, b64 in enumerate(data["images"]):
    with open(f"image_{i}.png", "wb") as f:
        f.write(base64.b64decode(b64))

# 方式 2：Chat Completions 兼容（适用于 NewAPI）
payload = json.dumps({
    "prompt": "1girl, masterpiece",
    "size": [832, 1216],
    "steps": 23,
})
resp2 = requests.post(
    f"{BASE}/v1/chat/completions",
    headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"},
    json={
        "model": "nai-diffusion-4-5-full-anlas-0",
        "stream": False,
        "messages": [{"role": "user", "content": payload}],
    },
    timeout=120,
)
content = resp2.json()["choices"][0]["message"]["content"]
for fmt, b64 in re.findall(r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)', content):
    with open(f"chat_image.{fmt}", "wb") as f:
        f.write(base64.b64decode(b64))
```

### JavaScript (Node.js)

```javascript
const payload = JSON.stringify({
  prompt: "1girl, masterpiece",
  size: [832, 1216],
  steps: 23,
});

const resp = await fetch("http://你的服务器:8010/v1/chat/completions", {
  method: "POST",
  headers: {
    Authorization: "Bearer 你的密钥",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "nai-diffusion-4-5-full-anlas-0",
    stream: false,
    messages: [{ role: "user", content: payload }],
  }),
});

const data = await resp.json();
const content = data.choices[0].message.content;
// 提取 base64 图片
const match = content.match(/data:image\/(\w+);base64,([A-Za-z0-9+/=]+)/);
if (match) {
  const [, fmt, b64] = match;
  // 使用 b64...
}
```

---

## 11. 超时建议

| 场景 | 推荐超时 |
|------|----------|
| 估算 | 5s |
| 小图生成 | 30s |
| 大图生成 | 60s |
| 流式生成 | 120s |
| 上传 | 30s |

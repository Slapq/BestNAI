# BestNAI NewAPI 接入指南

## 概述

BestNAI 通过 `/v1/chat/completions` 端点提供 OpenAI Chat Completions 兼容接口。
这使得 BestNAI 可以作为 NewAPI / One API 等中转平台的上游渠道使用。

核心思路：**把 NovelAI 原生生成参数作为 JSON 字符串，塞进 Chat Completions 的用户消息里。**

---

## 1. 为什么用 Chat Completions 而不是 Images

NewAPI 对 `/v1/images/generations` 的请求体校验非常严格，会强制按 OpenAI 图片接口的 schema 解析。
BestNAI 的原生参数（如 `size: [832, 1216]`、`quality: true` 等）与 OpenAI 图片接口的字段类型不兼容。

而 `/v1/chat/completions` 对消息内容没有类型限制，可以原样透传任意 JSON 字符串。

---

## 2. NewAPI 渠道配置

| 配置项 | 值 |
|--------|-----|
| 渠道类型 | OpenAI |
| Base URL | `http://你的服务器:8010` |
| API Key | 你在 `.env` 中设置的 `BESTNAI_CLIENT_SECRETS` |
| 模型 | `nai-diffusion-4-5-full-anlas-0`（或其他支持的模型名） |

### 支持的模型名

```
nai-diffusion-4-5-full-anlas-0
nai-diffusion-4-5-curated-anlas-0
nai-diffusion-4-full-anlas-0
nai-diffusion-4-curated-anlas-0
nai-diffusion-3-anlas-0
nai-diffusion-3-furry-anlas-0
```

> `-anlas-0` 后缀表示只允许 Opus 免费请求（0 点数上限）。

---

## 3. 请求格式

### 非流式请求

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

### 流式请求

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": "{\"prompt\":\"1girl, standing, masterpiece\",\"size\":[832,1216],\"steps\":23,\"scale\":5,\"sampler\":\"k_euler_ancestral\",\"n_samples\":1,\"quality\":true,\"uc_preset\":\"light\",\"noise_schedule\":\"karras\",\"image_format\":\"png\"}"
    }
  ]
}
```

### 关键说明

- `messages[0].content` 是一个 **JSON 字符串**，里面是 BestNAI 原生参数
- 外层的 `model` 会作为备用模型名（如果内层没指定的话）
- 内层参数支持 BestNAI 的所有原生功能

---

## 4. 内层参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | ✅ | 正向提示词 |
| `model` | string | 否 | 模型名（不填则用外层的） |
| `size` | [int, int] | 否 | 分辨率，如 `[832, 1216]`（默认 portrait） |
| `steps` | int | 否 | 步数（默认 23） |
| `scale` | float | 否 | CFG Scale（默认 5） |
| `sampler` | string | 否 | 采样器（默认 `k_euler_ancestral`） |
| `n_samples` | int | 否 | 生成数量（默认 1） |
| `quality` | bool | 否 | 质量标签（默认 true） |
| `uc_preset` | string | 否 | 负面预设（默认 `light`） |
| `noise_schedule` | string | 否 | 噪声调度（默认 `karras`） |
| `negative_prompt` | string | 否 | 负面提示词 |
| `seed` | int | 否 | 随机种子 |
| `cfg_rescale` | float | 否 | CFG 缩放 |
| `variety_boost` | bool | 否 | 多样性增强 |
| `image_format` | string | 否 | 输出格式（`png` 或 `webp`） |

### 高级参数（可选）

| 参数 | 类型 | 说明 |
|------|------|------|
| `i2i` | object | 图生图参数 |
| `inpaint` | object | 局部重绘参数 |
| `character_references` | array | 角色参考图 |
| `controlnet` | object | Vibe Transfer |
| `characters` | array | 多角色定位 |

---

## 5. 返回格式

### 非流式返回

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

### 流式返回

标准 OpenAI SSE 格式：

```
data: {"id":"bestnai-xxx","object":"chat.completion.chunk","created":xxx,"model":"...","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"bestnai-xxx","object":"chat.completion.chunk","created":xxx,"model":"...","choices":[{"index":0,"delta":{"content":"![image_0](data:image/png;base64,...)"},"finish_reason":null}]}

data: {"id":"bestnai-xxx","object":"chat.completion.chunk","created":xxx,"model":"...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### 图片在哪

图片以 **markdown data-URI** 格式嵌入在 `content` 字段中：

```
![image_0](data:image/png;base64,iVBOR...)
```

客户端可以直接渲染 markdown，或者用正则提取 base64 数据。

---

## 6. 错误返回

所有错误都返回 OpenAI 风格的错误信封：

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

### 常见错误

| HTTP 状态码 | type | code | 说明 |
|-------------|------|------|------|
| 400 | invalid_request_error | REQUEST_VALIDATION_ERROR | 参数校验失败 |
| 400 | invalid_request_error | — | 缺少 model 或 prompt |
| 401 | — | AUTH_REQUIRED | 缺少认证 |
| 402 | billing_error | ANLAS_EXCEEDED | 超出点数限制 |
| 429 | upstream_error | — | 上游限流 |
| 502 | upstream_error | — | 上游返回错误 |
| 502 | upstream_error | NO_IMAGE | 上游没有返回图片 |
| 503 | server_error | KEY_BUSY | 所有 Key 都在忙 |

---

## 7. 免费请求条件

当 `BESTNAI_DEFAULT_MAX_ANLAS=0` 时，只允许 Opus 免费请求：

- 图片面积 ≤ 1,048,576 像素
- steps ≤ 28

### 满足条件的分辨率

| 分辨率 | 面积 | 免费 |
|--------|------|------|
| 832×1216 | 1,011,712 | ✅ |
| 1216×832 | 1,011,712 | ✅ |
| 1024×1024 | 1,048,576 | ✅ |
| 512×768 | 393,216 | ✅ |
| 768×512 | 393,216 | ✅ |
| 640×640 | 409,600 | ✅ |
| 1024×1536 | 1,572,864 | ❌ |
| 1536×1024 | 1,572,864 | ❌ |
| 1472×1472 | 2,166,784 | ❌ |
| 1088×1920 | 2,088,960 | ❌ |
| 1920×1088 | 2,088,960 | ❌ |

---

## 8. curl 测试示例

### 直连 BestNAI

```bash
curl -X POST "http://你的服务器:8010/v1/chat/completions" \
  -H "Authorization: Bearer 你的密钥" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nai-diffusion-4-5-full-anlas-0",
    "stream": false,
    "messages": [{
      "role": "user",
      "content": "{\"prompt\":\"1girl, standing, masterpiece\",\"size\":[832,1216],\"steps\":23,\"scale\":5,\"sampler\":\"k_euler_ancestral\",\"n_samples\":1,\"quality\":true,\"uc_preset\":\"light\",\"noise_schedule\":\"karras\",\"image_format\":\"png\"}"
    }]
  }'
```

### 经 NewAPI 转发

```bash
curl -X POST "https://你的NewAPI域名/v1/chat/completions" \
  -H "Authorization: Bearer 你的NewAPI密钥" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nai-diffusion-4-5-full-anlas-0",
    "stream": false,
    "messages": [{
      "role": "user",
      "content": "{\"prompt\":\"1girl, standing, masterpiece\",\"size\":[832,1216],\"steps\":23,\"scale\":5,\"sampler\":\"k_euler_ancestral\",\"n_samples\":1,\"quality\":true,\"uc_preset\":\"light\",\"noise_schedule\":\"karras\",\"image_format\":\"png\"}"
    }]
  }'
```

---

## 9. 注意事项

1. **content 必须是 JSON 字符串**：不是 JSON 对象，而是字符串化的 JSON
2. **model 的 `-anlas-0` 后缀**：控制点数上限，不能通过别名提高到平台默认值以上
3. **stream 参数**：NewAPI 可能默认开启 stream，建议显式设置 `"stream": false`
4. **超时**：生成一张图通常需要 8-15 秒，请确保客户端和 NewAPI 的超时设置足够长
5. **返回大小**：一张 PNG 图片的 base64 约 2-3MB，注意 NewAPI 的响应大小限制

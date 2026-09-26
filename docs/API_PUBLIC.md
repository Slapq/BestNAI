# BestNAI 图片生成 API 使用文档

## 概述

本服务提供 NovelAI 图片生成能力，通过标准 OpenAI Chat Completions 接口调用。

---

## 1. 接入信息

| 项目 | 值 |
|------|-----|
| API 地址 | `https://你的域名/v1/chat/completions` |
| 认证方式 | `Authorization: Bearer <你的API密钥>` |
| 请求方法 | POST |
| Content-Type | application/json |

---

## 2. 请求格式

```json
{
  "model": "模型名",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "生成参数JSON字符串"
    }
  ]
}
```

### 关键要点

**`messages[0].content` 是一个 JSON 字符串**，不是普通文本。
你需要先构造生成参数对象，然后用 `JSON.stringify()` 转成字符串放进去。

### content 中支持的 size 写法

在 `content` 里的 JSON 中，`size` 支持三种写法：

1. 数组（推荐）
```json
"size": [832, 1216]
```

2. 字符串尺寸
```json
"size": "832x1216"
```

3. 预设名
```json
"size": "portrait"
```

推荐优先使用数组形式，最直观、最稳定。

---

## 3. 可用模型

### 免费模型（0 点数模型）

对外推荐只使用以下**免费模型**：

| 模型名 | 说明 |
|--------|------|
| `nai-diffusion-4-5-full-anlas-0` | V4.5 完整版（推荐） |
| `nai-diffusion-4-5-curated-anlas-0` | V4.5 精选版 |
| `nai-diffusion-4-full-anlas-0` | V4 完整版 |
| `nai-diffusion-4-curated-anlas-0` | V4 精选版 |
| `nai-diffusion-3-anlas-0` | V3 |
| `nai-diffusion-3-furry-anlas-0` | V3 Furry |

### 说明
- 带 `-anlas-0` 后缀的是**免费模型**（0 点数上限）
- 这些模型仍然必须满足免费规则（如分辨率和步数限制）
- 不带 `-anlas-0` 后缀的基础模型属于**非免费模型**，不属于本文档默认对外范围

## 4. 生成参数详解

以下参数需要构造成 JSON 对象，再转为字符串放入 `content`。

### 4.1 必填参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `prompt` | string | 正向提示词 |

### 4.2 常用参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `size` | [int, int] | [832, 1216] | 分辨率 |
| `steps` | int | 23 | 采样步数 |
| `scale` | float | 5.0 | CFG Scale（提示词引导强度） |
| `sampler` | string | `k_euler_ancestral` | 采样器 |
| `n_samples` | int | 1 | 生成数量（1-8） |
| `seed` | int | 随机 | 随机种子（固定种子可复现） |
| `negative_prompt` | string | — | 负面提示词 |

### 4.3 质量控制参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `quality` | bool | true | 自动添加质量标签 |
| `uc_preset` | string | `light` | 负面预设（`light` / `strong` / `none`） |
| `noise_schedule` | string | `karras` | 噪声调度 |
| `cfg_rescale` | float | 0 | CFG 缩放（0-1） |
| `variety_boost` | bool | false | 多样性增强 |

### 4.4 输出参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `image_format` | string | `png` | 输出格式（`png` 或 `webp`） |

---

## 5. 可用分辨率

| 分辨率 | 适用场景 | 免费可用 |
|--------|----------|:--------:|
| 832×1216 | 竖图（推荐） | ✅（使用免费模型时） |
| 1216×832 | 横图（推荐） | ✅（使用免费模型时） |
| 1024×1024 | 方图 | ✅（使用免费模型时） |
| 512×768 | 小竖图 | ✅（使用免费模型时） |
| 768×512 | 小横图 | ✅（使用免费模型时） |
| 640×640 | 小方图 | ✅（使用免费模型时） |
| 1024×1536 | 大竖图 | ❌（免费模型不可用） |
| 1536×1024 | 大横图 | ❌（免费模型不可用） |
| 1472×1472 | 大方图 | ❌（免费模型不可用） |
| 1088×1920 | 超大竖图 | ❌（免费模型不可用） |
| 1920×1088 | 超大横图 | ❌（免费模型不可用） |

### 免费条件
- 图片面积 ≤ 1,048,576 像素
- steps ≤ 28
- 同时满足则第一张免费

---

## 6. 请求构造示例

### 6.1 最简请求

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [{
    "role": "user",
    "content": "{\"prompt\":\"1girl, standing, masterpiece\"}"
  }]
}
```

### 6.2 指定分辨率和步数

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [{
    "role": "user",
    "content": "{\"prompt\":\"1girl, standing, masterpiece, best quality\",\"size\":[1024,1024],\"steps\":28,\"scale\":5}"
  }]
}
```

### 6.3 完整参数请求

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [{
    "role": "user",
    "content": "{\"prompt\":\"1girl, standing in garden, masterpiece, best quality\",\"negative_prompt\":\"lowres, bad anatomy\",\"size\":[832,1216],\"steps\":23,\"scale\":5,\"sampler\":\"k_euler_ancestral\",\"n_samples\":1,\"quality\":true,\"uc_preset\":\"light\",\"noise_schedule\":\"karras\",\"cfg_rescale\":0,\"variety_boost\":false,\"seed\":12345,\"image_format\":\"png\"}"
  }]
}
```

### 6.4 流式请求

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": true,
  "messages": [{
    "role": "user",
    "content": "{\"prompt\":\"1girl, masterpiece\",\"size\":[832,1216],\"steps\":23}"
  }]
}
```

---

## 7. 返回格式

### 7.1 非流式返回

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
      "content": "![image_0](data:image/png;base64,iVBORw0KGgo...)"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  }
}
```

### 7.2 流式返回

标准 SSE 格式，依次发送：

```
data: {"id":"bestnai-xxx","object":"chat.completion.chunk","created":xxx,"model":"...","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"bestnai-xxx","object":"chat.completion.chunk","created":xxx,"model":"...","choices":[{"index":0,"delta":{"content":"![image_0](data:image/png;base64,...)"},"finish_reason":null}]}

data: {"id":"bestnai-xxx","object":"chat.completion.chunk","created":xxx,"model":"...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

### 7.3 图片在哪里

图片以 **markdown data-URI** 格式返回在 `content` 中：

```
![image_0](data:image/png;base64,iVBORw0KGgo...)
```

如果生成了多张图片（n_samples > 1），会有多个：

```
![image_0](data:image/png;base64,...)
![image_1](data:image/png;base64,...)
```

---

## 8. 如何提取图片

### Python

```python
import requests, json, base64, re

API_URL = "https://你的域名/v1/chat/completions"
API_KEY = "你的API密钥"

# 构造生成参数
params = {
    "prompt": "1girl, standing, masterpiece, best quality",
    "size": [832, 1216],
    "steps": 23,
    "scale": 5,
    "sampler": "k_euler_ancestral",
    "quality": True,
    "uc_preset": "light",
    "noise_schedule": "karras",
    "image_format": "png"
}

# 发送请求
response = requests.post(
    API_URL,
    headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    },
    json={
        "model": "nai-diffusion-4-5-full-anlas-0",
        "stream": False,
        "messages": [{"role": "user", "content": json.dumps(params)}]
    },
    timeout=120
)

# 提取图片
data = response.json()
content = data["choices"][0]["message"]["content"]

# 用正则提取所有图片
images = re.findall(r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)', content)
for i, (fmt, b64) in enumerate(images):
    with open(f"output_{i}.{fmt}", "wb") as f:
        f.write(base64.b64decode(b64))
    print(f"保存: output_{i}.{fmt}")
```

### JavaScript (Node.js)

```javascript
const fetch = require("node-fetch");

const API_URL = "https://你的域名/v1/chat/completions";
const API_KEY = "你的API密钥";

const params = {
  prompt: "1girl, standing, masterpiece",
  size: [832, 1216],
  steps: 23,
  scale: 5,
};

async function generate() {
  const resp = await fetch(API_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "nai-diffusion-4-5-full-anlas-0",
      stream: false,
      messages: [{ role: "user", content: JSON.stringify(params) }],
    }),
  });

  const data = await resp.json();
  const content = data.choices[0].message.content;

  // 提取 base64
  const regex = /data:image\/(\w+);base64,([A-Za-z0-9+/=]+)/g;
  let match;
  while ((match = regex.exec(content)) !== null) {
    const [, fmt, b64] = match;
    const buf = Buffer.from(b64, "base64");
    require("fs").writeFileSync(`output.${fmt}`, buf);
    console.log(`保存: output.${fmt}`);
  }
}

generate();
```

### curl

```bash
curl -s -X POST "https://你的域名/v1/chat/completions" \
  -H "Authorization: Bearer 你的API密钥" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nai-diffusion-4-5-full-anlas-0",
    "stream": false,
    "messages": [{
      "role": "user",
      "content": "{\"prompt\":\"1girl, masterpiece\",\"size\":[832,1216],\"steps\":23}"
    }]
  }' | python3 -c "
import sys, json, base64, re
data = json.load(sys.stdin)
content = data['choices'][0]['message']['content']
for i, (fmt, b64) in enumerate(re.findall(r'data:image/(\w+);base64,([A-Za-z0-9+/=]+)', content)):
    with open(f'output_{i}.{fmt}', 'wb') as f:
        f.write(base64.b64decode(b64))
    print(f'保存: output_{i}.{fmt}')
"
```

---

## 9. 错误处理

### 错误返回格式

```json
{
  "error": {
    "message": "具体错误信息",
    "type": "错误类型",
    "param": "",
    "code": "错误代码"
  }
}
```

### 错误码速查表

| HTTP 状态码 | 含义 | 处理建议 |
|-------------|------|----------|
| 400 | 请求参数错误 | 检查 JSON 格式和参数值 |
| 401 | 认证失败 | 检查 API Key 是否正确 |
| 402 | 超出点数限制 | 换用免费分辨率或降低步数 |
| 429 | 请求过于频繁 | 等待几秒后重试 |
| 502 | 上游服务错误 | 稍后重试 |
| 503 | 服务器忙 | 等待几秒后重试 |

### 常见错误及解决

**"Anlas limit exceeded"（402）**
- 你的请求超出了免费额度
- 解决：使用免费分辨率（如 832×1216）并确保 steps ≤ 28

**"No user message found"（400）**
- messages 格式不对
- 解决：确保 messages 数组包含 `{"role": "user", "content": "..."}`

**"Payload validation failed"（400）**
- 生成参数有误
- 解决：检查 content 里的 JSON 格式和参数值

**"All NovelAI keys are busy"（503）**
- 服务器正在处理其他请求
- 解决：等待 3-5 秒后重试

---

## 10. 计费规则

### 基础公式

```
点数 = ceil(宽 × 高 × (步数 + 5.5) / 1,700,000)
```

### 免费规则

当请求满足以下所有条件时，第一张图片免费：
1. 图片面积 ≤ 1,048,576 像素
2. 步数 ≤ 28

### 常用免费组合

| 分辨率 | 最大免费步数 | 每张估价（超出时） |
|--------|:----------:|:------------------:|
| 832×1216 | 28 | 17 点 |
| 1024×1024 | 28 | 18 点 |
| 512×768 | 28 | 7 点 |
| 640×640 | 28 | 7 点 |

---

## 11. 最佳实践

### 推荐默认参数

```json
{
  "prompt": "你的提示词",
  "size": [832, 1216],
  "steps": 23,
  "scale": 5,
  "sampler": "k_euler_ancestral",
  "quality": true,
  "uc_preset": "light",
  "noise_schedule": "karras",
  "image_format": "png"
}
```

### 性能建议

1. **超时设置**：建议 ≥ 60 秒（生成通常需要 8-15 秒）
2. **stream 设为 false**：除非你需要流式处理
3. **优先用免费分辨率**：832×1216 是最佳画质/免费平衡
4. **固定 seed 调试**：调试时固定 seed 方便对比效果
5. **返回体积大**：一张 PNG 的 base64 约 2-3MB，注意网络和内存

### 提示词技巧

- 使用英文提示词效果更好
- 加上 `masterpiece, best quality` 可提升整体质量
- 用逗号分隔不同描述
- 负面提示词可以排除不想要的元素
- 默认启用 `quality: true` 会自动添加质量标签

---

## 12. 限制说明

| 限制项 | 值 |
|--------|-----|
| 最大步数 | 50 |
| 免费最大步数 | 28 |
| 最大分辨率 | 1920×1088 |
| 最小分辨率 | 64×64 |
| 分辨率步进 | 64 的倍数 |
| 最大生成数量 | 8 |
| CFG Scale 范围 | 0-10 |
| 单次请求超时建议 | 60-120 秒 |

# BestNAI 用户指南

## 你能用 BestNAI 做什么

BestNAI 是一个 NovelAI 图片生成服务，提供：

- **网页端**：打开浏览器直接生成图片
- **API 接口**：通过程序调用生成图片
- **NewAPI 接入**：作为 NewAPI / One API 的上游渠道

---

## 1. 网页端使用

### 打开页面
```
http://你的服务器:8010/
```

首次打开会要求输入密码（Client Secret）。

### 基本操作
1. 输入 Prompt（正向提示词）
2. 选择模型（推荐 `nai-diffusion-4-5-full`）
3. 选择分辨率（推荐 832×1216 或 1024×1024）
4. 调整 Steps（步数，推荐 23）
5. 点击"估算"查看 Anlas 消耗
6. 点击"生成"

### 支持的分辨率

| 分辨率 | 免费（Opus） |
|--------|:------------:|
| 832×1216 | ✅ |
| 1216×832 | ✅ |
| 1024×1024 | ✅ |
| 512×768 | ✅ |
| 768×512 | ✅ |
| 640×640 | ✅ |
| 1024×1536 | ❌ |
| 1536×1024 | ❌ |
| 1472×1472 | ❌ |
| 1088×1920 | ❌ |
| 1920×1088 | ❌ |

> 免费条件：面积 ≤ 1,048,576 且 steps ≤ 28

### 高级功能

**图生图**：上传源图 → 调整 Strength → 生成

**局部重绘**：上传源图 → 画 mask → 生成

**参考图**：
- Character Reference：角色/风格参考
- Vibe Transfer：构图/风格参考

**多角色定位**：为每个角色设置独立 prompt 和位置

**BYOK**：使用你自己的 NovelAI Key 生成

---

## 2. 通过 NewAPI 使用

如果你的 BestNAI 已经接入了 NewAPI / One API，你可以通过标准 OpenAI 接口调用。

### 请求方式

```bash
curl -X POST "https://你的NewAPI域名/v1/chat/completions" \
  -H "Authorization: Bearer 你的NewAPI密钥" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nai-diffusion-4-5-full-anlas-0",
    "stream": false,
    "messages": [{
      "role": "user",
      "content": "{\"prompt\":\"1girl, standing, masterpiece\",\"size\":[832,1216],\"steps\":23}"
    }]
  }'
```

### 关键要点
- **免费模型名**：使用 `-anlas-0` 后缀，例如 `nai-diffusion-4-5-full-anlas-0`
- **消息内容**：JSON 字符串，里面是生成参数
- **stream**：建议设为 `false`
- **返回**：图片以 markdown data-URI 格式在 assistant 消息中

### 最简请求（只写 prompt）

```json
{
  "model": "nai-diffusion-4-5-full-anlas-0",
  "stream": false,
  "messages": [{"role": "user", "content": "{\"prompt\":\"1girl, masterpiece\"}"}]
}
```

其余参数会使用默认值（分辨率 portrait、步数 23、sampler k_euler_ancestral 等）。

---

## 3. 可用模型

### 免费模型（0 点数模型）

这些模型名带有 `-anlas-0` 后缀，表示**最多只允许 0 点数请求**：

| 模型名 | 说明 |
|--------|------|
| `nai-diffusion-4-5-full-anlas-0` | V4.5 完整版（推荐） |
| `nai-diffusion-4-5-curated-anlas-0` | V4.5 精选版 |
| `nai-diffusion-4-full-anlas-0` | V4 完整版 |
| `nai-diffusion-4-curated-anlas-0` | V4 精选版 |
| `nai-diffusion-3-anlas-0` | V3 |
| `nai-diffusion-3-furry-anlas-0` | V3 Furry |

### 非免费模型（需要点数）

如果你有对应权限，也可以使用不带 `-anlas-0` 后缀的基础模型名，例如：

- `nai-diffusion-4-5-full`
- `nai-diffusion-4-5-curated`
- `nai-diffusion-4-full`
- `nai-diffusion-4-curated`
- `nai-diffusion-3`
- `nai-diffusion-3-furry`

但是否允许，取决于你的服务端点数策略。

---

### 基础公式
```
点数 = ceil(宽 × 高 × (步数 + 5.5) / 1,700,000)
```

### 免费条件（Opus 账号）
- 面积 ≤ 1,048,576 像素
- steps ≤ 28
- 满足时第一张免费

### 当 DEFAULT_MAX_ANLAS=0 时
- 只允许免费请求
- 大分辨率或高步数会被拒绝（402 错误）

---

## 4. 常见问题

**Q: 为什么返回 402 错误？**
A: 超出了点数限制。换成免费分辨率（如 832×1216）并确保 steps ≤ 28。

**Q: 为什么返回 502 错误？**
A: NovelAI 上游出错。可能是 Key 过期或 NovelAI 服务器问题。

**Q: 为什么返回 503 错误？**
A: 所有 Key 都在忙。稍等几秒重试。

**Q: 生成很慢？**
A: 正常。NovelAI 生成一张图通常需要 8-15 秒。

**Q: 怎么获取生成的图片？**
A: 
- 网页端：直接看预览，点击下载
- API：从返回的 JSON 中提取 base64 数据
- NewAPI：从 `choices[0].message.content` 中提取 `data:image/png;base64,...`

---

## 6. 支持的模型

### 免费模型（推荐对外使用）

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
- 不带后缀的基础模型属于**非免费模型**，是否可用取决于服务端配置
- 对外文档默认只推荐使用 `-anlas-0` 免费模型

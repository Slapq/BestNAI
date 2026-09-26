# BestNAI

BestNAI 是一个面向 NovelAI 的后端 API 网关，提供 Key 池调度、图片生成、估价、兼容接口和 YesNAI Python SDK。

本项目不包含 WebUI，专注于后端服务和 SDK。

## 功能

- NovelAI 多 Key FIFO 调度与单 Key 单并发
- 文生图、图生图、局部重绘、Character Reference、Vibe / ControlNet、多角色定位
- 普通生成、流式生成、JSON / 二进制 / ZIP 响应
- Anlas 估算与可配置定价规则
- 图片上传与 `upload_id:<id>` 引用
- BYOK：使用请求方自己的 NovelAI Key
- YesNAI、NovelAI Native、Launcher、OpenAI Images / Chat 兼容接口
- 管理 API：Key 池、定价规则、站点选项
- SQLite 持久化 Key、定价规则、任务和幂等记录
- 独立的 `yesnai` Python SDK

## 快速开始

要求：Python 3.12+

```bash
pip install -r requirements.txt
copy .env.example .env
python run.py
```

服务默认运行在：

```text
http://127.0.0.1:8010
```

健康检查：

```text
GET /health
GET /ready
```

## 配置

主要环境变量：

```env
BESTNAI_API_KEYS=pst-xxx,pst-yyy
BESTNAI_CLIENT_SECRETS=client-secret
BESTNAI_ADMIN_SECRETS=admin-secret
BESTNAI_REQUIRE_AUTH=true
BESTNAI_DB_PATH=data/bestnai.db
BESTNAI_NOVELAI_IMAGE_BASE=https://image.novelai.net
BESTNAI_NOVELAI_API_BASE=https://api.novelai.net
```

完整配置见 [`.env.example`](.env.example)。

不要提交真实的 `.env` 或 NovelAI Key。

## 接口

主要接口分类：

- `/v1/generate/*`：BestNAI 生成、流式和 BYOK 接口
- `/v1/nai/*`：YesNAI 风格接口
- `/v1/images/generations`：OpenAI Images 兼容接口
- `/v1/chat/completions`：OpenAI Chat 兼容接口
- `/native/*`：NovelAI Native 风格接口
- `/ai/*`、`/user/*`：Launcher 风格接口
- `/v1/estimate*`、`/v1/pricing/public`：估价和公开定价信息
- `/v1/upload/image`：图片上传和 `upload_id` 复用
- `/admin/*`：管理 API
- `/health`、`/ready`：健康检查

完整接口说明见 [`docs/`](docs/)。

## YesNAI SDK

SDK 位于 [`sdk/`](sdk/)，使用方式：

```bash
pip install -e ./sdk
```

```python
from yesnai import YesNAI

client = YesNAI(
    api_key="ynai-your-api-key",
    base_url="http://127.0.0.1:8010",
)

result = client.image.generate(
    prompt="1girl, masterpiece",
    model="nai-diffusion-4-5-full",
)
```

SDK 说明见 [`sdk/README.md`](sdk/README.md)。

## Docker

```bash
docker compose up --build
```

## 测试

```bash
python -m compileall app run.py sdk/src
python tests/run_all.py
```

测试使用 Mock NovelAI 服务，不代表真实 NovelAI 账户、网络环境或生产部署结果。

## 说明

- 本项目不提供 NovelAI 账户、用户注册、Gems 钱包或真实余额扣款系统。
- Anlas / Gems 数值用于估价、定价和任务记录；实际上游扣费以 NovelAI 为准。
- Key 池数据库包含用于请求上游的 Key，必须保护数据库文件和管理凭据。
- `vendor/novelai-sdk` 为随项目分发的上游 SDK 代码，相关许可声明见其源码。

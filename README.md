# Ollama Relay — API 中转站

一个 Python 进程同时运行管理面板 + Ollama 兼容代理，让你可以在 VS Code Copilot / Continue 等工具中使用 DeepSeek、MiMo 等大模型。

```
┌────────────────────────────────────────────────────────┐
│  你的服务器                                              │
│                                                        │
│  浏览器 ──→ :3456 (管理面板，密码保护)                    │
│  Copilot ──→ :11434 (Ollama API，转发到真实模型)          │
│                             │                          │
│                             └──→ DeepSeek / MiMo / ... │
└────────────────────────────────────────────────────────┘
```

## 特性

- **一个进程双端口** — 3456（管理）+ 11434（Ollama 代理）
- **Ollama 兼容** — 支持 `/api/tags`、`/api/chat`、`/api/show`、`/v1/chat/completions` 等端点
- **多协议支持** — OpenAI 兼容 + Anthropic 兼容
- **安全加固** — 密码 SHA-256 哈希存储、登录限流、API Key 脱敏、输入校验
- **VS Code 兼容** — CORS 中间件、`/api/embeddings`、`/api/ps` 等端点
- **Tool Calls 支持** — 透传 `tools`/`tool_choice` 参数，流式累积并返回完整 tool_calls
- **多模态支持** — 自动将 Ollama `images` 格式转为 OpenAI `image_url` 格式
- **现代管理界面** — Vue 3 SPA、状态面板、快速连接配置生成

## 环境要求

- Python 3.10+
- 网络能访问目标 LLM API（DeepSeek / MiMo 等）

## 快速部署

```bash
git clone https://github.com/mjh66666/ollama_tranmit_station.git
cd ollama_tranmit_station
chmod +x setup.sh
./setup.sh
```

`setup.sh` 会自动：
1. 创建 Python 虚拟环境
2. 安装依赖（`uvicorn`、`httpx`、`oai2ollama`）
3. 启动服务

## 手动部署

```bash
python3 -m venv venv
source venv/bin/activate
pip install uvicorn httpx oai2ollama
python start.py
```

## 启动服务

```bash
source venv/bin/activate
python start.py
```

启动后：

| 端口 | 用途 | 地址 |
|------|------|------|
| 3456 | 管理面板 | `http://服务器IP:3456` |
| 11434 | Ollama 代理 | `http://服务器IP:11434` |

## 配置

### 1. 登录管理面板

打开 `http://服务器IP:3456`，默认密码：`admin`

> 首次启动时密码会自动哈希存储到 `password.txt`。修改密码：直接编辑 `password.txt` 写入新密码，重启服务后自动哈希。

### 2. 添加模型

通过管理面板的向导添加模型配置，支持平台：
- **DeepSeek** — `https://api.deepseek.com`（OpenAI）/ `https://api.deepseek.com/anthropic/v1`（Anthropic）
- **MiMo** — `https://token-plan-cn.xiaomimimo.com/v1`（OpenAI）/ `https://token-plan-cn.xiaomimimo.com/anthropic/v1`（Anthropic）
- **自定义** — 任何 OpenAI / Anthropic 兼容 API

### 3. 在 VS Code 中使用

**Copilot（Ollama 格式）：**
```json
{
  "github.copilot.advanced": {
    "debug.overrideProxyUrl": "http://服务器IP:11434"
  }
}
```

**Copilot（OpenAI 格式，支持自定义模型选择）：**
```json
{
  "github.copilot.chat.experimental.endpoint": "http://服务器IP:11434/v1",
  "github.copilot.chat.experimental.customModels": [
    {
      "id": "mimo-v2.5-pro",
      "name": "MiMo v2.5 Pro",
      "endpoint": "http://服务器IP:11434/v1",
      "apiKey": "any",
      "model": "mimo-v2.5-pro"
    }
  ]
}
```

> **注意**：`experimental.*` 设置可能随 VS Code / Copilot 版本变化。将 `服务器IP` 替换为实际地址。

**Continue：**
```json
{
  "continue.modelProvider": "ollama",
  "continue.model": "mimo-v2.5-pro",
  "continue.ollama.baseUrl": "http://服务器IP:11434"
}
```

### 关于视觉（图片）功能

中转站已实现 Ollama → OpenAI 图片格式自动转换（`images[]` → `image_url`），但 **VS Code Copilot 的 Ollama 集成不支持传递图片数据**，这是 Copilot 本身的限制。

如需使用视觉功能，推荐：
- **Continue 扩展** — 原生支持 OpenAI 兼容 API 的多模态请求
- **Copilot OpenAI 端点** — 使用上述 `experimental.endpoint` 配置，走 OpenAI 格式
- **直接 API 调用** — `curl` 或 Python 脚本调用 `/v1/chat/completions`

## 验证

```bash
# 服务状态
curl http://localhost:11434/api/version

# 已配置模型
curl http://localhost:11434/api/tags

# 测试对话
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mimo-v2.5-pro","messages":[{"role":"user","content":"hello"}]}'
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/version` | Ollama 版本 |
| GET | `/api/tags` | 已配置模型列表 |
| POST | `/api/show` | 模型详情 |
| POST | `/api/chat` | Ollama 格式对话（支持流式） |
| POST | `/api/generate` | Ollama 格式生成（支持流式） |
| POST | `/api/embeddings` | 向量嵌入 |
| GET | `/api/ps` | 运行中的模型 |
| GET | `/v1/models` | OpenAI 格式模型列表 |
| POST | `/v1/chat/completions` | OpenAI 格式对话（支持流式） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MANAGEMENT_PORT` | 3456 | 管理面板端口 |
| `PROXY_PORT` | 11434 | Ollama 代理端口 |
| `RELAY_PASSWORD` | admin | 初始登录密码（仅首次启动生效） |

## 文件说明

| 文件 | 说明 |
|------|------|
| `start.py` | 入口文件，同时启动管理面板和代理服务 |
| `server.py` | 管理面板后端（stdlib HTTPServer） |
| `relay.py` | Ollama 兼容代理（FastAPI + uvicorn） |
| `index.html` | 前端管理界面（Vue 3 SPA） |
| `vue.global.prod.js` | Vue 3 运行库（本地） |
| `setup.sh` | 一键部署脚本 |
| `configs.json` | API 配置数据（自动生成，已 gitignore） |
| `password.txt` | 密码哈希文件（自动生成，已 gitignore） |
| `configs.example.json` | 配置文件示例 |

## 安全说明

- 密码使用 SHA-256 哈希存储，启动时自动从明文升级
- 登录接口限流：5 次失败 / 5 分钟
- API Key 在管理面板中脱敏显示
- 管理面板 CORS 仅允许 localhost
- `configs.json` 和 `password.txt` 已在 `.gitignore` 中排除

## 许可证

MIT

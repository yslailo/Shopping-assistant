# 小购 · 购物助手智能体（Demo）

基于大语言模型 API 的执行类购物智能体最小闭环：理解自然语言需求 ->
硬/软约束分离的 Top-5 选品 -> 购物车操作 -> 订单支付提醒。

## 架构

```
ui/streamlit_app.py        Streamlit 聊天前端（购物车/订单/提醒侧栏）
app/main.py                FastAPI 入口（对话、购物车、订单、提醒接口）
app/agent/                 Function Calling 主循环、系统提示词、工具定义
app/selection/             意图解析（硬/软约束）+ Top-5 选品流水线
app/cart/                  购物车与订单状态机（金额计算、状态迁移全部确定性）
app/reminder/              APScheduler 定时任务：超时失效、支付提醒
app/llm/                   模型适配层（OpenAI 兼容接口，DeepSeek/Qwen/GLM 可切换）
app/db/                    SQLite 建表与 48 款商品种子数据
```

核心设计约定：

1. **硬约束走 SQL，软约束走模型**：预算、品类、品牌由数据库过滤，
   违反硬约束的商品不可能出现在推荐里；功能、场景、规格偏好由
   大模型逐条打分（1 / 0.5 / 0），分组加权排序，输出附带推荐理由。
2. **LLM 决策，代码执行**：金额计算、库存校验、"第 N 款"指代解析、
   订单状态迁移、提醒触发时机全部由确定性代码完成。
3. **模型可插拔**：所有大模型调用走 OpenAI 兼容接口，改环境变量即可
   在 DeepSeek / 通义千问 / 智谱之间切换。
4. **降级可用**：未配置 API Key 时意图解析与打分回退到规则/关键词，
   购物车、订单、提醒全流程不依赖模型。

## 快速开始

```bash
# 1. 安装依赖（在项目虚拟环境或用户终端中执行；
#    下载慢时可加 -i https://pypi.tuna.tsinghua.edu.cn/simple
#    或 --proxy http://127.0.0.1:10808）
pip install -r requirements.txt

# 2. 启动后端（终端 1，默认 8000 端口）
python -m uvicorn app.main:app --port 8000

# 3. 启动前端（终端 2）
python -m streamlit run ui/streamlit_app.py
```

首次启动会自动建库并写入 48 款演示商品。

## 模型设置（网页端配置，无需改代码）

打开前端后，在左侧「模型设置」面板填写三项即可使用，保存后立即生效，不需要重启服务：

| 字段 | 说明 | 示例 |
|---|---|---|
| Base URL | 任何 OpenAI 兼容接口地址 | `https://api.deepseek.com` |
| 模型名 | 服务商提供的模型标识 | `deepseek-chat` |
| API Key | 对应平台密钥 | `sk-...` |

点「测试连接」会真实发一次最小请求验证可用性；若地址未带 `/v1` 而首次请求失败，
系统会自动补 `/v1` 再试一次，并在提示中给出建议地址。

可接入 DeepSeek、通义千问（兼容模式）、智谱、vLLM / Ollama 自建服务，
以及各类 OpenAI 兼容中转站。配置保存在本地 SQLite 的 `app_settings` 表
（`source=runtime`）；`source=env` 表示当前用的是 `.env` 兜底默认值。

## 降级运行

系统对两个第三方依赖做了可选处理，缺失时核心功能不受影响：

- 未装 openai / 未配置接口：对话不可用，但意图解析与打分自动回退
  到规则与关键词匹配，购物车、订单、提醒全流程照常（见下节验证）。
- 未装 APScheduler：后台定时提醒关闭，订单状态在每次对话前惰性刷新，
  超时失效与支付提醒逻辑依然成立。

## 验证

```bash
# 状态机单元测试（7 项）
python -m pytest -q

# 离线冒烟检查（11 项，不依赖大模型）
python scripts/smoke_offline.py
```

## 演示剧本

1. "预算 500 以内，想买个降噪耳机" → Top-5 列表（价格、匹配偏好、理由）
2. "第一款加购物车" → 指代解析 + 加购，侧栏购物车更新
3. "结算" → 生成待支付订单，告知截止时间
4. 等待提醒（默认截止前 10 分钟提醒、超时自动失效；
   可在 .env 把 PAYMENT_TIMEOUT_MINUTES 调成 2 快速演示）
5. "支付订单" 或侧栏按钮 → 订单完成

## 配置说明

模型接口（Base URL / 模型名 / API Key）优先使用网页端「模型设置」保存的值，
下列环境变量只作为未在网页配置时的兜底默认值：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| LLM_BASE_URL | https://api.deepseek.com | OpenAI 兼容接口地址（兜底） |
| LLM_MODEL | deepseek-chat | 模型名（兜底） |
| LLM_API_KEY | （空） | 可选的兜底 Key，推荐直接用网页端配置 |
| SHOP_AGENT_DB | 项目根 shop_agent.db | SQLite 文件路径 |
| PAYMENT_TIMEOUT_MINUTES | 30 | 订单支付超时时间 |
| REMIND_BEFORE_MINUTES | 10 | 截止前多少分钟生成提醒 |


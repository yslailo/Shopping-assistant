# 小购 · 面向约束满足的可解释 Top-K 商品选品智能体

毕业设计项目：一个**执行类购物智能体**。用户在对话框里用自然语言描述需求（预算、
品类、偏好、使用场景），系统把它拆成**硬约束**与**软约束**，硬约束交给 SQL 精确
过滤，软约束交给大模型逐条打分，最终给出可解释的 Top-K 推荐，并支持加购、结算、
支付提醒的完整闭环。

整个系统**不训练任何模型**，只通过 OpenAI 兼容接口调用大模型；去掉大模型后，
购物车、订单、提醒等业务链路依然完整可用（见「降级运行」）。

---

## 1. 核心设计

| 设计点 | 做法 | 目的 |
|---|---|---|
| 硬约束 / 软约束分离 | 预算、品类、品牌走 SQL 精确过滤；功能、场景、规格偏好由模型打分 | 保证推荐结果**永不违反**硬约束，且每个软约束都有可追溯的满足情况 |
| 可解释性 | 每款商品给出 `score` + `matched`（满足项）+ `missed`（未满足项）+ 一句话理由 | 推荐结果可核对、可复现，而非黑箱排序 |
| LLM 决策，代码执行 | 金额计算、库存校验、"第 N 款"指代、订单状态迁移、提醒时机全部由确定性代码完成 | 模型只负责理解与排序，不负责算钱 |
| 模型可插拔 | 所有调用走 OpenAI 兼容协议，Base URL / 模型名 / Key 在**网页端运行时配置** | 换厂商不需要改代码、不需要重启 |
| 降级可用 | 未配置 Key 时意图解析回退到规则/关键词，业务链路不中断 | 答辩现场不怕断网 |

### 选品流水线

```
用户输入
  ↓  意图解析（模型 + 规则兜底）→ {品类, 预算, 品牌, 功能诉求, 场景, 规格}
  ↓  硬约束召回：SQL WHERE（品类 / 价格区间 / 品牌），走复合索引
  ↓  软约束打分：模型对每件候选逐条判 1 / 0.5 / 0
  ↓  分组加权排序：features 0.4 · scenario 0.3 · specs 0.3
  ↓  Top-K + 推荐理由（命中项 / 未命中项）
```

排序权重与召回上限集中在 `app/selection/pipeline.py` 顶部常量，便于做消融实验。

---

## 2. 目录结构

```
app/
  main.py              FastAPI 入口：对话（SSE 流式）、购物车、订单、提醒、模型配置
  agent/
    loop.py            Function Calling 主循环（流式），会话按 session_id 隔离
    tools.py           工具定义与执行：搜索、加购、查看购物车、结算、支付
    prompts.py         系统提示词
  selection/
    parser.py          意图解析：硬约束 / 软约束结构化
    pipeline.py        选品流水线：召回 → 打分 → 加权排序 → Top-K
  cart/service.py      购物车与订单状态机（金额、状态迁移、提醒生成）
  reminder/scheduler.py APScheduler 定时扫描订单，未安装则降级为惰性刷新
  llm/
    client.py          OpenAI 兼容客户端（含流式 tool_call 分片合并）
    config_store.py    运行时模型配置（model_config.json，原子写入）
  db/
    database.py        连接管理与建表、索引
    seed.py            内置演示商品种子
ui/streamlit_app.py    Streamlit 单页前端（对话 + 商品卡片 + 右侧面板）
scripts/               导入脚本、离线冒烟检查
tests/                 pytest 单元测试
```

---

## 3. 快速开始

```bash
# 1. 安装依赖（慢可加 -i https://pypi.tuna.tsinghua.edu.cn/simple）
pip install -r requirements.txt

# 2. 启动后端（终端 1）
python -m uvicorn app.main:app --port 8000

# 3. 启动前端（终端 2）
python -m streamlit run ui/streamlit_app.py
```

打开 http://localhost:8501 ，首次启动会自动建库并写入演示商品。

### 模型配置（网页端，无需改代码）

页面右上角「模型设置」里填三项，保存即生效：

| 字段 | 说明 | 示例 |
|---|---|---|
| Base URL | 任何 OpenAI 兼容接口 | `https://api.deepseek.com` |
| 模型名 | 服务商标识 | `deepseek-chat` |
| API Key | 平台密钥 | `sk-...` |

点「测试连接」会真实发一次最小请求。地址漏写 `/v1` 时首次失败会自动补 `/v1` 重试。
配置保存在项目根 `model_config.json`（`source=runtime`），与业务数据库分离；
`source=env` 表示当前用的是 `.env` 兜底值。`.env` 只作兜底，不推荐把 Key 写死。

---

## 4. 功能清单

- 自然语言选品：预算 / 品类 / 品牌 / 功能 / 场景 / 规格多维约束
- Top-K 可解释推荐：卡片式呈现，整行可点开详情（功能标签、规格、库存、价格）
- 购物车：加购、移除、数量调整、金额确定性计算
- 结算与订单：生成待支付订单、支付倒计时、超时自动失效
- 支付提醒：截止前 N 分钟提醒，右侧面板实时显示
- 对话流式输出：SSE 增量渲染，带打字光标
- 会话隔离：每个浏览器标签页独立 `session_id`，互不污染

---

## 5. 降级运行

| 缺失项 | 影响 | 兜底 |
|---|---|---|
| openai / API Key | 对话无法调用模型 | 意图解析回退规则与关键词，选品仍可出结果，业务链路完整 |
| APScheduler | 后台定时提醒关闭 | 每次对话前惰性刷新订单状态，超时与提醒逻辑仍成立 |

---

## 6. 验证

```bash
python -m pytest -q            # 单元测试
python scripts/smoke_offline.py # 离线冒烟（不依赖大模型，输出 SMOKE_OK）
```

冒烟脚本会把模型配置路径重定向到临时目录，**不会污染真实的 model_config.json**。

---

## 7. 演示剧本

1. "预算 500 以内，想买个降噪耳机" → Top-5 卡片 + 推荐理由
2. 点卡片查看详情 → 加入购物车
3. "结算" → 生成待支付订单，告知截止时间
4. 等待提醒（默认截止前 10 分钟；演示时可把 `PAYMENT_TIMEOUT_MINUTES` 调成 2）
5. "支付订单" 或右侧面板按钮 → 订单完成

---

## 8. 配置项

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| LLM_BASE_URL | https://api.deepseek.com | 兜底接口地址 |
| LLM_MODEL | deepseek-chat | 兜底模型名 |
| LLM_API_KEY | （空） | 兜底 Key，推荐用网页端配置 |
| SHOP_AGENT_DB | 项目根 shop_agent.db | SQLite 文件路径 |
| PAYMENT_TIMEOUT_MINUTES | 30 | 订单支付超时时间 |
| REMIND_BEFORE_MINUTES | 10 | 截止前多少分钟生成提醒 |

---

## 9. 数据来源

演示商品库基于公开的中文商品数据集构建（商品标题、类目、属性标签、价格、
店铺等字段），导入脚本见 `scripts/`，可复现生成 SQLite 数据库。

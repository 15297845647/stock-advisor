# Stock Advisor — A股/期货 AI 分析系统

通过微信个人号与 AI 对话，获取 A 股和期货的每日技术分析和个性化推荐。

## 核心功能

| 功能 | 说明 |
|------|------|
| **智能对话** | 通过微信自然语言交互，自动识别意图 |
| **个股分析** | 3 级深度可选：快速 (~8s) / 标准 (~20s) / 深度 (~50s) |
| **股票推荐** | 规则漏斗（硬筛→板块加权→技术评分）+ LLM 语义裁决 |
| **深度分析** | 4 分析师并行 → 多空辩论 → 三方风控 |
| **期货分析** | 支持主力合约和指定合约的行情查询与分析 |
| **持仓管理** | 通过对话记录持仓，AI 自动识别上下文中的持仓 |
| **操作策略推送** | 每日开盘前 09:00 和收盘后 15:30 自动推送持仓策略 |
| **关注列表** | 自选股管理，收盘后自动分析并推送 |
| **决策校准** | 规则引擎对 AI 决策进行二次校准（资金流/支撑位/RSI） |
| **回测引擎** | 评估历史推荐的准确性（方向/目标价/止损） |
| **市场红绿灯** | 综合涨跌家数、涨停跌停、指数变化判断市场情绪 |
| **多数据源降级** | AKShare 五档 → 全量快照 → Tushare → 日K兜底，任一失败自动切下一个 |
| **多 LLM 供应商** | 按任务分发 DeepSeek/Qwen/MiniMax，成本可控 |
| **用量统计** | Admin 后台看每天 token 消耗 / 成本 / 用户排行 |
| **报告 PDF 分享** | 深度分析生成 7 天有效短链，微信推链接 |
| **自选股新闻预拉** | 每交易日 07:30 提前拉全体自选股新闻入库 |
| **管理后台** | 用户管理、Bot 绑定、日志查看、数据源观测、LLM 用量、配置管理 |

---

## 架构

```
微信用户 → ilink网关 → cc-connect → ACP Agent (Python)
                                      │
                                      ├── LLMRouter (DeepSeek/Qwen/MiniMax 多供应商路由)
                                      ├── DataSourceManager (AKShare + Tushare 多源降级链)
                                      ├── RecommendService (规则漏斗 + LLM 裁决)
                                      ├── AnalystPipeline (4分析师 + 辩论 + 风控)
                                      └── SQLite (记忆 + 缓存 + 用量 + 推荐 + 分享)
```

---

## 项目目录结构

```
stock-advisor/
├── cli.py                          # CLI 入口
├── requirements.txt                # Python 依赖
├── .env / .env.example             # 环境变量配置
├── config/                         # ★ 新增：策略配置
│   ├── data_sources.yaml           #   多数据源降级链
│   └── llm_routing.yaml            #   LLM 任务路由 + 计价
│
├── agent/                          # Agent 入口层
│   ├── main.py                     #   ACP Agent 主循环
│   ├── config.py                   #   全局配置
│   └── user_router.py              #   用户身份识别
│
├── application/                    # 应用服务层（流程编排）
│   ├── chat_service.py             #   对话主路由
│   ├── analysis_service.py         #   个股分析（3 级深度）
│   ├── analyst_agents.py           #   多分析师并行管线
│   ├── debate_service.py           #   多空辩论 + 风控
│   ├── recommend_service.py        # ★ 推荐总编排（6 Layer）
│   ├── news_sync_service.py        # ★ 自选股新闻预拉取
│   ├── report_export_service.py    # ★ 报告 PDF 分享
│   ├── data_source_admin_service.py # ★ 数据源 Admin 编排
│   ├── usage_statistics_service.py # ★ LLM 用量统计
│   ├── progress_notifier.py        # ★ 分析进度推送
│   ├── stock_picker_service.py     #   选股编排
│   ├── config_service.py           #   策略配置
│   ├── backtest_service.py         #   回测流程
│   └── subscription_service.py     #   关注列表管理
│
├── domain/                         # 领域层（核心业务逻辑）
│   ├── intent_parser.py            #   意图识别
│   ├── recommend_intent_parser.py  # ★ 推荐意图结构化解析
│   ├── keyword_dictionaries.py     # ★ 关键词字典
│   ├── recommend_screener.py       # ★ 硬规则筛选引擎
│   ├── sector_heat.py              # ★ 板块热度加权
│   ├── technical_scorer.py         # ★ 5 维技术评分
│   ├── recommendation_parser.py    # ★ LLM 推荐结果解析
│   ├── data_source_policy.py       # ★ 数据源降级策略
│   ├── llm_routing_policy.py       # ★ LLM 任务路由策略
│   ├── report_renderer.py          # ★ 报告 Markdown/HTML 渲染
│   ├── prompt_builder.py           #   Prompt 构建器
│   ├── stock_analyzer.py           #   技术指标计算
│   ├── stock_screener.py           #   选股筛选引擎
│   ├── decision_parser.py          #   结构化决策提取
│   ├── decision_stabilizer.py      #   决策校准
│   ├── backtest_engine.py          #   回测核心
│   ├── market_light.py             #   市场红绿灯
│   └── models/
│       ├── stock.py                #   Stock/StockNews/StockDecision
│       ├── user_context.py         #   用户上下文
│       ├── analysis_report.py      #   分析报告
│       ├── data_source.py          # ★ 数据源类型枚举
│       ├── llm_task.py             # ★ LLM 任务类型
│       ├── llm_usage.py            # ★ LLM 用量记录
│       ├── recommend_intent.py     # ★ 推荐意图
│       ├── candidate.py            # ★ 候选股票
│       ├── recommendation.py       # ★ 推荐结果
│       ├── research_depth.py       # ★ 研究深度 + 进度事件
│       └── report_share.py         # ★ 报告分享
│
├── infrastructure/                 # 基础设施层
│   ├── akshare_client.py           #   AKShare 数据客户端
│   ├── tushare_client.py           #   Tushare 客户端
│   ├── tdx_client.py               #   通达信客户端
│   ├── minimax_client.py           #   LLM 兼容 shim（委托 LLMRouter）
│   ├── database.py                 #   SQLite 建表
│   ├── log_setup.py                #   日志配置
│   ├── data_source/                # ★ 多数据源降级
│   │   ├── base.py                 #   DataSource 抽象基类
│   │   ├── manager.py              #   DataSourceManager 降级管理器
│   │   ├── factory.py              #   全局单例工厂
│   │   ├── circuit_breaker.py      #   熔断器
│   │   ├── akshare_source.py       #   6 个 AKShare 适配器
│   │   └── tushare_source.py       #   3 个 Tushare 适配器
│   └── llm/                        # ★ 多 LLM 供应商
│       ├── base.py                 #   LLMProvider 抽象
│       ├── openai_compat_provider.py #  OpenAI 兼容协议实现
│       ├── router.py               #   LLMRouter 路由 + fallback
│       └── cost_calculator.py      #   成本计算
│
├── repository/                     # 数据访问层
│   ├── user_repository.py          #   用户/记忆/关注列表
│   ├── strategy_config_repository.py #   策略配置
│   ├── report_repository.py        #   分析报告
│   ├── stock_repository.py         #   行情缓存
│   ├── data_source_call_log_repository.py # ★ 数据源调用日志
│   ├── llm_usage_repository.py     # ★ LLM 用量记录
│   ├── recommend_repository.py     # ★ 推荐记录（供回测）
│   ├── news_repository.py          # ★ 股票新闻缓存
│   └── report_share_repository.py  # ★ 报告分享记录
│
├── scheduler/                      # 定时任务
│   ├── daily_push.py               #   每日推送（15:30 收盘自选分析）
│   └── news_sync.py                # ★ 07:30 自选股新闻同步
│
├── prompts/                        # Prompt 模板
│   ├── system.txt
│   ├── analysis.txt
│   ├── recommend.txt
│   ├── recommend_judge.txt         # ★ 推荐裁决（新流程）
│   ├── chat.txt
│   ├── debate_bull.txt
│   ├── debate_bear.txt
│   ├── debate_judge.txt
│   ├── risk_conservative.txt
│   ├── risk_aggressive.txt
│   ├── risk_neutral.txt
│   └── risk_manager.txt
│
├── admin/                          # 管理后台
│   ├── server.py                   #   FastAPI 应用 + 定时任务启动
│   ├── api.py                      #   API 路由（含数据源、用量、分享）
│   ├── auth.py                     #   JWT 认证
│   ├── bot_manager.py              #   Bot 配置生成
│   ├── startup.py                  #   后台启动
│   └── static/index.html           #   前端页面（含数据源观测页）
│
├── cc-connect/                     # cc-connect 配置模板
├── deploy/                         # 部署脚本
└── data/                           # 运行时数据
    ├── stock_advisor.db            #   SQLite 数据库
    └── logs/                       #   日志目录
```

★ = 本次改造新增

---

## 傻瓜式安装指南

### 前提条件

- **Python 3.11+** (`python3 --version`)
- **Node.js + npm** (`npm --version`)
- **LLM API Key** — DeepSeek / Qwen / MiniMax 任一（推荐 DeepSeek，便宜）

macOS 装 Python/Node：

```bash
brew install python@3.11 node
```

### 第一步：安装 Python 依赖

```bash
cd ~/stock-advisor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 第二步：填写 API Key

```bash
cp .env.example .env
open .env
```

至少要填一个 LLM Key。推荐组合：

```
# DeepSeek（主力，便宜）
DEEPSEEK_API_KEY=sk-xxxx

# Qwen（备胎，可选）
DASHSCOPE_API_KEY=sk-xxxx

# 兜底（旧变量，向后兼容）
LLM_API_KEY=sk-xxxx

# Tushare token（可选，无 token 时 tushare 源自动禁用）
TUSHARE_TOKEN=your_tushare_token

# 管理后台密码
ADMIN_PASSWORD=your_secure_password

# 分享链接的公网 base URL（部署到服务器时填）
PUBLIC_BASE_URL=https://your-domain.com
```

### 第三步：（可选）调整数据源与 LLM 路由配置

- `config/data_sources.yaml` — 数据源降级链 + 熔断参数
- `config/llm_routing.yaml` — 每个任务用哪个 LLM，成本计价

保持默认即可开箱可用；后期可以按需微调（例如把 `judge` 从 `deepseek-chat` 换成 `qwen-max`）。

### 第四步：测试管理后台

```bash
cd ~/stock-advisor
source .venv/bin/activate
PYTHONPATH=. python cli.py admin
```

浏览器访问 [http://localhost:8900](http://localhost:8900) 输入密码登录。

新增侧边栏 **数据源** 菜单：

- 概览卡片（健康源数、成功率、总调用数）
- 源状态表（含单源"重置熔断"按钮）
- 降级链命中分布（横向堆叠条）
- 最近失败日志

### 第五步-第七步

同旧版：安装 cc-connect → 配置 → 扫码绑定 → 启动。

```bash
npm install -g cc-connect
mkdir -p ~/.cc-connect
cp ~/stock-advisor/cc-connect/config.example.toml ~/.cc-connect/config.toml
cc-connect weixin setup --project stock-advisor
cc-connect
```

---

## 支持的微信指令

| 消息 | AI 做什么 |
|------|-----------|
| `分析 000001` / `看看 600519` | 标准分析（4 分析师 + Judge，~20s） |
| `快速分析 000001` / `快速看 平安银行` | ⚡ 快速分析（单次综合，~8s） |
| `深度分析 000001` / `详细分析 600519` | 🔬 深度分析（+辩论+风控，~50s，含 PDF 分享链） |
| `推荐几只短线活跃的` | 触发新推荐流程（规则漏斗 + LLM 裁决） |
| `推荐 3 只低估白马` | 支持指定数量 + 风格关键词 |
| `再推一批` / `换一批` | 排除已推荐过的，重新出 |
| `关注 000001` | 加入自选 |
| `取消关注 000001` | 移除自选 |
| `自选股` / `关注列表` | 查看自选 |
| `大盘` / `今天行情` | 大盘概览 |
| 自由聊天 | AI 对话（记住偏好和历史） |

### 推荐关键词字典（部分）

- **风格**：短线/波段/中线/长线/价值/低估/白马/蓝筹/成长/题材/热门/龙头/小盘/大盘
- **板块**：半导体/新能源/AI/算力/医药/创新药/军工/机器人/白酒/银行/地产/汽车/有色/煤炭/钢铁/电力
- **黑名单**：`不要煤炭`、`别推 ST`、`排除银行` 等
- **风险偏好**：由用户画像 `risk_level` 决定门槛（保守型自动加市值/PE 限制）

---

## 每日自动推送

- **07:30**：自动拉全体自选股新闻入库（新增，减少分析时延迟）
- **15:30**：分析你关注列表里的所有股票，把摘要推送到微信

---

## 管理后台功能

浏览器访问 `http://localhost:8900`：

- **仪表盘** — 用户数、对话量、系统状态
- **用户管理** — 用户列表、编辑画像、关注列表、对话记录、Bot 绑定
- **策略配置** — 养家/中长线选股参数
- **数据源** ★新 — 健康状态、命中率、降级链分布、失败日志、手动重置熔断、热重载配置
- **LLM 用量** ★新 — 通过 `/api/usage/dashboard`、`/api/usage/daily` 查看
- **日志查看** — 日志文件、级别过滤、关键词搜索
- **配置管理** — LLM Key、Tushare Token、管理密码

---

## 服务器部署

```bash
bash deploy/setup.sh
```

---

## 代码更新流程

同旧版：本地 commit push → 服务器 pull → 重启服务。

首次升级到本版本需要 **重新安装依赖**（新增 pyyaml/tushare）：

```bash
cd /opt/stock-advisor
.venv/bin/pip install -r requirements.txt
```

---

## 服务重启命令

（与旧版一致，此处从略，参考 `deploy/` 目录）

---

## 日志管理

- 日志目录：`data/logs/`
- 每日自动分割，保留 7 天
- 管理后台「日志查看」页在线查看

---

## 成本

（DeepSeek 主力）

- **服务器**：~60 元/月（轻量云）
- **DeepSeek API**：~30-100 元/月（新推荐流程约省 40% 成本）
- **AKShare + cc-connect**：免费
- **Tushare**（可选）：120 元/年 起（免费额度不够用时）

---

## 版本亮点（本次改造）

### v0.2.0 — Data + LLM 多源化

- ✅ **数据源降级链**：AKShare 单源崩溃时自动降级 Tushare/其他 AK 接口
- ✅ **LLM 路由**：按任务分发 DeepSeek/Qwen/MiniMax，成本降 40%+
- ✅ **推荐流程重生**：规则漏斗（硬筛→板块→技术评分）+ LLM 裁决，比原两阶段快 30%
- ✅ **分析分级**：快速/标准/深度 3 档可选
- ✅ **PDF 报告分享**：深度分析生成短链，微信推链接
- ✅ **自选股新闻预拉**：07:30 提前入库，分析响应更快
- ✅ **Admin 观测**：数据源实时健康、命中率、失败日志
- ✅ **用量统计**：token 消耗、成本、用户排行
- ✅ **进度推送**：深度分析中间态提醒（cc-connect 主动推送 API 已预留）

---

## 免责声明

本系统仅供学习和研究使用，不构成投资建议。投资有风险，决策需谨慎。

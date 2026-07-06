# Stock Advisor — A股/期货 AI 分析系统

通过微信个人号与 AI 对话，获取 A 股和期货的每日技术分析和个性化推荐。

## 核心功能

| 功能 | 说明 |
|------|------|
| **智能对话** | 通过微信自然语言交互，自动识别意图（分析/推荐/关注/持仓等） |
| **个股分析** | 技术面 + 基本面 + 资金流 + 新闻综合分析，输出结构化投资建议 |
| **深度分析** | 4 路分析师并行（技术/基本面/新闻/资金）→ 多空辩论 → 风控评估 |
| **股票推荐** | 基于全市场行情筛选，按用户风险偏好推荐 |
| **期货分析** | 支持主力合约和指定合约的行情查询与分析 |
| **持仓管理** | 通过对话记录持仓，AI 自动识别上下文中的持仓信息并确认 |
| **操作策略推送** | 每日开盘前 09:00 和收盘后 15:30 自动推送持仓策略 |
| **关注列表** | 自选股管理，收盘后自动分析并推送 |
| **决策校准** | 规则引擎对 AI 决策进行二次校准（资金流/支撑位/RSI） |
| **回测引擎** | 评估历史推荐的准确性（方向/目标价/止损） |
| **市场红绿灯** | 综合涨跌家数、涨停跌停、指数变化判断市场情绪 |
| **选股筛选** | 内置金叉选股、超跌反弹等预设策略 |
| **多用户隔离** | 每个用户独立记忆、关注列表、持仓、风险偏好 |
| **管理后台** | 用户管理、Bot 绑定、日志查看、配置管理 |

---

## 架构

```
微信用户 → ilink网关 → cc-connect → ACP Agent (Python)
                                        ├── MiniMax M2.7 (分析 + 对话)
                                        ├── AKShare (行情数据)
                                        └── SQLite (记忆 + 缓存)
```

---

## 项目目录结构

```
stock-advisor/
├── cli.py                          # CLI 入口（start/admin/analyze/push/test-data）
├── requirements.txt                # Python 依赖
├── .env / .env.example             # 环境变量配置
│
├── agent/                          # Agent 入口层
│   ├── main.py                     #   ACP Agent 主循环（stdio JSON-RPC）
│   ├── config.py                   #   全局配置（API Key/端口/限频等）
│   └── user_router.py              #   用户身份识别 & 上下文加载
│
├── application/                    # 应用服务层（流程编排）
│   ├── chat_service.py             #   对话主路由（意图分发 → 各处理器）
│   ├── analysis_service.py         #   股票/期货分析编排
│   ├── analyst_agents.py           #   多分析师并行管线（技术/基本面/新闻/资金）
│   ├── debate_service.py           #   多空辩论 + 风控评估编排
│   ├── stock_picker_service.py     #   选股编排（养家短线 / 中长线）
│   ├── config_service.py           #   策略配置读写（多策略）
│   ├── backtest_service.py         #   回测流程编排
│   └── subscription_service.py     #   关注列表管理
│
├── domain/                         # 领域层（核心业务逻辑）
│   ├── intent_parser.py            #   意图识别（关键词 + 规则）
│   ├── prompt_builder.py           #   Prompt 构建器
│   ├── stock_analyzer.py           #   分析指标计算
│   ├── stock_screener.py           #   选股筛选引擎
│   ├── decision_parser.py          #   结构化决策提取（从 LLM 输出）
│   ├── decision_stabilizer.py      #   决策校准（规则引擎修正 AI 建议）
│   ├── backtest_engine.py          #   回测核心（预测 vs 实际对比）
│   ├── market_light.py             #   市场红绿灯（情绪指标）
│   └── models/                     #   数据模型
│       ├── stock.py                #     Stock/StockNews/StockDecision/StockFundamental
│       ├── user_context.py         #     用户上下文
│       └── analysis_report.py      #     分析报告
│
├── infrastructure/                 # 基础设施层（外部依赖对接）
│   ├── akshare_client.py           #   AKShare 数据客户端（行情/K线/资金流/新闻/期货）
│   ├── minimax_client.py           #   MiniMax LLM 客户端
│   ├── database.py                 #   SQLite 连接 & 建表
│   └── log_setup.py                #   日志配置（按天轮转/7天清理）
│
├── repository/                     # 数据访问层
│   ├── user_repository.py          #   用户/记忆/关注列表 CRUD
│   ├── strategy_config_repository.py #   策略配置 CRUD
│   ├── report_repository.py        #   分析报告存取
│   └── stock_repository.py         #   行情缓存存取
│
├── scheduler/                      # 定时任务
│   └── daily_push.py               #   每日推送（15:30 收盘自选分析）
│
├── prompts/                        # Prompt 模板
│   ├── system.txt                  #   系统人设
│   ├── analysis.txt                #   个股分析模板
│   ├── recommend.txt               #   推荐模板
│   ├── chat.txt                    #   自由对话模板
│   ├── debate_bull.txt             #   多头研究员
│   ├── debate_bear.txt             #   空头研究员
│   ├── debate_judge.txt            #   研究主管（裁决）
│   ├── risk_conservative.txt       #   保守型风控
│   ├── risk_aggressive.txt         #   激进型风控
│   ├── risk_neutral.txt            #   中性风控
│   └── risk_manager.txt            #   风控主管（综合）
│
├── admin/                          # 管理后台
│   ├── server.py                   #   FastAPI 应用
│   ├── api.py                      #   API 路由（用户/配置/日志/Bot）
│   ├── auth.py                     #   JWT 认证
│   ├── bot_manager.py              #   Bot 配置生成 & cc-connect 进程管理
│   ├── startup.py                  #   后台启动（线程模式）
│   └── static/index.html           #   前端页面（Alpine.js + Tailwind）
│
├── cc-connect/                     # cc-connect 配置模板
│   └── config.example.toml
│
├── deploy/                         # 部署脚本
│   └── setup.sh
│
└── data/                           # 运行时数据（不提交 git）
    ├── stock_advisor.db            #   SQLite 数据库
    └── logs/                       #   日志目录
        ├── agent.log               #     Agent 日志（按天轮转）
        ├── cc-connect.log          #     微信桥接日志
        └── admin.log               #     管理后台日志
```

---

## 傻瓜式安装指南（一步一步跟着做）

### 前提条件

你的电脑需要已经安装好：

- **Python 3.11+** — 终端输入 `python3 --version` 能看到版本号就行
- **Node.js + npm** — 终端输入 `npm --version` 能看到版本号就行
- **MiniMax API Key** — 去 [MiniMax 开放平台](https://www.minimaxi.com/) 注册并创建 API Key

如果没装 Python/Node，macOS 可以用 Homebrew：

```bash
brew install python@3.11 node
```

---

### 第一步：安装 Python 依赖

打开终端，复制粘贴以下命令（一行一行执行）：

```bash
cd ~/stock-advisor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 看到 `Successfully installed ...` 就是成功了。

---

### 第二步：填写你的 API Key

```bash
cp .env.example .env
open .env
```

> 这会用文本编辑器打开 `.env` 文件。把 `your-minimax-api-key-here` 替换成你真实的 MiniMax API Key，保存关闭。
>
> 同时把 `ADMIN_PASSWORD=changeme` 改成你想要的管理后台密码。

---

### 第三步：测试管理后台（可选，先验证代码能跑）

```bash
cd ~/stock-advisor
source .venv/bin/activate
PYTHONPATH=. python cli.py admin
```

> 打开浏览器访问 [http://localhost:8900](http://localhost:8900) ，输入你刚才设的密码登录。
> 看到仪表盘页面就说明代码没问题。按 `Ctrl+C` 停掉。

---

### 第四步：安装微信桥接工具 cc-connect

```bash
npm install -g cc-connect
```

> 看到安装成功提示即可。

---

### 第五步：配置 cc-connect

```bash
mkdir -p ~/.cc-connect
cp ~/stock-advisor/cc-connect/config.example.toml ~/.cc-connect/config.toml
open ~/.cc-connect/config.toml
```

> 编辑器打开后，你需要修改两处：
>
> 1. 把 `MINIMAX_API_KEY = "your-minimax-api-key"` 改成你的真实 Key
> 2. 确认 `work_dir`、`PYTHONPATH`、`DB_PATH` 里的路径是对的（默认是 `/Users/shy/stock-advisor`）
>
> 保存关闭。

---

### 第六步：绑定你的微信

```bash
cc-connect weixin setup --project stock-advisor
```

> 终端会显示一个二维码。打开手机微信 → 扫一扫 → 扫这个二维码 → 确认绑定。
>
> 绑定成功后 token 会自动写入配置文件，不需要你手动操作。

---

### 第七步：启动！

```bash
cc-connect
```

> 看到类似 `agent initialized` 的日志就表示一切正常。
>
> 现在找个朋友（或用另一个微信号）给你的微信发消息试试：
>
> - 发 `分析 000001` → AI 会回复平安银行的技术分析
> - 发 `关注 600519` → 把茅台加入关注列表
> - 发 `大盘` → 看今天大盘概览
> - 发任何问题 → AI 自由对话
>
> **管理后台**同时也在运行，浏览器访问 `http://localhost:8900` 可以管理用户和配置。

---

### 日常使用

每次要启动系统，只需要：

```bash
cc-connect
```

要停止：按 `Ctrl+C`。

---

## 支持的微信指令


| 你发的消息                     | AI 做什么              |
| ------------------------- | ------------------- |
| `分析 000001` 或 `看看 600519` | 返回个股技术面分析报告         |
| `关注 000001`               | 把这只股票加入你的关注列表       |
| `取消关注 000001`             | 从关注列表移除             |
| `关注列表` 或 `自选股`            | 查看你关注了哪些股票          |
| `大盘` 或 `今天行情`             | 大盘指数概览              |
| 随便聊天                      | AI 自由对话（会记住你的偏好和历史） |


## 每日自动推送

每个交易日 **15:30 收盘后**，系统自动分析你关注列表里的所有股票，把分析摘要推送到你的微信。无需手动操作。

---

## 管理后台

地址：`http://localhost:8900`（或 `http://服务器IP:8900`）

功能：

- **仪表盘** — 用户数、对话量、系统状态一目了然
- **用户管理** — 查看所有用户、编辑用户画像、查看关注列表和对话记录
- **配置管理** — 在线修改 MiniMax API Key、模型、管理密码

---

## 服务器部署（长期运行）

如果不想一直开着电脑，可以部署到云服务器：

```bash
# 把代码上传到服务器后
bash deploy/setup.sh
```

这个脚本会自动安装所有依赖、配置系统服务，让系统开机自启。

---

## 代码更新 & 部署流程

本地修改代码后，按以下步骤部署到服务器：

### 1. 本地提交推送

```bash
cd ~/stock-advisor
git add -A
git commit -m "描述你的改动"
git push
```

### 2. 服务器拉取代码

```bash
ssh root@你的服务器IP
cd /opt/stock-advisor
git pull
```

### 3. 重启服务（见下方「服务重启命令」）

---

## 服务重启命令

服务器上有三个需要管理的进程：

### 重启管理后台（Admin）

```bash
fuser -k 8900/tcp
cd /opt/stock-advisor && nohup /opt/stock-advisor/.venv/bin/python -m cli admin >> /opt/stock-advisor/data/logs/admin.log 2>&1 &
```

### 重启 cc-connect（微信桥接）

```bash
pkill cc-connect
cd /opt/stock-advisor && nohup cc-connect >> /opt/stock-advisor/data/logs/cc-connect.log 2>&1 &
```

也可以在管理后台页面点击「重启 cc-connect」按钮。

### 全部重启（一键）

```bash
# 停掉所有服务
fuser -k 8900/tcp; pkill cc-connect

# 启动 admin
cd /opt/stock-advisor && nohup /opt/stock-advisor/.venv/bin/python -m cli admin >> /opt/stock-advisor/data/logs/admin.log 2>&1 &

# 启动 cc-connect
nohup cc-connect >> /opt/stock-advisor/data/logs/cc-connect.log 2>&1 &
```

### 检查服务状态

```bash
# 检查 admin 是否在运行
fuser 8900/tcp

# 检查 cc-connect 是否在运行
pgrep -a cc-connect

# 查看 agent 日志（实时）
tail -f /opt/stock-advisor/data/logs/agent.log

# 查看 cc-connect 日志
tail -f /opt/stock-advisor/data/logs/cc-connect.log

# 查看 admin 日志
tail -f /opt/stock-advisor/data/logs/admin.log
```

### 安装新依赖后

如果修改了 `requirements.txt`，服务器上需要重新安装：

```bash
cd /opt/stock-advisor
.venv/bin/pip install -r requirements.txt
```

然后重启相关服务。

---

## 日志管理

- 日志目录：`data/logs/`
- `agent.log` — AI 对话和数据拉取日志
- `cc-connect.log` — 微信桥接日志
- `admin.log` — 管理后台日志
- 日志按天自动分割，保留 7 天，历史文件格式：`agent.log.2026-06-11`
- 管理后台「日志查看」页可在线查看，支持级别过滤和关键词搜索

---

## 成本

- 服务器：~60 元/月（轻量云）
- MiniMax API：~50-150 元/月（看使用量）
- AKShare + cc-connect：免费


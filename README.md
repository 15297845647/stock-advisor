# Stock Advisor — A股/期货 AI 分析系统

通过微信个人号与 AI 对话，获取 A 股和期货的每日技术分析和个性化推荐。

## 架构

```
微信用户 → ilink网关 → cc-connect → ACP Agent (Python)
                                        ├── MiniMax M2.7 (分析 + 对话)
                                        ├── AKShare (行情数据)
                                        └── SQLite (记忆 + 缓存)
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

> 打开浏览器访问 http://localhost:8900 ，输入你刚才设的密码登录。
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

| 你发的消息 | AI 做什么 |
|-----------|----------|
| `分析 000001` 或 `看看 600519` | 返回个股技术面分析报告 |
| `关注 000001` | 把这只股票加入你的关注列表 |
| `取消关注 000001` | 从关注列表移除 |
| `关注列表` 或 `自选股` | 查看你关注了哪些股票 |
| `大盘` 或 `今天行情` | 大盘指数概览 |
| 随便聊天 | AI 自由对话（会记住你的偏好和历史） |

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

## 成本

- 服务器：~60 元/月（轻量云）
- MiniMax API：~50-150 元/月（看使用量）
- AKShare + cc-connect：免费

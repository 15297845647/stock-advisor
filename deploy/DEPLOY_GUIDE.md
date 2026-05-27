# 服务器部署指南（傻瓜式）

本文档手把手教你把 Stock Advisor 部署到一台 Linux 云服务器上，实现 24 小时运行。

---

## 你需要准备什么


| 东西              | 说明                                                           |
| --------------- | ------------------------------------------------------------ |
| 一台云服务器          | 推荐腾讯云/阿里云轻量应用服务器，2核2G 就够，Ubuntu 22.04 系统，约 60 元/月            |
| MiniMax API Key | 去 [MiniMax 开放平台](https://www.minimaxi.com/) 注册获取             |
| 一个微信号           | 当机器人用的微信号（别人加它为好友后就能对话）                                      |
| SSH 工具          | Mac 自带终端就行；Windows 用 [Termius](https://termius.com/) 或 PuTTY |


---

## 第一步：登录你的服务器

打开终端，输入（把 `你的服务器IP` 换成真实 IP）：

```bash
ssh root@你的服务器IP
```

> 第一次连接会问你 `yes/no`，输入 `yes` 回车。
> 然后输入服务器密码（输入时屏幕不会显示，正常的，输完回车就行）。

---

## 第二步：安装系统软件

逐行复制粘贴执行：

```bash
apt-get update
apt-get install -y python3.11 python3.11-venv git nodejs npm
```

> 如果提示 python3.11 找不到，先执行：
>
> ```bash
> apt-get install -y software-properties-common
> add-apt-repository ppa:deadsnakes/ppa -y
> apt-get update
> apt-get install -y python3.11 python3.11-venv
> ```

验证安装成功：

```bash
python3.11 --version
node --version
npm --version
```

> 三个都能输出版本号就对了。

---

## 第三步：安装微信桥接工具

```bash
npm install -g cc-connect
```

验证：

```bash
cc-connect --version
```

> 能看到版本号就行。

---

## 第四步：下载代码

```bash
cd /opt
git clone https://github.com/15297845647/stock-advisor.git
cd /opt/stock-advisor
```

---

## 第五步：安装 Python 依赖

```bash
cd /opt/stock-advisor
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> 等它跑完，看到 `Successfully installed ...` 就行。需要几分钟。

---

## 第六步：填写配置

### 6.1 设置环境变量

```bash
cp .env.example .env
nano .env
```

> nano 是一个简单的文本编辑器。你会看到类似这样的内容：
>
> ```
> MINIMAX_API_KEY=your-minimax-api-key-here
> DB_PATH=./data/stock_advisor.db
> ADMIN_PORT=8900
> ADMIN_PASSWORD=changeme
> ```
>
> **你需要改的：**
>
> 1. 把 `your-minimax-api-key-here` 替换成你的 MiniMax API Key
> 2. 把 `changeme` 替换成你想设的管理后台密码
>
> 改完后按 `Ctrl+O` 保存，`Enter` 确认，`Ctrl+X` 退出。

### 6.2 设置 cc-connect 配置

```bash
mkdir -p ~/.cc-connect
nano ~/.cc-connect/config.toml
```

> 粘贴以下内容（把 `your-minimax-api-key` 换成你的真实 Key）：

```toml
[[projects]]
name = "stock-advisor"
work_dir = "/opt/stock-advisor"

[projects.agent]
type = "acp"
command = "/opt/stock-advisor/.venv/bin/python"
args = ["agent/main.py"]

[projects.agent.env]
MINIMAX_API_KEY = "your-minimax-api-key"
PYTHONPATH = "/opt/stock-advisor"
DB_PATH = "/opt/stock-advisor/data/stock_advisor.db"

[[projects.platforms]]
type = "weixin"

[projects.platforms.options]
token = ""
allow_from = "*"
```

> 按 `Ctrl+O` 保存，`Enter` 确认，`Ctrl+X` 退出。

---

## 第七步：绑定微信

```bash
cc-connect weixin setup --project stock-advisor
```

> 终端会显示一个二维码。
>
> **重要**：你需要用**要当机器人的那个微信号**扫这个码。
>
> 打开手机微信 → 扫一扫 → 对准屏幕上的二维码 → 确认绑定。
>
> 看到 `setup success` 就绑定好了。

---

## 第八步：测试运行

先手动跑一下，确认没问题：

```bash
cc-connect
```

> 看到类似 `StockAdvisorAgent initialized` 的日志就说明成功了。
>
> 此时用另一个微信号给机器人发 `大盘`，如果收到回复，一切正常。
>
> 按 `Ctrl+C` 停掉。

---

## 第九步：设为开机自启（后台永久运行）

### 9.1 修改 systemd 服务文件中的用户名

```bash
nano /opt/stock-advisor/deploy/stock-advisor.service
```

> 找到 `User=shy` 这一行，把 `shy` 改成你服务器上的用户名。
> 如果你一直用 root 登录的，就改成 `User=root`。
>
> `Ctrl+O` 保存，`Enter`，`Ctrl+X` 退出。

### 9.2 安装并启动服务

```bash
cp /opt/stock-advisor/deploy/stock-advisor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable stock-advisor
systemctl start stock-advisor
```

### 9.3 确认服务正在运行

```bash
systemctl status stock-advisor
```

> 看到 `**active (running)**` 就成功了。

---

## 日常运维命令

你以后可能会用到的命令，收藏备用：


| 操作     | 命令                                |
| ------ | --------------------------------- |
| 查看运行状态 | `systemctl status stock-advisor`  |
| 查看实时日志 | `journalctl -u stock-advisor -f`  |
| 重启服务   | `systemctl restart stock-advisor` |
| 停止服务   | `systemctl stop stock-advisor`    |
| 启动服务   | `systemctl start stock-advisor`   |
| 打开管理后台 | 浏览器访问 `http://你的服务器IP:8900`       |


---

## 更新代码

以后代码有更新，执行：

```bash
cd /opt/stock-advisor
git pull
source .venv/bin/activate
pip install -r requirements.txt
systemctl restart stock-advisor
```

---

## 防火墙放行（如果管理后台打不开）

如果浏览器访问 `http://服务器IP:8900` 打不开，可能是防火墙没放行：

**服务器防火墙：**

```bash
ufw allow 8900/tcp
```

**云平台安全组：**
去腾讯云/阿里云控制台 → 安全组 → 添加入站规则 → 端口 `8900`，协议 `TCP`，来源 `0.0.0.0/0`。

---

## 常见问题

### Q: 扫码绑定微信后，过几天断了怎么办？

ilink 协议会自动重连。如果确实断了，执行 `cc-connect weixin setup --project stock-advisor` 重新扫码，然后 `systemctl restart stock-advisor`。

### Q: AI 回复很慢

检查 MiniMax API 余额是否充足。也可以在管理后台「配置管理」里换用更快的模型。

### Q: 怎么限制只有特定的人能用？

编辑 `~/.cc-connect/config.toml`，把 `allow_from = "*"` 改成指定的微信 ID，逗号分隔。然后 `systemctl restart stock-advisor`。

### Q: 服务器重启后还能自动运行吗？

能。第九步的 `systemctl enable` 已经设好了开机自启。
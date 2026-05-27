#!/bin/bash
# 服务器初始化部署脚本
set -e

PROJECT_DIR="/opt/stock-advisor"
VENV_DIR="$PROJECT_DIR/.venv"

echo "=== 1. 安装系统依赖 ==="
sudo apt-get update -qq
sudo apt-get install -y python3.11 python3.11-venv nodejs npm

echo "=== 2. 安装 cc-connect ==="
sudo npm install -g cc-connect

echo "=== 3. 创建项目目录 ==="
sudo mkdir -p "$PROJECT_DIR"
sudo chown "$USER:$USER" "$PROJECT_DIR"

echo "=== 4. 复制项目文件 ==="
# 假设代码已 rsync/scp 到 $PROJECT_DIR
# rsync -avz ./stock-advisor/ server:$PROJECT_DIR/

echo "=== 5. 创建 Python 虚拟环境 ==="
python3.11 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install -r "$PROJECT_DIR/requirements.txt"

echo "=== 6. 配置 .env ==="
if [ ! -f "$PROJECT_DIR/.env" ]; then
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "请编辑 $PROJECT_DIR/.env 填入 MINIMAX_API_KEY"
fi

echo "=== 7. 配置 cc-connect ==="
mkdir -p ~/.cc-connect
if [ ! -f ~/.cc-connect/config.toml ]; then
    # 调整 config 中的 python 路径指向 venv
    cat > ~/.cc-connect/config.toml << 'TOML'
[[projects]]
name = "stock-advisor"
work_dir = "/opt/stock-advisor"

[projects.agent]
type = "acp"
command = "/opt/stock-advisor/.venv/bin/python"
args = ["agent/main.py"]

[projects.agent.env]
PYTHONPATH = "/opt/stock-advisor"
DB_PATH = "/opt/stock-advisor/data/stock_advisor.db"

[[projects.platforms]]
type = "weixin"

[projects.platforms.options]
token = ""
allow_from = "*"
TOML
    echo "已生成 ~/.cc-connect/config.toml，MINIMAX_API_KEY 从 .env 读取"
fi

echo "=== 8. 绑定微信 ==="
echo "运行以下命令扫码绑定微信："
echo "  cc-connect weixin setup --project stock-advisor"

echo "=== 9. 安装 systemd 服务 ==="
sudo cp "$PROJECT_DIR/deploy/stock-advisor.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable stock-advisor

echo ""
echo "=== 部署完成 ==="
echo "1. 编辑 /opt/stock-advisor/.env 填入 MINIMAX_API_KEY"
echo "2. 运行 cc-connect weixin setup 扫码绑定微信"
echo "3. sudo systemctl start stock-advisor 启动服务"
echo "4. journalctl -u stock-advisor -f 查看日志"

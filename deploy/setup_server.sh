#!/usr/bin/env bash
# 在华为云 ECS（Ubuntu 22.04/24.04）上以 root 执行
set -euo pipefail

APP_DIR=/opt/audit-platform
LOG_DIR=/var/log/audit-platform

echo "== 1. 安装依赖 =="
apt-get update -y
apt-get install -y python3.11 python3.11-venv python3-pip nginx

echo "== 2. 创建用户与目录 =="
id -u audit >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin audit
mkdir -p "$APP_DIR" "$LOG_DIR"
chown -R audit:audit "$APP_DIR" "$LOG_DIR"

echo "== 3. 配置 pip 国内镜像源（官方源在国内常超时）=="
mkdir -p /root/.config/pip
cat > /root/.config/pip/pip.conf <<'EOF'
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
extra-index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = mirrors.aliyun.com
               pypi.tuna.tsinghua.edu.cn
timeout = 120
retries = 5
EOF

echo "== 4. 创建 Python 虚拟环境 =="
python3.11 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/server/requirements.txt"

echo "== 5. 配置 systemd =="
cp "$APP_DIR/deploy/systemd/audit-platform.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable audit-platform

echo "== 6. 配置 Nginx =="
cp "$APP_DIR/deploy/nginx/audit-platform.conf" /etc/nginx/sites-available/audit-platform
ln -sf /etc/nginx/sites-available/audit-platform /etc/nginx/sites-enabled/audit-platform
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable nginx
systemctl restart nginx

echo "== 7. 启动后端 =="
systemctl start audit-platform
sleep 3
systemctl status audit-platform --no-pager

echo "== 完成 =="
echo "后端状态: systemctl status audit-platform"
echo "后端日志: tail -f $LOG_DIR/error.log"
echo "访问: http://<ECS公网IP>/"

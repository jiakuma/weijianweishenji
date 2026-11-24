#!/usr/bin/env bash
# 在 ECS 上以 root 执行：只更新代码，保留 server/data 与 server/.env
set -euo pipefail

APP_DIR=/opt/audit-platform

if [ ! -f /opt/audit-platform.tar.gz ]; then
  echo "未找到 /opt/audit-platform.tar.gz，请先上传代码包"
  exit 1
fi

echo "== 1. 备份当前配置 =="
cp "$APP_DIR/server/.env" "/opt/audit-platform.env.bak.$(date +%Y%m%d%H%M%S)"

echo "== 2. 解压代码（保留 data 与 .env）=="
tar -xzf /opt/audit-platform.tar.gz -C "$APP_DIR"

echo "== 3. 更新 Python 依赖（如有新增）=="
if [ ! -f /root/.config/pip/pip.conf ]; then
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
fi
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/server/requirements.txt" -q

echo "== 4. 重启后端 =="
systemctl restart audit-platform
sleep 3
systemctl status audit-platform --no-pager

echo "== 完成 =="
echo "前端为静态文件，解压即生效；若浏览器仍是旧版，强制刷新 Ctrl+F5"

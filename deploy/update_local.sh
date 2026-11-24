#!/usr/bin/env bash
# 本地执行：重新构建前端 + 打包代码 + 上传到 ECS（数据不重新传）
# 用法：在 Git Bash 中 cd 到项目根目录，bash deploy/update_local.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 1. 清空 dist 并重新构建前端 =="
node -e "const fs=require('fs');const p='web/dist';if(fs.existsSync(p)){for(const f of fs.readdirSync(p)){fs.rmSync(p+'/'+f,{recursive:true,force:true})}}"
( cd web && GOMEMLIMIT=2GiB GOMAXPROCS=2 npm run build )

echo "== 2. 打包代码（不含数据）=="
rm -f audit-platform.tar.gz
tar -czf audit-platform.tar.gz \
  --exclude='web/node_modules' \
  --exclude='server/.pytest_cache' \
  --exclude='server/__pycache__' \
  --exclude='server/data' \
  --exclude='.git' \
  --exclude='.idea' \
  web/dist web/package.json server deploy

echo "== 3. 上传到 ECS（按提示输密码）=="
scp audit-platform.tar.gz root@124.71.150.0:/opt/

echo "== 上传完成，去服务器执行：bash /opt/audit-platform/deploy/update_server.sh =="

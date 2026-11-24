# 审计工作平台 · 独立部署清单

目标机器：华为云 ECS，公网 IP `124.71.150.0`，Ubuntu 22.04 64bit，2vCPU / 8GiB。
架构：Nginx(80) → 静态前端 dist + 反代 /api → 后端 127.0.0.1:5281 → SQLite + Milvus Lite。

## 一、本地准备（已完成）

- 新增 `web/.env.production`，前端 API 走同源相对路径，构建产物不含 localhost:5281。
- `web/dist` 已用生产配置重新构建（2MB）。
- `server/run.py` 改为读取 APP_HOST/APP_PORT，便于后端只监听本地。
- 三个包已打包完毕：`audit-platform.tar.gz`（代码+部署文件）、`data.tar.gz`（业务数据）、`milvus.tar.gz`（向量索引）。

## 二、上传到 ECS

在本地 Windows 的 Git Bash 中执行（按提示输入 root 密码；主机名 shenji 已在 known_hosts）：

```bash
cd /e/审计工作平台
scp audit-platform.tar.gz data.tar.gz milvus.tar.gz root@124.71.150.0:/opt/
```

## 三、服务器部署（root 执行）

SSH 登录：`ssh root@124.71.150.0`

```bash
cd /opt
mkdir -p audit-platform
tar -xzf audit-platform.tar.gz -C audit-platform
tar -xzf data.tar.gz -C audit-platform/server
tar -xzf milvus.tar.gz -C audit-platform/server/data

cp audit-platform/deploy/.env.production.example audit-platform/server/.env
vim audit-platform/server/.env
```

`.env` 里必须改这三项（DeepSeek key 在数据库里，无需配置）：

```bash
JWT_SECRET_KEY=$(openssl rand -hex 32)   # 把输出填进 .env
DEFAULT_ADMIN_PASSWORD=<你的强密码>
ALLOW_USER_SWITCH=false
APP_HOST=127.0.0.1
MILVUS_URI=/opt/audit-platform/server/data/milvus.db
```

一键安装并启动：

```bash
bash audit-platform/deploy/setup_server.sh
```

## 四、开放安全组

华为云控制台 → ECS 安全组 → 入方向放行 TCP 80（和 443，若配 HTTPS）。
不要开放 5280、5281。

## 五、验收

浏览器访问 `http://124.71.150.0/`：

- 能打开登录页
- 登录后资料库列表、文档预览、知识地图正常
- 引用详情「打开原文」能直接显示原始文件
- 审计助手提问能正常流式返回
- 语义检索能命中（验证 milvus 迁移成功）

服务器侧排障命令：

```bash
systemctl status audit-platform
tail -f /var/log/audit-platform/error.log
tail -f /var/log/nginx/error.log
```

## 六、后续更新（重点）

部署完成后，日常改代码不用重新传 551M 的数据。数据只传这一次，之后只更新代码。

**本地**（Git Bash，项目根目录）：
```bash
bash deploy/update_local.sh
```
这条命令自动完成：清空并重新构建前端 → 打包代码（不含数据）→ 上传到服务器。按提示输 root 密码。

**服务器**：
```bash
bash /opt/audit-platform/deploy/update_server.sh
```
这条命令自动完成：备份当前配置 → 解压代码（不动 data 和 .env）→ 更新依赖 → 重启后端。

**两种更新要分清**：
- 改了前端/后端代码 → 用上面的更新流程，数据在服务器原地不动。
- 只是导入了新资料 → 直接在网页上用「导入资料」功能即可，不需要重新部署。

**前端更新后如果浏览器还是旧版**，强制刷新 Ctrl+F5 清缓存。

## 七、可选：HTTPS

有域名并备案后，安装 certbot，申请证书，在 `/etc/nginx/sites-available/audit-platform` 补 443 server 块并配置证书路径。当前先用 IP + HTTP 跑通。

## 八、排障记录

**pip 安装超时**（已遇到）：国内服务器直连 `files.pythonhosted.org` 会 ReadTimeout。setup_server.sh 已内置阿里云+清华镜像源配置（`/root/.config/pip/pip.conf`）。若首次部署时用的是旧脚本，手动补上该配置后重跑 pip install 即可。模型文件已随 data.tar.gz 迁移到 `server/data/models`，代码配了 hf-mirror 镜像，无需联网重新下载。

若 pip 仍慢，可换成华为云源：`index-url = https://repo.huaweicloud.com/repository/pypi/simple/`。

**Python 3.11 语法错误**（已遇到并修复）：Ubuntu 22.04 自带 Python 3.11，本地若为 3.12+，注意 f-string 表达式内不能带反斜杠（如 `f"{re.sub(r'\s+', ' ', x)}"`），3.11 会 SyntaxError。已改成 `' '.join(x.split())` 写法。

**数据库 Windows 路径导致启动崩溃**（已遇到并修复）：`documents.file_path` 存的是开发机 Windows 绝对路径，Linux 下 `Path().exists()` 抛 `OSError [Errno 36] File name too long`。首次部署后执行 `python3.11 deploy/fix_paths_server.py` 转换路径（自动备份）；代码侧 db.py 已加异常保护，坏路径不再导致启动失败。

**首次部署完整排障顺序**：pip 超时 → f-string 语法 → 数据库路径。三个问题依次暴露，每修一个才露出下一个。代码包已含全部修复。

## 九、无域名场景的安全措施（当前采用 IP+HTTP）

当前用 `http://124.71.150.0/` 访问，无 HTTPS。已做的安全加固：
- JWT_SECRET_KEY 换成 64 位随机值
- ALLOW_USER_SWITCH=false（关闭测试身份切换）
- 安全组只放行 80（SSH 22 保留给管理员）
- 首次登录强制修改管理员密码

**自动备份**（`deploy/backup_server.py`）：每天备份数据库（sqlite 在线快照）+ 向量索引，保留最近 7 份。原始上传文件导入后不再变动，已含在首次的 data.tar.gz 中，不重复备份。

安装定时任务（服务器执行）：
```bash
crontab -e
# 加这一行，每天凌晨 2:30 备份
30 2 * * * /opt/audit-platform/venv/bin/python /opt/audit-platform/deploy/backup_server.py >> /var/log/audit-platform/backup.log 2>&1
```
手动测一次：`/opt/audit-platform/venv/bin/python /opt/audit-platform/deploy/backup_server.py`，检查 `/opt/audit-platform/backups/` 下是否生成时间戳目录。

**风险提示**：HTTP 明文传输，密码在网络中可被嗅探。建议限定同事在可信网络（办公内网/VPN）内访问；若要公网长期使用，仍需域名+备案+HTTPS。有域名后按第七节配置 certbot。

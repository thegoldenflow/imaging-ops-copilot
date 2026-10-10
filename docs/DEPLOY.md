# 部署：Docker Hub → 服务器（替换旧的 smart_medical）

流程与旧项目 `smart_medical` 一致：本机构建 `linux/amd64` 镜像并推到 Docker Hub，服务器拉镜像，用 `docker compose` 起服务，宿主机的 Caddy 继续负责域名和 HTTPS。域名不变，只把 Caddy 的上游换成新服务，然后停掉旧服务。

## 架构

```
浏览器 ──HTTPS──> Caddy（宿主机，原有）──> 127.0.0.1:8080
                                            └─ ioc_web  (nginx：前端静态文件 + /api 反代)
                                                 └─ ioc_api (FastAPI，uvicorn 单 worker)
                                                      └─ ioc_db (PostgreSQL 16，数据卷 pgdata)
```

- 两个自建镜像：`imaging-ops-api`（后端 + 知识图谱数据 + evals）和 `imaging-ops-web`（前端构建产物 + nginx）；数据库用官方 `postgres:16-alpine`。
- **数据在 PostgreSQL 里**（数据卷 `imaging-ops_pgdata`）：重启容器、发版、服务器重启后数据和登录会话都保留，进行中的危急结果升级、旧片调取、提醒发送会接着做。API 启动时自动跑数据库迁移；库是空的就生成演示数据。
- **PHI 字段级加密**：患者姓名、出生日期、电话、邮箱、地址、健康卡号，以及申请单原文、外发消息、通话记录等自由文本，用 `PHI_ENCRYPTION_KEY` 加密后入库；按出生日期 / 健康卡号查人用 `PHI_BLIND_INDEX_KEY` 算的盲索引。两把密钥只在 `.env` 里，**不在数据库备份里**，要单独保管（见下文“备份”）。
- 审计日志表在数据库层禁止 UPDATE / DELETE（触发器），加上哈希链。
- API 跑 1 个 worker：后台步骤已经用行锁 / advisory lock 防重，但爽约模型、检索索引是每个进程一份的缓存，演示量没必要多开。
- 资源：API 常驻约 250MB，Postgres 约 100MB（估计），nginx 约 10MB。1GB 内存的机器够用；旧服务（Neo4j + Postgres + bge）停掉后能腾出约 1.7GB。

## A. 本机：构建并推送

在仓库根目录执行（`VER` 每次发布递增）：

```bash
VER=v0.1.0
docker build --platform linux/amd64 -f apps/api/Dockerfile -t wsyjh8/imaging-ops-api:$VER -t wsyjh8/imaging-ops-api:latest .
docker build --platform linux/amd64 -f apps/web/Dockerfile -t wsyjh8/imaging-ops-web:$VER -t wsyjh8/imaging-ops-web:latest .
docker login
docker push --all-tags wsyjh8/imaging-ops-api
docker push --all-tags wsyjh8/imaging-ops-web
```

> Docker Hub 免费账号新建的仓库默认是 **public**。镜像里没有 `.env` 和密钥（`.dockerignore` 已排除），但有完整代码。不想公开就先在 Docker Hub 网页把仓库建成 private（免费账号只有 1 个私有仓库，两个镜像需要付费账号，或者都公开）。

本机先验证（不需要推送）：`docker compose up -d --build` 后打开 http://localhost（端口可用 `WEB_PORT=8088` 改）。它会读 `apps/api/.env`。

## B. 服务器：首次部署

### 1. 目录和文件

```bash
sudo mkdir -p /opt/imaging-ops && cd /opt/imaging-ops
```

只需要两个文件：

| 文件 | 来源 |
|---|---|
| `docker-compose.prod.yml` | 从仓库根目录拷过去 |
| `.env` | 以 `apps/api/.env.example` 为模板新写一份 |

数据库不用单独装：它是 compose 里的一个容器，数据在 Docker 数据卷里。

```bash
scp docker-compose.prod.yml <用户>@<服务器>:/opt/imaging-ops/
```

### 2. 写 `.env`

```dotenv
API_IMAGE=wsyjh8/imaging-ops-api:v0.1.0
WEB_IMAGE=wsyjh8/imaging-ops-web:v0.1.0
WEB_PORT=8080

# 数据库口令（只在 compose 网络内使用，不对外暴露端口）
POSTGRES_PASSWORD=<随机长口令>

# PHI 加密密钥：在本机仓库 apps/api 下执行 `uv run python -m app.core.db.crypto` 生成，
# 或者 `openssl rand -base64 32` 生成两次。丢了密钥，库里的 PHI 就解不开了。
PHI_ENCRYPTION_KEY=<base64>
PHI_BLIND_INDEX_KEY=<base64>

# 可选：每天几点（服务器时间，0–23）自动重新生成演示数据，防止访客的操作一直累积、时间线变旧
# DEMO_DAILY_RESET_HOUR=4

# 可选：设置后登录页要先输入这个口令。不设则知道域名的人都能用（访问记录见 Caddy 日志）。
# DEMO_PASSCODE=<口令>

# AI：二选一；都不填则全部用内置 mock 输出，功能照常可演示
ANTHROPIC_API_KEY=<key>
# LLM_PROVIDER=gemini
# GOOGLE_AGENT_PLATFORM_API_KEY=<key>
```

```bash
chmod 600 .env
```

`CORS_ORIGINS` 不用设：前端和 API 同域（都走 nginx）。

### 3. 启动并自检（旧服务此时仍在跑，不受影响）

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps        # ioc_db、ioc_api 应为 healthy（首次启动要建表和生成数据，约 30 秒）
curl http://127.0.0.1:8080/api/health               # {"status":"ok","llm_mode":...}
curl -I http://127.0.0.1:8080/                      # 200
```

### 4. 切换域名（Caddy）

在 `/etc/caddy/Caddyfile` 里找到旧的站点块（`# ===== smart_medical ... =====`），**域名保持不变**，只改上游端口：

```caddy
# ===== Imaging Ops Copilot（原 smart_medical）=====
chat.example.com {
	reverse_proxy 127.0.0.1:8080

	log {
		output file /var/log/caddy/imaging-ops.access.log
		format json
	}
}
```

旧配置里的 `flush_interval -1` 是给旧的 NDJSON 流式接口用的，新服务没有流式接口，可以去掉（留着也无害）。nginx 已经做了 gzip，Caddy 这边不用再加 `encode`。

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy           # reload 不断流
curl -s https://chat.example.com/api/health
```

浏览器打开域名，选角色登录（设了 `DEMO_PASSCODE` 的话先输口令）。

### 5. 停掉旧服务

```bash
cd /opt/smart_medical
docker compose -f docker-compose.prod.yml stop
```

用 `stop` 而不是 `down -v`：容器和 Neo4j / Postgres 的数据卷都保留，随时可以回滚。确认新服务稳定一段时间后，再决定是否 `down`（仍不加 `-v`）释放容器。

### 回滚到旧服务

```bash
cd /opt/smart_medical && docker compose -f docker-compose.prod.yml start
# Caddyfile 里上游改回 127.0.0.1:8000（加回 flush_interval -1），然后
sudo systemctl reload caddy
```

## 访问记录：几点、哪个 IP

Caddy 站点块里的 `log` 会把每个请求（时间、客户端 IP、路径、状态码、浏览器）写进 `/var/log/caddy/imaging-ops.access.log`，按 100MB 自动轮转。应用自己的审计日志（“Audit log” 页）现在存在数据库里，重启不丢，但“重置演示数据”（手动或每日自动）会把它一起清空，所以访问记录仍以这个文件为准。

```bash
sudo apt install -y jq
# 每个 IP：首次、最后访问时间和请求数（按最后访问排序）
sudo jq -rs 'group_by(.request.client_ip) | map({ip: .[0].request.client_ip, first: (map(.ts) | min), last: (map(.ts) | max), n: length}) | sort_by(.last)[] | "\(.first | floor | strflocaltime("%F %T"))  ->  \(.last | floor | strflocaltime("%F %T"))  \(.ip)  \(.n) 次请求"' /var/log/caddy/imaging-ops.access.log
# 每次登录（选角色）的时间和 IP
sudo cat /var/log/caddy/imaging-ops.access.log | jq -r 'select(.request.uri=="/api/auth/login") | "\(.ts|floor|strflocaltime("%F %T"))  \(.request.client_ip)"'
```

时间按服务器时区显示；要看北京时间在命令前加 `TZ=Asia/Shanghai`。

## C. 后续更新

本机按 A 构建并推送新 tag；服务器改 `.env` 里的 `API_IMAGE` / `WEB_IMAGE`，然后：

```bash
cd /opt/imaging-ops
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

回滚就是把 tag 改回去再执行同样两条命令。重启不会重置数据；要回到初始数据，用页面上的“Reset demo”或设 `DEMO_DAILY_RESET_HOUR`。

新版本如果带数据库迁移，API 启动时自动升级表结构。升级后再回滚到旧镜像，旧代码可能读不了新表结构：发版前先按下文做一次备份，回滚时连库一起恢复。

## 备份与恢复

数据库和密钥要分开备份：库的备份里只有密文，没有 `.env` 里的两把 PHI 密钥就解不开；反过来密钥丢了，备份也没用。把 `.env` 存到密码管理器之类的地方，不要和数据库备份放在一起。

```bash
cd /opt/imaging-ops
# 备份（在线，不停服务）
docker exec ioc_db pg_dump -U ioc -d ioc -Fc > backup-$(date +%F).dump
# 恢复：先停 API，再整库覆盖，然后启动 API
docker compose -f docker-compose.prod.yml stop api
docker exec -i ioc_db pg_restore -U ioc -d ioc --clean --if-exists < backup-2026-10-07.dump
docker compose -f docker-compose.prod.yml start api
```

演示数据随时能重新生成，所以备份主要用于发版前留一个回滚点。要彻底清空（比如换了 PHI 密钥），停掉服务后删数据卷：`docker compose -f docker-compose.prod.yml down` 再 `docker volume rm imaging-ops_pgdata`，下次启动会重新建库生成数据。

**换 PHI 密钥**：密文带密钥编号（`k1:`），目前只支持一把密钥，没有在线换钥工具。演示环境换钥的办法就是上面的清空重建。

## 可选：持久化工作流（Temporal，规格 6.5）

生产环境不必开。不设 `TEMPORAL_ADDRESS` 时工作流视图显示 "offline"，其余模块照常工作。要开的话，在服务器的 compose 里照搬仓库根目录 `docker-compose.yml` 的 `temporal`（单容器 dev server，SQLite，namespace `hospital-demo`，内存约 150 MB）和 `worker`（API 同一个镜像，命令 `python -m app.workflows.worker`）两个服务，给 `api` 加 `TEMPORAL_ADDRESS=temporal:7233`。演示时可在 `.env` 里把 `WORKFLOW_SIGNOFF_TIMEOUT_S` 等计时缩短（默认签字超时 24 小时、随访 48 小时，按墙钟走，不跟模拟的医院时钟）。worker 挂掉不丢东西：待发的启动和信号留在库里的 outbox 或 Temporal 里，worker 重启后从断点继续。

## 排查

| 症状 | 原因 |
|---|---|
| `up` 报 `set API_IMAGE ...` / `set WEB_IMAGE ...` | `.env` 缺这几项 |
| 页面能开，所有接口 502 | `ioc_api` 没起来：`docker logs ioc_api` |
| `docker logs ioc_api` 里有 `PHI_ENCRYPTION_KEY is not set` | `.env` 缺 PHI 密钥（或不是 32 字节的 base64） |
| 日志里有 `InvalidTag` / 患者页面 500 | 当前 PHI 密钥和库里数据加密时用的不一致：换回原来的密钥，或按“备份与恢复”清空重建 |
| `up` 报 `set POSTGRES_PASSWORD` | `.env` 缺 `POSTGRES_PASSWORD`；改这个口令要在建库之前，建库后改需要进容器 `ALTER USER` |
| 页面右上角显示 AI: mock | `.env` 里没有可用的 API key，或 `LLM_PROVIDER` 指向的提供方没有 key |
| AI 调用超时 | 服务器出网访问 api.anthropic.com / Google 受限；可调 `LLM_TIMEOUT_S` |
| 上传胸片报 413 | 超过 nginx 的 25MB 限制（`apps/web/nginx.conf`） |
| 审计日志里 IP 全是同一个内网地址 | 代理链没有传 `X-Forwarded-For`；Caddy 默认会传，检查是否被改过 |

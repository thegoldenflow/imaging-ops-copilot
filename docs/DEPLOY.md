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
- API 跑 1 个 worker：后台步骤已经用行锁 / advisory lock 防重，但爽约模型、检索索引、行缓存是每个进程一份的缓存，演示量没必要多开。
- 全院扩展（Phase 6–8）的合成 EHR（约 6.5 万个 FHIR 资源）也在同一个 PostgreSQL 库里（表 `fhir_resources`），不需要额外服务。HAPI FHIR（compose 里的 `ehr` profile）只用于本机对照，服务器上不要开（单独就要约 1.7GB 内存）。
- 可选的持久化工作流：Temporal dev server 和 worker（`workflows` profile，见下文“可选：持久化工作流”）。不开时工作流页面显示 "offline"，其余功能照常。
- 资源（2026-10-11 本机实测，生成演示数据并打开过各页面之后）：API 常驻约 650MB（全院数据、行缓存、知识图谱、流量模型都在进程里），Postgres 单库约 150–250MB，nginx 约 10MB；开工作流再加 Temporal 约 200MB 和 worker（与 API 同一个镜像，按 API 的量估）。不开工作流至少 1.5GB 内存，开工作流至少 2.5GB；再小的机器加 swap。一次“Reset demo”或每日重置会在几十秒内把 API 的内存和 CPU 顶高。

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

# 用真 key 时务必设：每天（服务器日期）AI 花费上限，美元。到了就拒绝所有模型调用，
# AI 字段显示“暂不可用”，非 AI 功能照常；午夜清零。见下文“AI 费用控制”。
LLM_DAILY_BUDGET_USD=5
# 没人用多少分钟后暂停会调用模型的后台工作（模拟时钟、AI 摄入、旁白、医嘱点评），下一个请求恢复。0 = 不暂停
# IDLE_PAUSE_MINUTES=15
# on_demand（默认）= 没人要的 AI 不跑：后台只跑规则；某一项的 AI 在有人打开它、点 Ask AI、或自己的操作产生新工作
# （提交申请单、患者答问卷、故事线按钮）时才跑。eager = 后台提前把每一项写好（每次重置约 311 次模型调用，没人看也照付）
# BACKGROUND_AI=on_demand

# 发布门（规格 6.4 / 6.6）：demo（默认）下未通过评测的 agent 照常运行，输出带 "Not evaluated" 标记；
# prod 下这些 agent 被拒绝，模块退回规则层或人工。目前提交的评测全是 mock 基线，prod 等于关掉 AI 步骤。
# APP_MODE=demo

# 可选：持久化工作流（需要 --profile workflows，见下文）
# TEMPORAL_ADDRESS=temporal:7233
# WORKFLOW_FOLLOWUP_DELAY_S=20
```

其余变量（模型 id、FHIR、随访时长等）见 `apps/api/.env.example` 和仓库根目录 README 的 Configuration 表。

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

## AI 费用控制

用真 key 时，后台循环会自己调用模型：模拟医院的时钟一走，模拟器就写医嘱、入院和出院，每条都触发医嘱审核点评、
用药重整和 Control Tower 旁白。2026-10-12 实测 Gemini 下 API 空跑约 $4/小时。三道闸（第三道让 AI 只在用户操作时运行）：

**1. 空闲暂停（`IDLE_PAUSE_MINUTES`，默认 15）。** 连续这么多分钟没有已登录的人使用，API 暂停会调用模型的后台工作，
下一个已登录请求立即恢复：

| 空闲时暂停 | 空闲时照常 |
|---|---|
| 模拟医院时钟（恢复后从停下的地方接着走，不补空闲那段） | 消息发送（提醒、短信 / 邮件 mock） |
| 申请单 AI 摄入、患者反馈分类 | 旧片调取、危急结果升级、同行评审抽样、迎检提醒 |
| Control Tower 旁白、医嘱审核点评、用药重整、出院指导 | 每日演示重置（`DEMO_DAILY_RESET_HOUR`） |
| | 事件投递、看板刷新、用药安全摄入、随访排程（只跑规则，没有新事件时什么也不做） |
| | 持久化工作流（Temporal 的计时器按墙钟走，已开始的流程照常做完） |

- 正在跑的那一步会做完；暂停只是不再开始新的一步。积压的 AI 工作在有人回来后补上。
- “使用”= 带登录令牌的请求。前端每个请求带 `X-Idle-Seconds`（距离这个人上次动鼠标、按键、滚动或切回这个标签页的秒数），
  所以开着不动、只在轮询的标签页不会让服务器一直醒着；没有这个头的请求（脚本、curl 带令牌）按“现在有人用”算。
  患者端的公开页面（预登记、问卷）不带登录，不会唤醒。
- API 刚启动时处于暂停状态，第一个人登录后才开始跑，所以重启或发版后没人访问就不花钱。
- Control Tower 的时钟在暂停时显示 “on hold while idle”；`curl -s https://<域名>/api/health` 里的
  `background.idle_paused` 也能看到。
- `BACKGROUND_WORKERS=0` 仍然是全部关掉（包括消息发送和医院时钟），只在不需要后台时用。

**2. 每日 AI 花费上限（`LLM_DAILY_BUDGET_USD`，不设 = 不限）。** 网关每次调用模型前比较当天已花的钱和上限：

- 到了上限，所有模型调用被拒绝，模块按“AI 暂不可用”降级（规则层或交给人），非 AI 功能照常；调用日志里记一条
  `refused`，错误以 `Daily AI budget reached` 开头；API 日志里每天第一次拒绝时打一条 warning。
- 按服务器本地日期算，午夜清零（容器默认 UTC，要按本地时间就给 API 容器设 `TZ`）。
- 当天花费记在 `app_meta` 表的 `llm_spend:<日期>` 行，不是从 `llm_calls` 现算：“Reset demo” 会清空 `llm_calls`，
  如果从调用日志算，访客点一下重置就又有一整天的额度。每次调用的花费用一条原子 upsert 在独立事务里累加，
  API、Temporal worker 和评测脚本都记到同一行。
- 已经发出去的调用会做完，所以当天最多超出上限几美分。
- AI Ops 首页顶部显示“今天已花 / 上限”，到了上限变黄并显示被拒绝的调用数；`/api/health` 的 `ai_budget.reached` 也能看到。
- 参考：2026-10-12 实测 API 开着没人用时约 $4/小时（Gemini 3.5 Flash），有了空闲暂停后没人用时接近 0。
  Google Cloud 的预算只发提醒、不会停，这个上限才是真正的闸。

**3. AI 只在用户操作时运行（`BACKGROUND_AI`，默认 `on_demand`）。** 空闲暂停只管“没人在”的时候；只要有人在，`eager` 模式下后台
会把每份新数据自带的积压写完：每次 “Reset demo” 或每日重置后约 311 次模型调用（mock 实测：医嘱审核 243、用药重整 48、
Control Tower 9、申请单 9、反馈 2），访客通常只打开其中几条，而且这阵调用和访客自己的调用抢 Vertex 配额（并发下测到过 429 和卡顿）。
`on_demand` 下没人要的 AI 一次都不跑：

| | 后台只做什么 | AI 什么时候跑 |
|---|---|---|
| 医嘱审核 | 规则检查（过敏、相互作用、肾功能剂量、剂量范围、重复检查），照常进队列 | 有人打开这一条：先显示规则结果，同时显示“AI is reviewing this order…”和进度，几秒后出现点评 |
| 用药重整 | 比对出的差异和草稿 | 有人打开这一次重整（在医嘱旁边顺带显示的不算：那里显示 Ask AI） |
| Control Tower 例外 | 规则引擎的标题、事实和动作菜单；卡片上标 “AI not run · narrative when opened” | 打开抽屉时写旁白和推荐动作 |
| 申请单 | 收到即入队，标 “Waiting for the AI” | 有人提交（门户、受理表单、重新处理）时立刻跑；种子数据里的三份传真在有人打开时跑 |
| 患者反馈 | 低分评价按规则立即通知站点经理 | 患者答完问卷时立刻跑；种子里的两条在员工点 Ask AI 时跑 |
| 故事线 | — | 按某一步的 Advance，这一步的 AI（例外旁白；萘普生医嘱点评和入院用药重整）在条目出现时立刻跑 |

- 列表页从不自己跑 AI：显示“N 条已过规则、AI 未运行”，可点 “Ask AI for these N”（先说明调用次数和预计时间，最多 50 条，两条并行）。
- 页眉的 “AI activity” 列出这个浏览器每一次 AI 动作：做了什么、哪一条、谁的哪一步操作引起的、何时、多久、几次调用、花费（mock 为标价估算）；
  可切到 “Everyone since the reset”。
- 每条 AI 结果写明生成时间和耗时；失败时显示原因和 Retry，规则结果照常显示。同一条重复打开或连点不会重复调用（同进程等前一次，跨进程靠数据库锁）。
- 没有规则标记的检验 / 影像医嘱只有点 Ask AI 才写点评（两种模式都一样）。
- 代价：规则没标记、只有 AI 会提出疑虑的医嘱，不会自己出现在 “Needs action” 里；有人打开它，AI 的意见才出现。
- 开着 Temporal 时，例外工作流的 explainAndRecommend 步骤也不调用模型（步骤里写明“有人打开时写”）；审批前照样先写旁白。
- 改这个设置要重启 API。已经生成的数据保持原来的状态，“Reset demo” 或每日重置后按新设置生成。
- 仍在后台调用模型、但都是某个人的操作引起的：出院指导（有人签了出院小结）、Temporal 的旅程步骤（有人签了上一步）。空闲暂停和每日上限照样管着它们。
- 实测见 docs/PROGRESS.md “AI only on the user's click”。

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

生产环境不必开。不设 `TEMPORAL_ADDRESS` 时工作流视图显示 "offline"，其余模块照常工作，演示故事线按“离线模式”一步步推进。

`docker-compose.prod.yml` 里已经有 `workflows` profile 的两个服务：`temporal`（单容器 dev server，SQLite 存在数据卷 `temporaldata`，namespace `hospital-demo`，内存约 200MB；Web UI 只绑 127.0.0.1:8233，用 `ssh -L 8233:127.0.0.1:8233` 看）和 `worker`（与 API 同一个镜像，命令 `python -m app.workflows.worker`）。开启：

```bash
# .env 里加一行
TEMPORAL_ADDRESS=temporal:7233
# 演示时可缩短计时（默认签字超时 24 小时、升级再 24 小时、随访 48 小时、产能复核 1 小时，按墙钟走，不跟模拟的医院时钟）
WORKFLOW_FOLLOWUP_DELAY_S=20

docker compose -f docker-compose.prod.yml --profile workflows up -d
docker compose -f docker-compose.prod.yml --profile workflows ps     # ioc_temporal healthy，ioc_worker running
```

关闭：删掉 `.env` 里的 `TEMPORAL_ADDRESS`，`docker compose -f docker-compose.prod.yml up -d`（API 重建），再 `docker compose -f docker-compose.prod.yml --profile workflows stop temporal worker`。worker 挂掉不丢东西：待发的启动和信号留在库里的 outbox 或 Temporal 里，worker 重启后从断点继续。“Reset demo” 会终止正在跑的工作流。

## 端口

| 服务 | 本机开发（`docker-compose.yml`） | 服务器（`docker-compose.prod.yml`） |
|---|---|---|
| Web（nginx + 前端） | `WEB_PORT`，默认 80（`docker compose up`）；开发时 Vite 5173 | 127.0.0.1:`WEB_PORT`（示例 8080，Caddy 反代） |
| API | 8000（`uvicorn --port`，被占用就换，并设 `VITE_API_PROXY_TARGET`） | 不对外，只在 compose 网络里（nginx 反代 `/api`） |
| PostgreSQL | 127.0.0.1:5433（`POSTGRES_PORT`） | 不对外 |
| Temporal gRPC / Web UI | 127.0.0.1:7233 / 127.0.0.1:8233 | gRPC 不对外；UI 127.0.0.1:8233 |
| HAPI FHIR（可选） | 127.0.0.1:8080（`HAPI_PORT`） | 不部署 |

Windows 开发机常有被 Hyper-V 保留的端口段（`netsh interface ipv4 show excludedportrange protocol=tcp`），绑定报 `Errno 13` / `10013` 就换一个段外的端口。

## 排查

| 症状 | 原因 |
|---|---|
| `up` 报 `set API_IMAGE ...` / `set WEB_IMAGE ...` | `.env` 缺这几项 |
| 页面能开，所有接口 502 | `ioc_api` 没起来：`docker logs ioc_api` |
| `docker logs ioc_api` 里有 `PHI_ENCRYPTION_KEY is not set` | `.env` 缺 PHI 密钥（或不是 32 字节的 base64） |
| 日志里有 `InvalidTag` / 患者页面 500 | 当前 PHI 密钥和库里数据加密时用的不一致：换回原来的密钥，或按“备份与恢复”清空重建 |
| `up` 报 `set POSTGRES_PASSWORD` | `.env` 缺 `POSTGRES_PASSWORD`；改这个口令要在建库之前，建库后改需要进容器 `ALTER USER` |
| 页面右上角显示 AI: mock | `.env` 里没有可用的 API key，或 `LLM_PROVIDER` 指向的提供方没有 key |
| 所有 AI 字段都显示“暂不可用”，模型调用日志里是 `refused` / `Daily AI budget reached` | 当天花费到了 `LLM_DAILY_BUDGET_USD`，午夜（服务器时间）清零；急用就调高后 `up -d` 重建 API |
| Control Tower 时钟显示 “on hold while idle” | 超过 `IDLE_PAUSE_MINUTES` 没人用，后台 AI 工作暂停；点一下页面就恢复 |
| 打开医嘱审核、例外、申请单要等几秒才有 AI 结果 | `BACKGROUND_AI=on_demand`（默认）：AI 在有人打开时才写，页面显示进度和通常耗时。要提前写好就设 `eager`，代价是每次重置约 311 次模型调用 |
| AI 调用超时 | 服务器出网访问 api.anthropic.com / Google 受限；可调 `LLM_TIMEOUT_S` |
| 上传胸片报 413 | 超过 nginx 的 25MB 限制（`apps/web/nginx.conf`） |
| 工作流页面显示 "unreachable" | 设了 `TEMPORAL_ADDRESS` 但 `ioc_temporal` 没起来，或没加 `--profile workflows` |
| 工作流页面显示 "offline" 但想开 | API 没读到 `TEMPORAL_ADDRESS=temporal:7233`：写进 `.env` 后 `up -d` 重建 API |
| API 容器被 OOM 杀掉（`docker inspect ioc_api` 里 `OOMKilled: true`） | 内存不够：见上文“资源”，加内存或 swap；不要在小机器上开 HAPI |
| “Reset demo” 后几十秒页面很慢 | 正在重新生成全院数据，等它完成；每日自动重置（`DEMO_DAILY_RESET_HOUR`）挑没人用的时间 |
| 审计日志里 IP 全是同一个内网地址 | 代理链没有传 `X-Forwarded-For`；Caddy 默认会传，检查是否被改过 |

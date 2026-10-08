# 技术栈变更说明：补齐 PostgreSQL（含 PHI 加密）与 Temporal

状态：第一步（PostgreSQL + PHI 字段级加密）已完成（2026-10-07，分支 `feat/postgres`），第 7 节各项按本文建议执行；Temporal 暂缓（选 b），重启不丢任务由数据库轮询（`FOR UPDATE SKIP LOCKED`）满足。实现与实测见 `docs/PROGRESS.md` 的“Phase 0 completion”一节。

按 CLAUDE.md 第 5 条，改技术栈前先写明理由。本文说明为什么要把演示版的内存存储和进程内循环换回 SPEC 里的 PostgreSQL 与 Temporal、怎么换、代价多大，以及需要 owner 拍板的几件事。

## 1. 现状

演示版为了赶时间，和 SPEC 有以下偏离（见 `docs/PROGRESS.md` 开头的表）：

| 组件 | SPEC | 现在 |
| --- | --- | --- |
| 数据 | PostgreSQL + SQLAlchemy + Alembic | `app/core/store.py`：一个进程内的 `Store` 对象，启动时由带种子的生成器重建 |
| PHI 保护 | 姓名、出生日期、健康卡号、电话、地址字段级加密 | 未做（没有落盘的数据） |
| 定时与长流程 | Temporal | `app/main.py` 里两个 asyncio 循环：每 5 秒发到期消息，每 2 秒跑 6 个 `process_*` 函数 |
| 登录会话 | 未规定 | `app/core/auth.py` 里的进程内字典 |

现有数据规模（`build_store()` 实测）：约 2 千患者、1.9 万预约、2.2 千检查、2 千报告、4.3 千账单、3.9 千剂量记录、3.8 千库存流水、1.7 千审计事件，合计约 4.5 万行。核心实体 14 类，另有 43 个由各模块自己放在 `store.modules` 里的状态（报告、危急结果、补位、通话、同行评审等）。

## 2. 为什么要改

### 2.1 阶段 0 有两条验收现在过不了

- “直接查数据库时，PHI 字段是密文”：没有数据库，这条无从谈起。
- “Temporal 运行所有定时和长流程任务，服务重启不丢任务”：现在一重启，进行中的补位邀约、危急结果升级、旧片调取全部丢失，回到种子状态。

CLAUDE.md 要求按阶段顺序做、上一阶段过验收才开始下一阶段。阶段 0 是在“演示范围”这个前提下标记完成的，这两条一直是带例外通过的。

### 2.2 内存方案在部署后暴露出的问题

见 `docs/DEPLOY.md`：

- **重启即丢数据**：每次发版、容器重启或服务器重启，面试官或自己做的操作全部消失，登录也会失效。
- **只能 1 个 worker**：数据、会话、后台循环都在一个进程里；开多 worker 会各有一份数据。
- **请求和后台循环共享同一份可变对象**：后台循环在线程里跑（`asyncio.to_thread`），和请求处理同时改同一个字典和 Pydantic 对象，没有锁。演示量下没出过问题，但这是真实的并发隐患，换成数据库事务后自然消失。

### 2.3 作品集角度

SPEC 写明这是申请 AI Developer 岗位的作品链接和面试演示。医疗影像场景里，“PHI 加密落库 + 不可篡改审计 + 重启不丢的工作流”正是面试官会追问的地方。现在只能回答“演示版没做”；补齐后可以现场演示：直接查表看到密文、重启 API 后危急结果升级照常继续。

## 3. 方案

分两步，第一步独立有价值，第二步可以单独决定做不做。

### 第一步：PostgreSQL + PHI 字段级加密

**存储层**

- 新增 Postgres 16 容器；后端加 SQLAlchemy 2.x、Alembic、psycopg 3、`cryptography`。
- 14 类核心实体（`Site`、`Patient`、`Appointment`、`ImagingStudy`、`MessageOutbox` 等）建成正式表，字段沿用现有 Pydantic 模型和 FHIR 风格命名，**不增删字段**。
- 43 个模块状态分两类处理：
  - 有状态机、有到期时间的（危急结果、旧片调取、补位邀约、提醒、通话记录、报告），建正式表，方便按 `next_action_at` 查询和加索引。
  - 配置和派生缓存（权重、阈值、`phipa_cache`、`dose_by_appointment` 之类的索引），放进一张通用表 `module_state(module, key, data jsonb)`，以后需要再拆。
- 图片（`store.images`，PNG 字节）存 `bytea`，数量很少，不值得为它加对象存储。
- 登录会话进数据库表，重启和多 worker 下都有效。

**PHI 加密**

- 加密 `Patient` 的 `given_name`、`family_name`、`dob`、`phone`、`email`、`address`、`health_card`，用 AES-GCM（`cryptography` 的 `AESGCM`），密钥从环境变量 `PHI_ENCRYPTION_KEY` 读；密文带 key id 前缀，留出换钥余地。
- 密文没法直接查。电话 Agent 核身份（姓名 + 出生日期）和前台按健康卡号查人，需要另存 **盲索引**：对规范化后的值算 HMAC-SHA256（另一把密钥），存成可建索引的列，查询时比对 HMAC。
- 列表页按姓名排序和模糊搜索：2 千患者规模下，在应用层解密后排序即可；这是演示规模的取舍，会写进 PROGRESS。

**审计日志**

- 审计事件进表；数据库层撤销应用账号对该表的 `UPDATE`/`DELETE` 权限，配合现有 SHA-256 哈希链，做到“库里也只能追加”。
- 哈希链要求严格顺序：写入时取事务级 advisory lock，避免并发写入导致链分叉。

**后台任务（第一步内不引入 Temporal）**

现有 6 个后台流程其实已经是“状态 + 下次执行时间”的状态机（例如 `CriticalCase.step`、`next_action_at`、`ack_due_at`；`RetrievalTask.attempts`、`next_attempt_at`）。状态进了数据库之后，轮询循环每次从库里取到期的行处理，**重启后自然接着做**，阶段 0 的“重启不丢任务”在这一步就能满足。取任务用 `SELECT … FOR UPDATE SKIP LOCKED`，以后开多 worker 也不会重复处理。

**种子与重置**

- `uv run python -m app.seed` 一条命令建表（Alembic 迁移）并写入种子数据；“重置演示数据”按钮改为清表重灌。
- 线上演示是公开的，数据持久化后访客的操作会累积。建议加一个可选的每日自动重置（环境变量开关），默认关闭。

**测试**

- 132 个后端测试现在每个都调 `reset_store()` 重建内存数据。改成：测试会话开始时建一次库并灌种子，每个测试包在一个事务里，结束时回滚。
- 需要本地或 CI 有 Postgres；用 `docker compose` 起一个测试库，`conftest.py` 读 `TEST_DATABASE_URL`。

### 第二步：Temporal

只把 SPEC 点名的 4 类长流程迁到 Temporal：

| 流程 | 现在 | Temporal 里 |
| --- | --- | --- |
| 预约提醒（72 h / 24 h） | `dispatch_due` 每 5 秒扫 outbox | 每个预约一个工作流，`workflow.sleep` 到点发送；改约或取消时发 signal 取消 |
| 补位邀约（先确认者得） | 补位 case 状态 + 轮询 | 工作流依次发邀约、等确认 signal、超时换下一位 |
| 危急结果通知与升级 | `critical.process_due` | 通知 → 等确认 signal → 超时重通知 → 再超时升级医疗总监 |
| 旧片调取 | `priors.process_due` 带重试 | activity 调 mock 外院影像库，用 Temporal 的重试策略和超时 |

其余三个（新申请单的 AI 流水线、患者反馈分类、迎检提醒）和夜间同行评审抽样，继续用第一步的数据库轮询。前三个本质是“有新数据就处理”的队列，用 Temporal 收益很小；夜间抽样如果要迁，可以用 Temporal Schedule，但不是必须。

工作流代码放 `apps/api/app/workflows/`（SPEC 的目录结构），activity 调用现有的 service 函数，业务逻辑不重写。Temporal 不可用时，API 照常响应，只是这几类流程暂停推进；这一点会在 UI 的系统状态里显示。

**为什么第二步可以单独决定**：第一步做完后，“重启不丢任务”这条验收已经满足，Temporal 带来的是更清楚的流程可视化（Temporal UI 能看到每个工作流走到哪一步、等什么、重试几次）和面试时的技术点，代价是部署复杂度和内存。

## 4. 代价

| 项 | 第一步（Postgres + 加密） | 第二步（Temporal） |
| --- | --- | --- |
| 工作量估计 | 主要在把约 38 个文件里对 `store.*` 字典的直接读写改成仓储层调用；原地改对象字段（如 `appt.status = …`）粗查至少 22 处，都要改成显式保存 | 4 个工作流 + activity、worker 进程、测试用 Temporal 的 time-skipping 测试环境 |
| 新增服务 | `postgres:16` | `temporalio/auto-setup`（复用同一个 Postgres 的独立库）、Temporal UI（可选）、一个 worker 进程 |
| 内存（估计，需实测） | +约 100 MB | +约 400–600 MB |
| 部署 | compose 加一个服务和一个数据卷；`.env` 加数据库地址和两把密钥 | compose 再加 2–3 个服务；`DEPLOY.md` 要重写 |
| 风险 | 改动面广，回归靠现有 132 个测试和 Playwright；数据库调用变多，部分看板接口可能变慢，要量 | Temporal 首次启动慢、占内存；单机 1 GB 的服务器可能不够（旧服务停掉后腾出约 1.7 GB，需要实测） |

## 5. 不变的部分

- 核心数据模型的字段不增不删；FHIR 风格命名不变。
- 所有 API 路径、请求和响应格式不变，前端不用改（除了可能加一个“系统状态”显示）。
- LLM 调用层、de-identification、mock 外部服务不变。
- Orthanc / DICOM 不在本次范围，仍用合成 PNG。
- 本地不想起 Docker 时：本次**不保留**内存存储作为第二套后端，避免两套实现长期并存。代价是本地开发也要起 Postgres（一条 `docker compose up -d postgres`）。如果 owner 希望保留无 Docker 的启动方式，请在下面第 7 节说明。

## 6. 实施顺序

1. 在 `docs/PROGRESS.md` 列任务清单（本次作为阶段 0 的补齐项）。
2. 新分支，先加仓储层接口，让所有模块改走仓储层，此时底下仍是内存实现，跑通全部测试——这一步把“改调用方式”和“换存储”拆开，出问题容易定位。
3. 实现 Postgres 仓储、Alembic 迁移、种子命令、加密与盲索引、审计表权限，测试切到事务回滚模式。
4. 后台轮询改为从库里取到期任务；验证“重启不丢任务”。
5. 更新 compose、`.env.example`、`DEPLOY.md`、PROGRESS 的偏离表；勾选阶段 0 剩下的两条验收。单独提交。
6. （若确认第二步）加 Temporal 服务和 4 个工作流，再次验证重启场景，更新文档，单独提交。

第 2 步结束后删掉内存实现。

## 7. 需要 owner 确认

1. **是否同意第一步**：PostgreSQL + SQLAlchemy + Alembic + PHI 字段级加密（含盲索引）+ 审计表只追加。
2. **是否同意第二步 Temporal**，以及时机：
   - a. 第一步完成后紧接着做；
   - b. 先只做第一步，等确认服务器内存够、或者面试需要时再做；
   - c. 不做，PROGRESS 里写明“用数据库轮询满足重启不丢任务，Temporal 未采用”及理由。
   
   建议选 b：第一步已经满足阶段 0 的验收；Temporal 的主要收益是展示，主要代价是服务器内存，先测内存再决定。
3. **模块状态的存法**：有状态机的建正式表、配置和缓存放 `jsonb` 通用表（本文建议），还是全部建正式表。
4. **本地开发是否必须能不用 Docker 启动**（决定要不要保留内存实现）。
5. **线上演示是否要每日自动重置数据**。

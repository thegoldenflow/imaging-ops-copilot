# 开发与调试问题手册（Debugging Casebook）

这份手册收集开发和调试 Imaging Ops Copilot 时遇到的真实问题，每个问题都写清楚：看到了什么、怎么一步步查到原因、
怎么修、怎么证明修好了，以及换个项目也用得上的经验。目的是学习**怎么解决问题**，所以重点放在排查思路上，不只是结论。

- 资料来源：每个 `fix` 提交的提交说明、[engineering-notes.md](engineering-notes.md)（并发与数据库层的英文技术记录，
  细节更全）、[PROGRESS.md](PROGRESS.md) 各工作包的记录、[RATIONALE-db-temporal.md](RATIONALE-db-temporal.md)、
  模块 README 的 "Known failure modes"、2026-10-12 真实 key 测试
  （[../evals/live-run-2026-10-12.md](../evals/live-run-2026-10-12.md)），以及 2026-10-07 至 10-12 的**开发会话记录**。
- 会话记录的写法：`main__394ebe0a` 是在主工作区里的会话，`wt-wp6-med-safety-570354` 这类是在 worktree 里的会话，后面跟会话里的时间。
  **会话时间是 UTC，开发机是 UTC-4**，所以同一件事的提交时间（本地）比会话时间早 4 小时（例如会话 20:21 提交，提交说明里是 16:21）。
  阶段 0–4、Gemini 接入和临床知识问答是在云端会话里做的，没有会话记录，这些案例的"怎么查的"只能写到提交说明和 PROGRESS.md 记下的程度。
- 提交号都是 main 上合并后的历史，可以用 `git show <提交号>` 看完整改动。有几个会话里写的提交号在合并时变了：
  WP7 c16536c → a04ca7b、WP10b 11ac175 → 03be4e4、a64b261 → a454d12、3f15693 → 95c8eef、4dcdc98 → 85a4545，本手册只写后者。
- 每个案例用同一个格式：**现象 → 怎么查的 → 根因 → 怎么修 → 怎么证明 → 经验**，最后给出来源、代码和提交号。
  资料里没查清的地方照实标注：**未查明**表示原因没有找到；**推测**表示会话的判断没有经过实验证实。
- 范围标签：**imaging** = 影像中心（阶段 0–4 和它的数据层）；**hospital** = 医院扩展（WP0–WP10）；**平台** = 两边共用的代码
  （数据库层、LLM 调用层、测试 harness、Web 登录等）；**环境** = 开发机和工具；**kg-qa** = 子项目。
- 以后每修一个值得记的问题，就按这个格式加一个案例。

## 目录

- [案例索引](#案例索引)
- [一、通用方法](#一通用方法)
- [二、并发与竞态（race）](#二并发与竞态race)
- [三、真实模型（Gemini）上线问题](#三真实模型gemini上线问题)
- [四、隐私：去标识化的漏洞](#四隐私去标识化的漏洞)
- [五、和日期有关的测试：今天过、明天挂](#五和日期有关的测试今天过明天挂)
- [六、开发环境（Windows 开发机）](#六开发环境windows-开发机)
- [七、性能：先测量再优化](#七性能先测量再优化)
- [八、合成数据与种子校准](#八合成数据与种子校准)
- [九、外部服务与测试替身（HAPI、Temporal）](#九外部服务与测试替身hapitemporal)
- [十、端到端测试（Playwright）](#十端到端测试playwright)
- [十一、前端与多语言界面](#十一前端与多语言界面)
- [十二、业务逻辑与设计缺口](#十二业务逻辑与设计缺口)
- [十三、Python 与框架的坑](#十三python-与框架的坑)
- [十四、测试与评测的可信度](#十四测试与评测的可信度)
- [十五、用脚本改代码的坑](#十五用脚本改代码的坑)
- [十六、多会话并行开发](#十六多会话并行开发)
- [十七、遇到问题时的检查清单](#十七遇到问题时的检查清单)
- [十八、换模型续作：同步、合并、重试实现与真实密钥验证](#十八换模型续作同步合并重试实现与真实密钥验证)
- [附录、kg-qa 子项目](#附录kg-qa-子项目)

---

## 案例索引

范围：**imaging** = 影像中心（阶段 0–4 和它的数据层）；**hospital** = 医院扩展（WP0–WP10）；**平台** = 两边共用的代码；**环境** = 开发机和工具；**kg-qa** = 子项目。
标"速查表"的是一句话小问题的表格，标"未查明"的是原因没有找到的案例。

**只看影像中心（imaging）：**

| 影像中心的部分 | 案例 |
| --- | --- |
| 阶段 0 数据层和部署（迁到 PostgreSQL、Docker、部署文档） | 2.9、5.7、6.1、7.3、10.1、11.1、13.4、14.7、15.3 |
| 系统 4 前台电话 | 3.14、4.1、11.1、14.1、14.2、14.3 |
| 排程与候补（影像故事线） | 5.1、5.2、7.2 |
| 申请单与协议（系统 5、6） | 3.6、12.7 |
| 报告草稿 | 3.4 |
| 阶段 3 放射科运营（积压看板、同行评审） | 2.7、2.9、5.11、11.3 |
| 系统 18 账单 | 5.4、5.7、13.5、14.6 |
| 系统 19 患者反馈 | 5.5、13.5 |

**全部案例：**

| 编号 | 问题 | 范围 | 类别 |
| --- | --- | --- | --- |
| 2.1 | `functools.cache` 不是锁：worker 启动时两个线程同时建表映射 | 平台 | 并发 |
| 2.2 | 被取消的查询让连接一直"忙" | 平台 | 并发 |
| 2.3 | 重置锁只在写入时拿 | 平台 | 并发 · 锁范围 |
| 2.4 | 进程级缓存是隐藏的共享状态 | 平台 | 并发 · 共享状态 |
| 2.5 | 停 worker 时取消了还在跑的活动 | 平台 | 并发 · 测试 harness |
| 2.6 | 两个事务同时打开同一个用药核对 | hospital | 并发 · 先查后写 |
| 2.7 | 遍历共享集合时后台线程在加东西 | imaging | 并发 |
| 2.8 | 打开 Temporal 跑端到端，一次暴露三个问题 | hospital | 并发 · 死锁 |
| 2.9 | 多进程下的定时任务先查后锁 | imaging | 并发 · 先查后写 |
| 2.10 | 演示重置和后台写入死锁（`REWRITE_LOCK` 的由来） | hospital | 并发 · 死锁 |
| 2.11 | Pause 偶发不生效：人的操作撞上后台 tick | hospital | 并发 · 锁策略 |
| 2.12 | `another command is already in progress` 的第三个来源 | hospital | 并发 · Temporal |
| 2.13 | try-lock 跳过扫描，请求看到空队列 | hospital | 并发 · 锁策略 |
| 2.14 | "Ask the AI now" 卡在 pending | hospital | 并发 · 前端轮询 |
| 2.15 | 工作流在等一件已经做完的事 | hospital | 并发 · 工作流 |
| 3.1 | Gemini 拒收带大 `maxItems` 的 schema（400） | 平台 | 真实模型 |
| 3.2 | 思考 token 占满输出上限，JSON 被截断 | 平台 | 真实模型 |
| 3.3 | 504 超时：30 秒截止时间 + 默认思考 | 平台 | 真实模型 |
| 3.4 | 429 限流看起来像图片问题 | imaging | 真实模型 |
| 3.5 | AI Studio 的 key 在 Vertex 上只返回 401 | 平台 | 真实模型 · 错误信息 |
| 3.6 | 分数低，先核对标签和评分口径 | imaging | 真实模型 · 评测口径 |
| 3.7 | 空闲的 API 在花钱 | hospital | 真实模型 · 成本 |
| 3.8 | 评测报错，其实是数据库落后 4 个迁移 | 平台 | 环境 |
| 3.9 | 一个用例失败，整个评测套件中断 | hospital | 评测 harness |
| 3.10 | 合并后 5 个测试失败：未提交的真实运行记录改变了发布状态 | 平台 | 测试 · 共享状态 |
| 3.11 | 504 重试永远不会触发：重试预算被一次超时吃光 | 平台 | 真实模型 · 重试 |
| 3.12 | 文书只过 10/30：用单次失败率推算整份通过率 | hospital | 真实模型 · 可用性 |
| 3.13 | 故事线第 9 步：验证器抓到模型编造的"房颤" | hospital | 真实模型 · 幻觉 |
| 3.14 | 电话 agent 的"中文失败"其实是 MINIMAL 思考 | 平台 / imaging | 真实模型 · A/B 实验 |
| 3.15 | 重试救回 2/3，剩下的是连环卡顿 | 平台 / hospital | 真实模型 · 可用性 |
| 3.16 | 一半预算的副作用：用药两个 agent 的慢调用被 12.5 秒截断 | 平台 / hospital | 真实模型 · 超时 |
| 3.20 | 交班 SBAR 的"未记录"无处可引，代理指标重复计分 | hospital | 真实模型 · 契约 · 评测口径 |
| 4.1 | 来电者口述的出生日期没被去标识化 | imaging | 隐私 |
| 4.2 | 两个字母的姓（Hu、Li）泄露 | hospital | 隐私 |
| 4.3 | 去标识化评测满分，说明评测集太简单 | hospital | 隐私 · 评测可信度 |
| 4.4 | 占位符自己出错（`\x00-3`、嵌套占位符） | hospital | 隐私 |
| 4.5 | 两层去标识化叠加，换掉了临床日期 | hospital | 隐私 · 设计 |
| 5.1 | 周日候补找不到 Mei：首次请求要 11 秒（曾被当成负载四次） | imaging | 日期依赖 · 性能 |
| 5.2 | 周六技师的预约列表是空的 | imaging | 日期依赖 · 界面默认值 |
| 5.3 | 用药安全的预埋病例依赖星期几 | hospital | 日期依赖 · 种子 |
| 5.4 | 账单预埋差异一小时后掉出窗口 | imaging | 日期依赖 · 种子 |
| 5.5 | Westbrook 偶然不是最差；中文问卷有时不存在 | imaging | 日期依赖 · 种子 |
| 5.6 | 扫描所有星期，扫出周二的 `KeyError` | hospital | 日期依赖 · 扫描 |
| 5.7 | 种子变老：账单对账多出一串 missing | imaging | 日期依赖 · 种子 |
| 5.8 | "明早 8 点"在 7 点变成今天 8 点 | hospital | 日期依赖 · 边界 |
| 5.9 | 改了数据就按星期扫：两个模拟器边界 bug | hospital | 日期依赖 · 扫描 |
| 5.10 | 计划 horizon 差一天，时钟原地空转 | hospital | 日期依赖 · 边界 |
| 5.11 | 积压看板测试全量失败、单独通过 | imaging | 测试 · 未查明 |
| 5.12 | 周二的"编造诊断"是真的：预埋病人恰好因肺炎入院 | hospital | 日期依赖 · 测试假设 |
| 6.1 | `localhost` 建连接要 8 秒，播种像卡住 | 环境 | 环境 · 网络 |
| 6.2 | `websockets` 反复被删坏 | 环境 | 环境 · 依赖 |
| 6.3 | Docker 数据盘写满（10-09） | 环境 | 环境 · 磁盘 |
| 6.4 | 孤儿 `find` 占满 CPU 两小时 | 环境 | 环境 · 进程 |
| 6.5 | 路径太长，DLL 加载失败 | 环境 | 环境 · 路径 |
| 6.6 | Windows、shell 和进程 | 环境 | 速查表 |
| 6.7 | Docker、数据库、测试工具 | 环境 | 速查表 |
| 6.8 | 脚本输出重定向到文件时崩在 GBK 编码（SpO₂） | 环境 | 环境 · 编码 |
| 7.1 | 重置写入 INSERT 换 COPY | 平台 | 性能 |
| 7.2 | 计时测试在机器忙时失败 | 平台 | 性能 · 负载 |
| 7.3 | 迁到 PostgreSQL 后变慢：五步优化 | imaging | 性能 |
| 7.4 | 医院生成器和模拟器的性能 | hospital | 性能 |
| 7.5 | 控制塔快照 2 秒降到 0.3 秒 | hospital | 性能 |
| 7.6 | 故事线走查太慢：测量推翻两个假设 | hospital | 性能 · 负载 |
| 7.7 | COPY 后仍慢：Docker Desktop 端口转发 | 环境 | 性能 · 环境 |
| 8.1 | 生成器第一次校准：医院被挤爆 | hospital | 合成数据 |
| 8.2 | ICU 太空，看到三次才修 | hospital | 合成数据 |
| 8.3 | 术前 Task 指向错误的 id | hospital | 合成数据 · 引用 |
| 8.4 | 往返测试抓到"表示漂移" | hospital | 合成数据 · 格式转换 |
| 8.5 | 周一的预置病例 id 撞车 | hospital | 合成数据 · id |
| 8.6 | 拿统计模拟器演精确剧本 | hospital | 合成数据 · 模拟器 |
| 8.7 | 规格里的故事数字模型给不出来 | hospital | 合成数据 · 演示 |
| 8.8 | 其他数据小记 | hospital | 速查表 |
| 9.1 | HAPI 的 60 秒搜索缓存 | hospital | 外部服务 |
| 9.2 | HAPI 冒烟：R4 搜索参数；`.sh` 被检出成 CRLF | hospital | 外部服务 |
| 9.3 | 时间跳跃测试服务器不转交粘性队列 | hospital | 测试替身 |
| 9.4 | 残留的 Temporal 测试服务器（`ExitStack`） | 平台 | 测试替身 · teardown |
| 9.5 | 继承了 `TEMPORAL_ADDRESS`；worker 计数不可信 | hospital | 测试环境 |
| 9.6 | 探针工作流 `Failed decoding arguments` | hospital | Temporal |
| 9.7 | 测试里失败的 unit of work 不回滚（未修） | 平台 | 测试替身 |
| 9.8 | 其他小记 | hospital | 速查表 |
| 10.1 | 点完登录马上跳转 | imaging + 平台 | 端到端测试 · 竞态 |
| 10.2 | 计时断言算进了被测操作本身 | hospital | 端到端测试 · 计时 |
| 10.3 | 随访转写 5 秒内没出现：等响应 | hospital | 端到端测试 · 计时 |
| 10.4 | `beforeAll` 超时：满载 + 110 次全表扫描 | hospital + 平台 | 端到端测试 · 性能 |
| 10.5 | 长跑的 API 上全套越跑越慢 | 平台 | 端到端测试 · 未查明 |
| 10.6 | 测试悄悄用了真实的 Gemini key | 平台 | 端到端测试 · 环境 |
| 10.7 | 负载下写一个 75 分钟的长 e2e | hospital | 端到端测试 |
| 10.8 | Playwright 小坑 | 平台 | 速查表 |
| 11.1 | 电话组件崩溃：`useEffect` 返回了 Promise | imaging | 前端 |
| 11.2 | `/api/auth/me` 被中断就登出 | 平台 | 前端 · 错误处理 |
| 11.3 | 刚提交的同行评审从列表消失 | imaging | 前端 · 排序截断 |
| 11.4 | 中文患者指导混入英文诊断名 | hospital | 多语言 |
| 12.1 | MedRec 可以绕过工作台签终稿 | hospital | 设计 · 安全 |
| 12.2 | 一条超出 72 小时权限的记录拖垮整个队列 | hospital | 设计 · 权限 |
| 12.3 | 来电者亲属关系存反 | hospital | 设计 · 语义 |
| 12.4 | "The second one" 订成第 1 个时段 | hospital | 设计 · 规则顺序 |
| 12.5 | "截至入院时刻"的医嘱永远是 0 条 | hospital | 设计 · 时间 |
| 12.6 | 指南检索被病人背景词带偏 | hospital | 设计 · AI 检索 |
| 12.7 | 髋部骨折配成头颅 CT 或胸片协议 | imaging | 设计 · 检索覆盖 |
| 12.8 | 审计列空：审批没关联病人 | hospital | 设计 · 审计 |
| 12.9 | "最新的骨科异常"不是稳定标识 | hospital | 设计 |
| 12.10 | 架构守卫测试：真问题和误报 | hospital | 设计 · 架构测试 |
| 12.11 | 其他设计小记 | hospital | 速查表 |
| 13.1 | 函数名覆盖了模块级的锁 | hospital | Python |
| 13.2 | 循环导入只在某个入口出现 | 平台 | Python |
| 13.3 | 新代码绕开测试的注入点 | 平台 | Python · 测试 |
| 13.4 | `os.getenv` 的默认值总会先被计算 | 平台 | Python · 部署 |
| 13.5 | 加载旧模块：`holds Survey, not Survey` | imaging | Python · 对照实验 |
| 13.6 | 其他小记 | 平台 | 速查表 |
| 14.1 | 电话 agent 的模型路径从没跑过 | imaging | 评测 |
| 14.2 | 评测裁判本身也要测 | imaging + hospital | 评测 |
| 14.3 | `\b` 遇到中文：裁判误判 | imaging | 评测 · 正则 |
| 14.4 | 可溯源校验器漏看句末数字 | hospital | 评测 · 正则 |
| 14.5 | 在基线提交上跑同样的测试 | hospital + imaging | 对照实验 |
| 14.6 | 先比较旧代码对旧代码 | imaging + 平台 | 对照实验 |
| 14.7 | 换数据库后测试的对象身份假设失效 | imaging | 测试 |
| 14.8 | 结果文件被覆盖、被抹掉、没标脏 | 平台 | 评测 · 结果文件 |
| 14.9 | 其他测试与评测小记 | 平台 | 速查表 |
| 15.1 | `\b` 变成退格符 | 环境 | 脚本改代码 |
| 15.2 | heredoc 和多层转义 | 环境 | 脚本改代码 |
| 15.3 | 写给别人跑的命令先自己跑一遍 | 平台 | 脚本改代码 · 文档 |
| 16.1 | 共用工作区，测试跑到别人的代码 | 环境 | 多会话 |
| 16.2 | 共享机器上的对照实验没控制住负载 | 环境 | 多会话 · 对照实验 |
| 16.3 | 两个分支各自绿、合起来红 | 平台 | 多会话 · 合并 |
| 16.4 | LF/CRLF 让 `git merge-file` 报假冲突 | 环境 | 多会话 · 合并 |
| 16.5 | App 后台检出 worktree 失败（未完全确认） | 环境 | 多会话 · 工具 |
| 18.1 | 本地独有的 main 提交让"同步 base 分支"空转 | 平台 | 换模型续作 |
| 18.2 | 工作区不干净时安全合并并保住 WIP | 平台 | 换模型续作 |
| 18.3 | 退避重试要在调用超时内：先按测试契约 trace | 平台 | 换模型续作 |
| 18.4 | scratchpad 里的脚本 import 不到 app 包 | 平台 | 换模型续作 |
| 18.5 | 全量 run 里孤立失败的看板测试 | 平台 | 换模型续作 |
| 18.6 | Postgres/Docker 卡死时改做不依赖 DB 的因果验证 | 平台 | 换模型续作 |
| 18.7 | 截断后原样重试等于白花一倍钱 | 平台 | 换模型续作 |
| 18.8 | thinking_level 只有 Gemini 3.x 认，2.5 要 thinking_budget | 平台 | 换模型续作 |
| A.1 | 删除的历史被 checkpoint 复活 | kg-qa | 并发 |
| A.2 | 流式输出里的标记跨块 | kg-qa | 流式输出 |
| A.3 | 聊天界面的一组前端 bug | kg-qa | 前端 |
| A.4 | 其他小记 | kg-qa | 速查表 |

---

## 一、通用方法

下面这些做法是从后面一百多个案例里反复出现的模式总结出来的。

1. **先拿到准确的现象，不要凭印象。** 完整的报错原文、出现频率（"10 次里 1 次"）、出现条件（哪天的数据、
   机器是否忙、是不是只在 Windows 上）。很多问题一看条件就有方向：只在 teardown 出现、只在周日出现、只在并发时出现。
2. **让复现变便宜、变确定。** 写一个最小脚本直接打到出问题的那一层（不经过界面、不经过整个评测）；用
   `threading.Barrier` 让线程同时出发，而不是靠 `sleep` 碰运气；把"按今天播种"改成"按任意时间播种"，再一口气扫
   72 小时。自然复现太难时，就造一个确定的复现：让活动去等一把测试持有的锁，停 worker 之后再放锁（案例 2.5）。
   复现越确定，后面每次验证越快。
3. **把问题切小：二分和分层。**
   - 二分：Gemini 拒收一个 schema，就把关键字一个个去掉，看去掉哪个就不报错（案例 3.1）。
   - 分层：评测分数低，先分清是**技术故障**（400、429、504、锁超时）、**模型答错**、**标签或评分口径问题**，
     还是**环境问题**（数据库版本落后）。这几类的修法完全不同（案例 3.6、3.8）。
   - 拆时间：一个操作慢，把它拆成互相独立的几段（格式化、传输、服务器写入、读回）分别计时（案例 7.7）。
4. **提出假设，再设计一个能推翻它的实验。** 比如怀疑"思考 token 占满了输出上限"，就在调用处记下每次返回的
   `finish_reason` 和思考 token 数。数字一出来，假设就被证实或推翻了（案例 3.2）。最干脆的实验是**把怀疑的机制关掉**，
   看问题是否消失：怀疑粘性队列，就让测试 worker 不缓存工作流（案例 9.3）；怀疑自己的后台扫描，就不改代码把它关掉再跑（案例 5.1）。
5. **在正确的层修。** 6 个输出模型都带 Gemini 不接受的 `maxItems`，就在 Gemini provider 里统一处理，不去逐个改模型；
   锁拿得太久，就缩小锁的范围，而不是把超时调长；测试因为数据随星期变化而失败，就让生成器或测试不依赖星期，
   而不是挑一天跑。
6. **写"修之前失败、修之后通过"的测试，并证明它真能抓到问题。** 先在旧代码上跑一遍新测试，确认它真的会失败；
   或者故意把实现弄坏一次（把 `clone` 改成浅拷贝、把一个写错的 COPY 实现放到 `PYTHONPATH` 上、把旧的页面文件换回来），
   看测试失败，再恢复（案例 7.1、7.3、5.2）。否则测试可能根本没测到问题。裁判和断言也一样（案例 14.2）。
7. **测量，不要猜。** 锁持有多少秒、哪张表写得慢、请求在哪一步花了 11 秒，都先量出来再动手（第七章）。
   解读一个基准之前，先量一个最简单的基线（裸 `SELECT 1`）：它本身就慢，说明是机器在慢（案例 7.6）。
8. **警惕"看起来像"的结论。** 同一张胸片连续两次失败，看起来像图片的问题，单独测一次才发现是限流（案例 3.4）；
   一个 5 秒的计时测试在机器忙时失败，单独再跑就过了（案例 7.2）；电话步骤超时看起来像"数据库让回合变慢"，
   其实是前端组件崩了（案例 11.1）。
9. **先看现场，再猜原因。** 失败快照（`error-context.md`）、Playwright trace 里的网络记录、浏览器的 `pageerror` 和 console、
   服务器日志、数据库的 `pg_stat_activity`、卡住进程的调用栈（py-spy）。"找不到某条数据"和"页面还没加载完"在断言层面一模一样，
   快照一看就分开了（案例 5.1）；快照是登录页，说明是登录竞态（案例 10.1）；一个 `pageerror` 监听就能看到组件崩溃（案例 11.1）。
10. **分清新问题和老问题。** 用 `git worktree add --detach` 在干净目录检出基线提交，同一台机器、同一份数据、同样的测试，
    只换代码版本（案例 14.5）；或者把旧版文件临时放回去，在**同一份数据**上重跑失败的测试（案例 5.9）。
    先证明"旧代码也失败"，再决定修不修、在哪个会话修。
11. **在共享的机器上控制变量。** 多个会话同时跑测试时，"A 在负载下失败、B 在负载下通过"只有在当场记录了负载、
    交替紧挨着多次跑时才算数（案例 16.2）。"单独重跑能过"只说明它对负载敏感，不说明没有 bug：
    案例 5.1 的真 bug 被至少四个会话归成了"负载"。
12. **先量噪声底。** 用"新旧对比"证明修改范围之前，先让旧代码跑两次、和自己比（案例 14.6）；计时对比要交替跑几轮，
    不要分开各跑一次（案例 7.1）。
13. **满分和"0/0 达标"是警报。** 规则和评测集出自同一个人时，满分只说明两边对得上（案例 4.3）；评测 20/20 时也要读几条
    完整的转写（案例 14.1）；只有拿到真实 key 才会执行的路径，等于没测过。
14. **资源被"别人"用了，在入口加守卫让它自己交出调用栈。** 测试的连接被某个线程不拿锁就用了，就在 `Store.conn` 上加
    "不持锁就断言"，违规线程的调用栈直接指向元凶（案例 2.12）。
15. **把排查过程写进提交说明和这份手册。** 这个仓库的 `fix` 提交都写了现象、原因、修复和证据；没查清的就写"未查明"，
    推测就写"推测"，不要把猜测写成结论（案例 2.1 的措辞校正、案例 2.2 删掉没验证过的一句）。

---

## 二、并发与竞态（race）

并发问题的共同点：单独跑都正常，只有两件事同时发生才出错，而且不是每次都出错。排查的关键是**找出"哪两件事同时
发生了"**，然后想办法让它们稳定地同时发生。这一章里反复出现两个模式：**先查后写（check-then-act）**，解法是拿锁后重读；
**取消或打断落在查询中途**，解法是清理前先看连接状态。

### 2.1 `functools.cache` 不是锁

- **现象：** 独立的 Temporal worker 刚启动，有一个活动失败：
  `Table 'sites' is already defined for this MetaData instance`。只在启动时出现，偶发。最早是 WP10 的新人检查发现的：在 `D:\ioc-nc`
  的新克隆上开 Temporal、做故事线重置，两个 CapacityExceptionWorkflow 的第一个 `workflows.recordProgress` 同时运行，其中一个报这个错
  （调用链 `repo._codec(name)` → `_RowCodec(name)` → `schema.mapping(name)` → `Table(...)`）。Temporal 重试后 worker 能恢复，只是日志里一堆 traceback。
- **怎么查的：**
  1. WP10 当时已经判断"`_CODECS` 和 `schema.mapping` 是没加锁的模块级缓存，worker 有 8 个活动线程"，但不在 WP10 范围内，于是提议单独的修复会话。
     PROGRESS 当时把它和同一份日志里的 `another command is already in progress` 写成了因果关系。
  2. 修复会话先数日志：`grep -c` 显示这句在 `newcomer-worker.log` 里只出现 **1 次**，而 `another command is already in progress` 和
     `temporalio.exceptions.CancelledError: Cancelled` 出现几十次，每次都跟在 recordProgress 第 1–3 次尝试的 "Completing activity as failed" 后面。
     一次对几十次，说明日志里是**两个问题**（另一个见 2.2），不是任务说明设想的一个。
  3. 报错说同一张表被定义了两次。表定义来自 `schema.mapping`，它用 `functools.cache` 缓存，按理只建一次。再看运行方式：worker 用
     **8 个线程**跑活动，每个线程开自己的 unit of work，启动瞬间它们**同时**第一次打开 Store。
  4. 把数据库层所有模块级缓存过一遍：`_CODECS`、`_ROW_CACHE`、`crypto._cipher`、`schema.mapping`、`_engine`。`_cipher` 也是懒加载的，
     后来（5eed672 的会话里）判断两个线程同时初始化它也没有危害。
- **根因：** `functools.cache` 只缓存结果，不阻止两个线程同时计算同一个结果。两个线程同时发现缓存为空，各自建了一次
  同一张 SQLAlchemy `Table`，第二次就报"已定义"。
- **怎么修：** 每张表在锁里只建一次；模型的 import 放在锁外面（import 一个模块本身可能又需要建映射，放锁里会死锁）。
  同理，`repo._codec` 改成"谁先存进去就用谁的"（`dict.setdefault`），`get_engine` 在锁里创建引擎，避免两个线程建出两个连接池。
- **怎么证明：** 测试让 8 个线程卡在一个 barrier 后面同时出发，并故意让模型 import 变慢，把它们对齐。修之前 8 个里 7 个失败
  （`Left contains 7 more items, first extra item: InvalidRequestError("Table 'race_probe_0942e70e' is already defined ...")`），
  修之后只建出一个 codec 和一个 `Table`，单独重复跑 3 次都通过。测试每次用一个新名字的探针表（`race_probe_<8 位十六进制>`），结束时从
  `repo._CODECS` 和 `schema._MAPPINGS` 里删掉，所以不需要数据库，`--noconftest -k codec` 5 秒出结果。全套 773 passed（25 min 22 s）。
- **经验：** "缓存"和"只执行一次"是两回事。凡是"第一次用时才初始化"的东西（懒加载、单例、缓存），在多线程下都要问一句：两个线程同时第一次用会怎样？
  另外，先数一数每种报错出现几次：出现 1 次和出现几十次的两种报错，多半不是同一个原因。写结论时分清场景：生产中只有一个线程撞上，
  "8 个里 7 个"是强制对齐线程的测试里的数字，所以提交前把 PROGRESS 的 "every thread but one failed" 改成了 "one of them failed"。
- **范围：** 平台（数据库层，在 Temporal worker 上暴露）。
- **来源：** wt-wp10-roadmap-docs-6e66e7 10-11 08:51–08:52；wt-serene-liskov-4dbc74；PROGRESS WP10 "Found during the check, not fixed"、
  "WP10 follow-up · worker thread race"。
- **代码：** `apps/api/app/core/db/schema.py`、`repo.py`、`apps/api/app/core/store.py`；`apps/api/tests/test_db_threads.py`；提交 b95d2a3。

### 2.2 被取消的查询让连接一直"忙"

- **现象：** worker 日志里出现 `another command is already in progress`，接着是连接池的 `Exception during reset`。
- **怎么查的：**
  1. 这条报错的意思是：往一个还在执行查询的连接上又发了一条命令。那是谁在查询还没结束时发了命令？
  2. **任务说明的假设被推翻了。** 说明怀疑"失败的活动在 `Store.__init__` 抛异常时，把池里的连接留在了命令中途"。会话读代码发现 Store 的事务
     在第一次查询时才开始，构建失败的 Store 根本还没拿连接。为此专门写了第三个测试"建 Store 失败时不占连接"，它在修复前后都通过，作用是把这个结论记下来。
  3. 真正原因的证据链：日志里 "already in progress" 总是紧跟在 `CancelledError` 和 recordProgress 的多次尝试之后；`base.py` 里 recordProgress 的
     `start_to_close_timeout=timedelta(seconds=30)`；`activities.py` 里活动先执行 `pg_advisory_xact_lock_shared(REWRITE_LOCK)`（等重置锁）；
     再读 temporalio 的 `worker/_activity.py`：用线程池执行时给每个活动建一个 `_ThreadExceptionRaiser`，取消时用
     `Runtime._raise_in_thread(thread_id, exc)` 在活动线程里抛异常。时间线就是：一个活动等重置锁超时，Temporal 取消了它，随后 unit of work 回滚。
     API 日志里没找到能直接证明当时在重置的行（grep "reset|storyline" 只命中一条 `GET /api/storyline`），结论来自代码路径和复现测试。
- **根因：** Temporal 取消一个超时的同步活动，是在它的线程里抛 `CancelledError`。在 Windows 上，psycopg 等结果是一个
  Python 循环，所以这个异常可能正好落在**查询进行到一半**的时候。unit of work 随后往这个连接发 ROLLBACK，而连接上的查询还在跑。
- **怎么修：** `EngineSource` 先看 psycopg 的 `transaction_status`。如果是 ACTIVE（查询还在跑），就不回滚，而是在服务器端
  取消这个查询（这样它也不会继续等锁），然后把连接作废，让原始异常正常抛出来。
- **怎么证明：** 复现测试：一条连接先 `SELECT pg_advisory_lock(:key)` 模拟重置持锁，活动线程去等这把锁；用 `pg_locks` 轮询（期限 10 秒），
  看到等待者后，像 Temporal 那样往这个线程里注入异常。断言：抛出的是原来的异常；`caplog` 没有 ERROR；`engine.pool.checkedout()` 回落；
  服务器不再等锁；下一个 unit of work 能正常运行。在未修复的代码上，测试得到的错误链和 worker 日志一模一样：`_rollback_gen` → `send_query` →
  `psycopg.OperationalError: sending query failed: another command is already in progress`。修复后第一次运行**仍然失败**，原因是测试自己：
  在持锁连接 checkout 之前就记了 `checkedout()` 的基线，改成 `checked_out + 1` 后 10 passed（会话当场写明是 "test bug, not a code bug"）。
- **经验：** "取消"不是瞬间、干净的。被取消的操作可能留下半截状态（这里是一个忙着的连接）。清理代码要先检查状态，不能假设一切都已停下。
  跨平台的结论要有证据：非 Windows 上 psycopg 默认用 Cython 的 `wait_c`，注入的异常可能要等查询结束才落地；测试因此在所有平台强制用
  Python 的 `wait_select` 循环。会话查了 `wait_c` 的源码也没得出 Linux 上的结论，于是把 PROGRESS 草稿里"Linux 上活动可能跑过超时"
  这句没验证过的话删掉，只写"未检查"。同一句报错还有另外两个来源，见 2.5 和 2.12。
- **范围：** 平台（数据库层）。
- **来源：** wt-serene-liskov-4dbc74；PROGRESS "WP10 follow-up · worker thread race"。
- **代码：** `apps/api/app/core/store.py`（`EngineSource`、`_release`）；`apps/api/tests/test_db_threads.py`；提交 b95d2a3。

### 2.3 锁只在需要的那段时间拿

- **现象：** 演示数据重置时，工作流活动 `workflows.recordProgress` 超过 30 秒的 start-to-close 超时，被反复重试，直到重置结束。
- **怎么查的：**
  1. 活动在等什么？等 `REWRITE_LOCK`（重置时拿的排他 advisory lock，活动拿共享锁；这把锁的由来见 2.10）。
  2. **任务说明的前提被测量推翻了。** 说明以为重置要 1–3 分钟、主要花在生成数据上。测量脚本 measure_reset.py 在一个线程里跑重置，
     记下 build、persist、commit 各自的起止时间；另开一个 prober 连接观察锁什么时候被持有；再开一个模拟活动的 waiter，在大约第 2 秒请求共享锁。
     修复前（在已播种的测试库上）：build 17.8 秒，persist 35.5 秒，锁持有 52.6 秒，第 2.1 秒到达的 waiter 一直等到第 53.6 秒。
     （第一次运行是给空库播种，锁持有 74.0 秒、persist 55.5 秒，所以拿第二次比。）
  3. 看重置在锁里做了什么：**整个重置**都在锁里，包括在内存里生成数据、加密编码，这些根本不碰数据库。
  4. 关键的推论：只把生成挪到锁外，只能砍掉约三分之一，因为 persist 本身就超过 30 秒。于是继续拆 persist（probe_persist.py）：
     编码 5.8 秒（107,212 行、38 张表）；TRUNCATE 64 张表 1.3 秒；INSERT 共 20.9 秒，其中 `cursor.execute` 占 19.5 秒、SQLAlchemy 外层只占 1.4 秒
     （不是 ORM 的开销）；按表看 `fhir_resources` 14.0 秒（64,653 行）、`appointments` 3.0 秒。所以纯 CPU 的编码也该挪出锁。
     这组按表的数字后来直接成了 7.1 换 COPY 的起点。
  5. 检查所有调用方：admin 重置、每日重置（先拿共享锁再升级，和以前一样）、故事线重置、首次启动时的播种、`python -m app.seed`、测试 fixture。
     它们在生成期间都不需要持锁；锁是事务级的，调用方在重置之后、提交之前写的东西（审计记录、故事线 preset、`bridge.on_reset`）仍然受保护。
     还 grep 了 `global` 和没传种子的 `random.*` 调用，确认生成不依赖别的共享状态（这一步引出了 2.4）。
- **根因：** 锁的范围比需要的大得多。真正要互斥的只有"清表并写入"这一步。
- **怎么修：** 先在锁外生成数据（`build_store`）并编码（`encode_rows`：PHI 加密和 JSON），只在锁里做 TRUNCATE、写入和重置序列号。
- **怎么证明：** 修复后两次：锁持有 18.1 / 20.3 秒，waiter 等待 0.0 / 0.1 秒；故事线重置锁持有 27.6 秒，waiter 等 0.1 秒。
  两个并发重置用"生成过程中的 barrier"证明它们确实是并行生成的，第二个的等待在 `pg_locks` 里看到。`tests/test_reset_lock.py` 的三个测试，
  用 `git show b95d2a3:...` 临时换回旧的 store.py 和 repo.py 跑，全部失败（"an activity waited while the reset generated its data"、两个重置并行生成时
  barrier 报 `BrokenBarrierError`、脱离数据库的 Store 看到了进程缓存），再换回新版。
- **经验：** 拿锁之前问一句"这段代码里哪些行真的需要互斥"。准备工作（计算、编码、读配置）尽量挪到锁外。动手前先量，
  任务说明里的数字和"大头在哪"都可能是错的；推算一下"只改一处能省多少"，看够不够达标。
- **范围：** 平台（演示重置）。
- **来源：** wt-priceless-shamir-24594d；PROGRESS "WP10 follow-up · the demo reset holds its lock only while it writes"。
- **代码：** `apps/api/app/core/store.py` 的 `reset_store`；`apps/api/tests/test_reset_lock.py`；提交 5eed672。

### 2.4 进程级缓存是隐藏的共享状态

- **现象：** 这是 2.3 修复带出来的问题：把生成挪到锁外，前提是生成过程不读任何别人可能同时修改的东西。这个前提要先验证，不能假设。
- **怎么查的：** 一个很巧的办法：把 `DATABASE_URL` 指向一个关闭的端口（这样任何数据库访问都会立刻失败），并在每个引擎上挂
  SQLAlchemy 的 `before_cursor_execute` 监听器，给 `get_engine`、ambient store 和两个进程缓存都套上记录调用的包装，然后跑一次生成（probe_build.py）。
  结果 `13 recorded events`：没有执行 SQL，没用引擎、ambient store 或行缓存，**但读写了进程级缓存字典**：`_PROCESS_CACHE.get('phipa_cache')`、
  `_PROCESS_CACHE.set('phipa_cache')`、`_PROCESS_CACHE.pop('inspection_index')`。再追到使用者：`phipa.service.detect()` 读 `phipa_cache`；
  inspection 模块 pop 和 get `inspection_index`；`scheduling.noshow.get_model` 用 `noshow`；三者都在 `schema.CACHES` 里。
  （插桩下那次 build_store 用了 260.0 秒，转录里没有单独解释，正常约 18 秒。）
- **根因：** 一些模块的"缓存"状态（`noshow`、`phipa_cache`、`inspection_index`）放在进程级的 `_PROCESS_CACHE` 里，即使是脱离数据库的
  内存 Store 也用它。PHIPA 的种子数据会调用 `detect()`，审计事件数一致时直接返回缓存结果。所以同一进程里另一个 unit of work
  可能把"根据线上数据库算出的告警"喂给生成器。
- **怎么修：** 脱离数据库的 Store 用自己的缓存（`ModuleSpace._caches()`），数据库 Store 仍共享进程缓存。评测用的脱离数据库数据集也因此不再共享爽约模型和 PHIPA 结果。
- **怎么证明：** 改完后 grep `_PROCESS_CACHE|phipa_cache|inspection_index|modules\.get\("noshow"\)|get_model\(`，检查有没有代码依赖旧行为。
  再验证生成能不能并行（probe_concurrent_build.py）：单独生成 44.8 秒，两个线程同时生成 49.1 秒；38 个集合逐个比哈希，有 3 个不同
  （`module:critical_cases`、`module:feedback_surveys`、`outbox`）。这看起来像线程 bug，于是做对照：连续两次**单线程**生成，差异的字段一模一样
  （critical_cases 的 `ack_due_at`、`created_at`、`events`、`next_action_at`，以及随机的问卷 token）。说明是早就存在的非确定性（墙钟时间戳、随机 token），和并行无关（见 14.6）。
- **经验：** 全局变量、模块级字典、单例里的缓存都是共享状态，只是不显眼。验证"某段代码不碰共享状态"时，用插桩把它的所有外部访问都记下来，而不是读代码猜。
  看到"并行结果和单独运行不一样"，先跑一个"单独运行两次"的对照，确认差异是不是本来就有。
- **范围：** 平台（数据库层）。
- **来源：** wt-priceless-shamir-24594d；PROGRESS "WP10 follow-up · the demo reset holds its lock only while it writes"。
- **代码：** `apps/api/app/core/db/repo.py`；`tests/test_reset_lock.py::test_a_detached_store_keeps_its_caches_to_itself`；提交 5eed672。

### 2.5 停 worker 时取消了还在跑的活动（测试偶发失败）

- **现象：** 故事线工作流测试本身通过了，但 teardown 有时失败，fixture 回滚时报 `another command is already in progress`。
  第一次出现在 5eed672 改完后的全套运行里（775 passed、1 failed、1 error，28 分钟）：error 是
  `test_the_storylines_journey_starts_in_the_ed_and_waits_on_her_cards_and_her_bed` 在 teardown（`conftest.py:60` 的 `outer.rollback()`）报错。
- **怎么查的：**
  1. **先判断是不是自己引入的回归，用数据说话**（wt-priceless-shamir）。这个测试根本不调用 `reset_store`，不在自己改动的代码路径上。
     单独跑两个失败的测试：teardown 错误复现（2 passed, 1 error）；换回旧代码 b95d2a3 单独跑：通过，看起来像回归；新旧代码交替各跑 3 次：
     新代码 通过、通过、错误，旧代码 通过×3，会话的判断是 "suggestive but not conclusive"；在旧代码上加两个 CPU 空转进程复现：第一次就挂住了，
     还留下一个残留的测试服务器（见 9.4）；不加负载，在旧代码上再跑 6 次，每次输出写到单独的文件：第 5 次出现同样的错误。
     结论：这个错误早于本次改动。合计旧代码 10 次 1 次、新代码 6 次 3 次（含全套那次），会话明确说样本太小，不能据此断言两者频率不同，两组数字都如实报告。
  2. 当时的分析：harness 让所有活动共用测试的唯一连接（`ConnectionSource`，外面套 `DB = threading.RLock()`）；b95d2a3 加的"忙连接保护"只加在
     `EngineSource` 上，`ConnectionSource` 没有；recordProgress 没有异常处理；测试结束时工作流停在 orderReview，停 worker 的那一刻可能正好有活动在跑。
     这些写成了下一个会话的任务说明。
  3. 报错和 2.2 一样，说明又有一个查询被拦腰取消了。谁在 teardown 时取消活动？测试 harness 用 `Worker.shutdown()` 停 worker
     （wt-heuristic-allen）。读 temporalio 源码：`graceful_shutdown_timeout` **默认是 0**，也就是 `notify_shutdown` 之后立刻在所有运行中的活动线程里抛
     `CancelledError`。测试里所有活动共用测试的唯一一个连接，于是被切断的 `recordProgress` 留下一个忙连接，fixture 的 ROLLBACK 正好撞上去。
  4. 先跑 4 次基线想自然复现：全部通过（188 / 197 / 111 / 99 秒），自然复现太难，于是造一个确定的复现（见下）。
- **怎么修：** 测试 worker 停止时给 30 秒宽限期，让活动先跑完；万一还是被切断，fixture 直接丢弃这个忙连接（关闭连接会结束外层事务）。
  注意"先取消查询再回滚"行不通，因为被取消的查询结果还得先读出来。harness 也改用 `ExitStack` 逐步撤销，避免启动或停止超时后
  Temporal 测试服务器进程残留（见 9.4）。30 秒宽限期顺带也保护了"重启 worker"的测试（它中途停 worker 后继续用同一个连接）。
- **怎么证明：** 一个探针活动去等测试持有的 `_PROBE_LOCK`，worker 被要求停止并通知活动（`_STOPPING` 事件）之后，测试才放锁。
  **不改代码就能切换新旧行为**：写两个临时 pytest 插件，`grace0.py` 在 `pytest_runtest_setup` 里设 `th.GRACE = timedelta(0)`，`noguard.py`
  去掉 fixture 的忙连接保护。grace0 下新测试 3/3 失败，traceback 显示 `CancelledError` 落在 `psycopg\waiting.py:341 wait_select`，下一条语句
  （activities.py 的 `_mark`）报 `sending query and params failed: another command is already in progress`；noguard 下 fixture 保护测试在 teardown 报
  `Exception during reset or similar`；修复版通过。修复后故事线测试单独跑 8/8 通过（92–269 秒，后几次和全套并行），后端全套 778/778 两次
  （25 min 53 s；改用 ExitStack 后的最终代码 30 min 45 s）。
- **经验：** 偶发失败先看**失败发生在哪个阶段**（这里是 teardown，不是测试本身），再去查那个阶段的框架默认值。很多"玄学"问题是一个没注意到的默认参数。
  怀疑自己引入了回归时，用交替运行和计数说话；样本小就如实说"不能下结论"。用临时插件切换一个参数，可以在不改代码的前提下比较新旧行为。
- **范围：** 平台（工作流测试 harness）。
- **来源：** wt-priceless-shamir-24594d 10-11 18:30–19:22；wt-heuristic-allen-f3088b；PROGRESS 两条 WP10 follow-up。
- **代码：** `apps/api/tests/temporal_harness.py`、`apps/api/tests/conftest.py`；提交 4573b74。

### 2.6 两个事务同时打开同一个用药核对：后提交的覆盖先提交的

- **现象：** WP7 写端到端测试的准备步骤时发现：药师刚打开的用药核对会话，返回给他的 id 过一会儿就不存在了。`documentation.spec.ts` 先要签好一份
  出院用药核对（辅助函数 `signDischargeMedrec`），连续失败了几种样子：`Error: co-signature of undefined: {"detail":"No reconciliation undefined"}`；
  加重试后变成 `the pharmacist's signature on MR-00356 ... Expected: "pharmacist_signed" Received: "open"`；把签名响应打印出来，是连续 5 次
  `sign MR-00414: 404 {"detail":"No reconciliation MR-00414"}`：刚拿到的 id 不存在了。
- **怎么查的：**
  1. 对照实验：同样的"打开 → 逐项处理 → 签名"序列用脚本 `dbg.py` 直接打 API，全部 200，状态变成 `pharmacist_signed`。差别在于 e2e 是演示重置之后
     马上做，后台步骤（intake 扫描）还在跑。WP7 先在 spec 里每轮重新取"留下来的那个"核对会话绕过（3 passed, 1.4m），没有改 WP6 的代码，
     把它写进 WP7 的 Limits（"found by the e2e setup, not changed here"），建议"reconcile 里加按键的锁"，另开会话修复；
     协调会话把 WP7 的合并推迟到修复合入之后。（开修复会话时还撞上了 16.5 的 worktree 检出失败。）
  2. 打开会话有两条路：药师的请求调用 `reconcile()`，后台的 intake 扫描也会打开会话。intake 拿了自己的 "intake" 锁，`reconcile()` 却**什么锁都没拿**。
     再看写入方式：先查有没有（查不到的结果在本次 unit of work 里被 `EntityTable` 缓存），再按 upsert（`INSERT ... ON CONFLICT`）写入。
- **根因：** 典型的"先检查再写入"（check-then-act）竞态：两个事务都查到"没有"，都创建了一个会话，按同一个键 upsert，
  后提交的那个覆盖了先提交的，先拿到 id 的调用方手里的 id 就消失了。
- **怎么修：** 两条路径都拿同一把**按键的** advisory lock（`medrec:<encounter>:<kind>`，持有到事务结束），拿到锁后**重新读一次**再决定是否创建。
  这样后来的请求会等前一个事务提交，然后返回那个会话。为什么不直接用 intake 的锁？因为 `medrec_admission` 在同一事务里还要调用模型，
  用大锁会卡住整个后台扫描。也检查了不会死锁：`queue()` 和后台步骤都是先拿 "intake" 再拿键锁，请求只拿一个键锁。
- **怎么证明：** 两个新测试用辅助函数 `opened_elsewhere` 在第二个真实事务里打开同一个核对，1 秒后提交，再删掉这一行。任务说明特别提醒：这个辅助事务
  不能写 `app_meta` 的版本行（每次提交都会更新这一个全局行，辅助事务会占住另一个事务提交需要的行锁）。修复前两个测试都失败，失败形态是会话 id
  被换掉：`assert 'MR-00004' == 'MR-00003'`；修复后请求等了 1.17 秒，返回对方的会话。开销：`test_med_safety.py` 的 17 个旧测试 45.0 秒 → 38.3 秒，
  第一次 intake 扫描 3.16 秒 → 2.94 秒（在本机噪声范围内），两个新测试各要等第二个事务，共多约 5.5 秒；全套 563 通过（717 秒）。
- **经验：** 只要是"查一下没有就创建"，并发下就可能创建两次。解决办法是锁加重读（或数据库唯一约束加冲突处理）。锁的粒度要按"谁和谁需要互斥"来定：
  粒度越小，互相阻塞越少。e2e 的准备步骤"直接调 API 一切正常，紧跟在重置后就出错"，差别就在后台循环，这是找并发问题的好线索。
  同一模块更早的一次同类修改见 2.13。
- **范围：** hospital（WP6，WP7 写 e2e 时发现）。
- **来源：** wt-wp7-documentation-c03e03 10-10 04:14–04:21（发现）；wt-fix-medrec-reconcile-race 10-10 05:28–06:02（修复）；
  main__7a422c19 10-10 05:52–06:03（协调）；PROGRESS WP7 Limits、WP6 "Follow-up, a race when opening a reconciliation"。
- **代码：** `apps/api/app/modules/med_safety/service.py`；提交 84478f2。

### 2.7 遍历共享集合时，后台线程在往里加东西

- **问题：** 第 3 阶段（放射科运营）的积压看板和同行评审抽样会遍历检查和报告集合，而后台 worker 线程可能同时往这些集合里加新的检查和报告，
  相当于"边遍历边修改"（Python 遍历中的字典被修改会抛 `RuntimeError: dictionary changed size during iteration`）。
- **怎么修：** 积压和抽样代码改为遍历集合的**快照**（先复制一份再遍历）。
- **补充：** `docs/RATIONALE-db-temporal.md` 2.2 节早就把这个隐患列为换数据库的理由之一：后台循环在线程里（`asyncio.to_thread`）和请求同时改
  同一个字典和 Pydantic 对象，没有锁，"演示量下没出过问题，但这是真实的并发隐患，换成数据库事务后自然消失"。
- **经验：** 有后台线程时，任何共享集合的遍历都要问：遍历期间别人会不会改它？最简单的解法是遍历快照。
- **范围：** imaging（阶段 3 放射科运营）。
- **代码：** `apps/api/app/modules/backlog/service.py`、`apps/api/app/modules/peer_review/service.py`；提交 7392bdc（同一提交的另一处修复见 11.3）。

### 2.8 打开 Temporal 跑端到端，一次暴露三个问题

WP4c 第一次在 Temporal 开启的状态下跑端到端测试，找到三个单元测试都没覆盖到的问题（PROGRESS.md WP4c）：

1. **事件总线的 drain 和重置的 TRUNCATE 死锁。** API 的医院循环在 `_step` 之外 drain 事件总线。以前 API 进程里没有订阅者，这无所谓；
   接上 Temporal bridge 后，drain 要写表，和重置的 TRUNCATE 互相等待。修复：drain 的事务也拿重置锁的共享模式，或者在重置进行时跳过这一轮。
   - drain 死锁之前 45 分钟，同一个模式已经出现过一次（算上 WP5 的 2.10，drain 已是第三次）：在线 Playwright 日志里的 `psycopg.errors.DeadlockDetected`，用 `pg_class` 按 oid 查出两张表是
     `llm_calls` 和 `flow_exceptions`。重置持有 `REWRITE_LOCK`（排他）时 TRUNCATE 所有表，而**工作流活动根本没拿这把锁**。修复：活动的每个事务开头用
     `_work()` 共享地拿 `REWRITE_LOCK`。这次死锁拖累了当时的演示重置，表现为 `radiology-ops` 用例失败。
   - drain 那次 Postgres 给出的 DETAIL：`Process 23179 waits for AccessExclusiveLock on relation 3329188 ...; blocked by process 23343.`
     `Process 23343 waits for AccessShareLock on relation 3329178 ...; blocked by process 23179.`，两张表是 `event_consumers`（重置的
     `TRUNCATE ... RESTART IDENTITY`）和 `domain_events`。drain 写在 `_step` 外面，没有走 2.10 引入的那把重写锁。
2. **先读后锁，导致重复处理。** narrator 循环在拿锁**之前**就读出了异常列表，工作流刚叙述完的异常又被叙述一次，产生两个审阅任务。
   这是从在线 e2e 的截图 `wf-06-correction.png` 里看出来的：同一个异常有两条 Control Tower 审阅 Task。
   修复：`service.narrate` 和 `execute_actions` 拿到锁后**重新读**这条异常（`exceptions.fresh`）；为此给 `EntityTable` 加了 `forget(key)`
   （先 flush 再丢掉本事务里缓存的那一行），在拿到按异常的锁之后调用，再读就是别的事务已提交的数据；并加了回归测试。
   修复后的在线运行 27 过 1 跳过，零死锁、零服务器异常，新截图里每个异常只有一条审阅 Task。
3. **线程太多，连接池被耗尽。** 排查顺序：在线 Playwright 19 过、多个失败后，先怀疑上一次被停掉的运行留下了孤儿 Playwright runner；查进程发现
   19:39 启动的 runner 属于 WP6 会话，再查监听端口，它用自己的 5183/9421，没有冲突，排除。再逐条看真正的失败：Control Tower 用例期待 "Task task-"
   却看到 "queued for the workflow"（开了 Temporal 后批准由容量工作流异步执行，测试要给出时间）；`radiology-ops` 超时；演示重置超过 60 秒。
   最后算账：API 连接池 20 个连接，API 里跑的 worker 开 8 个活动线程，每个 unit of work 占一个连接，每个审计事件再占一个，正好能把池子耗尽。
   修复：API 内的 worker 只开 2 个线程，独立 worker 保留 8 个并用自己的连接池。

- **经验：** 并发问题常在"组件第一次真正连起来跑"时才出现。第 2 点和 2.6 是同一个模式：拿锁之后一定要重读，锁之前读到的数据可能已经过期
  （阶段 0 的定时任务就是这么做的，见 2.9）。第 1 点说明：整库重写和后台写入要用同一把重写锁隔开，而且**所有**后台写入都要走同一个入口，
  漏一个（活动、drain）就会再死锁一次。怀疑"有别人的进程干扰"时，查端口和数据库是否真的共用，"有别人的进程"不等于"有冲突"。
- **范围：** hospital（WP4c）。
- **来源：** main__025b79fc 10-09 23:16–23:17、23:45–23:47、23:59–10-10 00:31；PROGRESS WP4c Notes。

> **附：怎么测并发而不用 `sleep`**（engineering-notes.md 第 5 节）
> - 在桩函数里放一个 `threading.Barrier(2)`：如果第二个操作被锁挡住，它永远到不了 barrier，barrier 就会超时报错，用这个来断言"它们是并行的"。
> - 用 `pg_locks` 查"第二个事务正在等锁"（`NOT granted`），而不是靠计时；查询要加 `database = current_database()`，因为别的测试库也用同样的锁键
>   （这是 5eed672 的会话写测试时加上的）。
> - 每个测试写在会回滚的事务里（PostgreSQL 的 TRUNCATE 也是事务性的），共享的测试库不受影响。
> - 往线程里注入异常模拟 Temporal 的取消（2.2）；用一把测试持有的锁让活动停在指定位置（2.5）；在资源入口加"不持锁就断言"的守卫（2.12）。

### 2.9 多进程下的定时任务：先查"今天做过没有"再拿锁

- **现象：** 没有出过错，是代码审查发现的。起因：PostgreSQL 迁移时改 Dockerfile 注释，写了"数据和会话都在 PostgreSQL 里，开多个 worker 也可以"，
  于是去核对这句话是否成立。
- **怎么查的：** 读每日演示重置 `maybe_daily_reset`：先查 `store.modules.get("daily_reset") == 今天`，再 `pg_try_advisory_xact_lock`，拿到锁后
  **不再检查**就重置。读同行评审的定时抽样 `maybe_run_scheduled`：没有任何防重。
- **根因：** check-then-act。两个进程同时查到"今天还没做"，一个拿锁重置并提交、释放事务锁后，另一个再拿到锁，又重置一次；抽样则可能被两个进程各做一遍。
- **怎么修：** 新增 `Store.try_lock(key)`（事务级 advisory lock，脱离数据库的 Store 直接返回 True）；两个任务都先拿锁，**拿到锁后再读**"今天已做过"的标记。
- **怎么证明：** 转录里没有看到针对这两处的并发测试，只有全套测试通过。
- **经验：** 和 2.6、2.8 是同一个模式：锁之前读到的状态可能已经过期，拿锁之后一定要重读。写下"可以多开 worker"这种结论前，把所有定时任务挨个检查一遍。
- **范围：** imaging（系统 13 同行评审定时抽样、每日演示重置）。
- **来源：** main__394ebe0a 10-07 23:20；PROGRESS 阶段 0 "Finishing work"。
- **代码：** `apps/api/app/core/store.py`（`try_lock`）、`apps/api/app/modules/admin/service.py`、`apps/api/app/modules/peer_review/service.py`；提交 9458db0。

### 2.10 演示重置和后台写入死锁：`REWRITE_LOCK` 的由来

- **现象：** WP5 第一次跑 `e2e/control-tower.spec.ts`（API 在 9015，自己的库 `ioc_wp5`），3 个测试里 2 个失败。Playwright 报的是看板时钟没变，
  但 API 日志里有一次 `POST /api/demo/reset HTTP/1.1" 500 Internal Server Error`，traceback：
  `psycopg.errors.DeadlockDetected: deadlock detected`
  `DETAIL: Process 14351 waits for AccessExclusiveLock on relation 2021377 of database 1690615; blocked by process 14359.`
  `Process 14359 waits for RowExclusiveLock on relation 1690833 of database 1690615; blocked by process 14351.`
- **怎么查的：**
  1. 会话没有只看断言，而是去 API 日志里 grep 模拟器调用和 `demo/reset` 的状态码，发现其中一次重置是 500。它把这个 500 和时钟断言失败当成两个
     独立的问题分别处理（时钟问题见 10.2）。
  2. 读 traceback：一个进程要 `AccessExclusiveLock`（TRUNCATE 要的锁），另一个要 `RowExclusiveLock`（普通写入要的锁），两边互等，是一个锁环。
  3. 查重置怎么加锁：`app/modules/admin/service.py` 只有一个 `_RESET_LOCK`，用于 `maybe_daily_reset` 的 `try_lock`；`reset_store` 本身和后台循环之间没有任何互斥。
  4. 结论：重置 TRUNCATE 所有表的同时，WP5 新加的后台叙述（narration）在写同一批表（会话的总结是 "seen once with the narrator"）。
- **根因：** TRUNCATE 需要最强的表锁，后台步骤在各自的事务里写同样的表，两边拿锁的顺序不同，就形成了环。WP5 新增了每秒刷新的控制塔看板和
  每 2 秒一次的叙述循环，后台写入变多了。转录里只观察到一次。
- **怎么修：** `app/core/store.py` 新增 `REWRITE_LOCK = 0x5EED_DA7A`。重置在自己的事务里拿排他锁 `pg_advisory_xact_lock`；`main.py` 的每个后台 `_step`
  先试 `pg_try_advisory_xact_lock_shared`，拿不到就跳过这一轮（"Skipped while the demo data is being rewritten"）；每日重置在 `_step` 里先拿共享锁、
  在同一事务里升级。看板快照的构建同时改成 single-flight。
- **怎么证明：** 重启 API 后 control-tower.spec.ts 3/3 通过，最终全量 Playwright 25/25。没有为死锁单独写测试。
- **经验：** 整库重写（TRUNCATE）和日常后台写入用一把"重写锁"隔开：重写拿排他锁，后台拿共享锁，拿不到就让路。但这把锁只保护走它的人：
  后来工作流活动没拿它、事件总线的 drain 写在 `_step` 外面，又各死锁一次（2.8 第 1 点）；它的持有时间又在 2.3 里被缩短。断言失败时也去看服务器日志的状态码，
  里面可能藏着另一个问题。
- **范围：** hospital（WP5）。
- **来源：** main__dec77d7e 10-09 08:50–08:52；PROGRESS WP5 Simulator 一项。
- **代码：** `apps/api/app/core/store.py`、`apps/api/app/main.py`；提交 d9ffd9f。

### 2.11 Pause 偶发不生效：后台 tick 的非阻塞锁也挡住了人的操作

- **现象：** control-tower.spec.ts 跑 3 次挂 1 次。单独把 board 测试跑 4 次，3 次通过，1 次失败：点了 Pause 之后，`clock-state` 一直显示
  `"running · 15 h / min · hospital time"`，没有变成 `paused`。
- **怎么查的：** 前一轮已经修过时钟断言（10.2），这次失败在 Pause 这一步，是另一个问题。查模拟器的锁：后台每秒的 tick 在自己的事务里持有模拟器锁
  （WP3 的设计就是 "409 while another step holds the simulator lock"），人点按钮的请求如果正好撞上，`try_lock` 拿不到就直接返回 409 "busy"，
  界面上什么也不发生。这是根据代码判断的，转录里没有贴出 409 的日志。再 grep 所有测试里的 409，确认没有测试依赖这个行为。
- **根因：** 后台 tick 和人的操作用的是同一种"只尝试一次"的锁。后台拿不到锁就跳过，这是对的；但人的操作拿不到锁就失败，按钮就会随机失灵。
- **怎么修：** 人的模拟器操作（advance、run、pause、inject、快进任务的每一步）最多等 5 秒，等进行中的 tick 结束；后台 tick 仍然不阻塞。
- **怎么证明：** 新增测试 `test_a_persons_simulator_action_waits_for_a_tick_in_progress`；之后 control-tower.spec.ts 连跑 4 次全部通过；最终后端 417 passed，Playwright 25/25。
- **经验：** 同一把锁，后台任务和人发起的操作应该区别对待。后台拿不到锁就跳过；人的操作应该稍等一会儿，而不是直接报"忙"。偶发失败时，先单独把那个测试
  重复跑几次，看清楚每次具体挂在哪一步：这里前后两次失败挂在不同的步骤，是两个问题。
- **范围：** hospital（WP5）。
- **来源：** main__dec77d7e 10-09 09:25–09:38；PROGRESS WP5 Simulator 一项和测试结果。
- **代码：** `apps/api/tests/test_control_tower.py`；提交 d9ffd9f（"a person's simulator action waits for a tick in progress instead of answering 409"）。

### 2.12 同一句 `another command is already in progress` 的第三个来源：工作流被逐出时打断了本地活动

- **现象：** WP4c 开发时，容量工作流测试 `test_capacity_workflow_executes_the_approval_and_checks_the_occupancy_an_hour_later` 失败，teardown 也报错：
  `psycopg.OperationalError: sending query failed: another command is already in progress`，出错的 SQL 是 bridge 读 `event_consumers`
  （`consumer = 'workflows.bridge'`）。worker 日志里同时刷出一串
  `temporalio_sdk_core::worker::activities::local_activities: Tried to complete untracked local activity`。
- **怎么查的：**
  1. 先判断性质：测试 harness 让测试主体和 worker 的活动线程共用一个会回滚的连接，靠一把 `DB = threading.RLock()` 串行化。这个报错说明有两个线程
     同时在用这一个连接，所以问题是"谁在不拿锁的情况下用了连接"。
  2. 第一个怀疑：容量路径里某段代码自己开了线程（快照构建器）。`grep Thread|executor|to_thread|concurrent` 查 Control Tower 和几个网关，什么都没有，排除。
  3. **把违规线程抓现行**：给 harness 加一个守卫，monkeypatch `Store.conn`，worker 线程不持锁就访问连接时直接
     `AssertionError("database used without the harness lock in thread ...")`，带出调用栈。
  4. 重跑，栈指向 `app/workflows/activities.py record_progress → progress.record → _publish → ... repo.flush`，即记录时间线的**本地活动（local activity）**
     `recordProgress`。
  5. 联系上下文：为了让重启测试能跑（见 9.3），测试 worker 刚改成**不缓存工作流**（`max_cached_workflows=0`），每个工作流任务结束后工作流都会被逐出（evict）。
     逐出时 SDK 会中断该工作流还在跑的本地活动线程；日志里大量 "untracked local activity" 和这一点吻合。
- **根因：** 本地活动和工作流任务绑在同一个 worker 上，工作流被逐出时它的线程被中断，异常可能正好落在一条 SQL 执行到一半的时候，留下一个"忙"的连接；
  后面任何命令（bridge 读游标、fixture 的 ROLLBACK）都撞上它。
- **怎么修：** `recordProgress` 改成普通活动（regular activity），它本来就是幂等的，不受逐出影响。守卫保留在 harness 里，后来收窄为只对 worker 活动线程生效
  （它一开始也拦住了 TestClient 那些本来安全的请求线程）。
- **怎么证明：** 改完后 `-k "capacity or cleared or low_confidence"` 里容量测试通过（剩下 1 个失败是另一个无关的 `TypeError`，随后单独修了）；
  因为命令序列变了，之后重新录制了所有回放用的历史。PROGRESS WP4c 记下了这条设计：时间线用普通 activity，因为驱逐会打断 local activity 的线程，
  "could cut a database statement in half"。
- **经验：** 同一句报错可以有多个触发者：2.2（活动超时被取消）、2.5（停 worker 时取消活动）和这里（逐出打断本地活动），都是"某个外力在查询进行到一半时打断了线程"。
  共享资源被"别人"用了，最快的办法是在资源入口加一个断言守卫，让违规者自己把调用栈交出来。另外，一个修复（为 9.3 关掉工作流缓存）可能制造新的问题。
- **范围：** hospital（WP4c）。
- **来源：** main__025b79fc 10-09 22:44–22:53、23:21；PROGRESS WP4c Notes；`apps/api/tests/temporal_harness.py` 文档字符串。
- **代码：** `apps/api/app/workflows/base.py`（第 15–16 行写明了原因）、`apps/api/tests/temporal_harness.py`；提交 da35c54。

### 2.13 请求用"抢不到锁就跳过"，看到了一个空的待办队列

- **现象：** WP6 药师工作台的 Playwright 用例在第一步失败：`TypeError: Cannot read properties of undefined (reading 'id')`，测试在
  `/api/med-safety/queue` 返回的队列里找不到预期的 MEDA 条目。
- **怎么查的：** 转录里看不到推理过程，只看到修改的说明 "Make the intake wait for a concurrent sweep"。从最终代码可知：intake 扫描原来用
  `_try_lock`（`pg_try_advisory_xact_lock`，抢不到就放弃），API 后台循环正在扫描时，请求里的 intake 直接跳过，于是看到还没有生成审核条目的队列。
- **根因：** try-lock 适合"别人在做，我就不做了"的场景，但请求需要的是"别人做完的结果"；跳过就意味着读到旧状态。
- **怎么修：** intake 扫描改用 `_wait_lock`（`pg_advisory_xact_lock`，等锁）：请求发现后台在扫描，就等它提交，再读到它生成的审核。评论和结算仍用 try-lock。
- **怎么证明：** 重跑两个工作台 e2e 用例，2/2 通过。
- **经验：** 选锁的方式前先问"拿不到锁时，调用方需要什么"：只想避免重复工作就 try-lock，需要看到结果就等锁。同一模块后来的 2.6、2.14 是同类问题。
- **范围：** hospital（WP6）。
- **来源：** wt-wp6-med-safety-570354 10-09 23:08–23:12；`service.py` 的 `_wait_lock` 文档字符串。
- **代码：** `apps/api/app/modules/med_safety/service.py`（`_wait_lock`）；提交 3d23650。

### 2.14 "Ask the AI now"：后台循环拿着锁，点击只返回 pending，面板也不再刷新

- **现象：** 预埋病例修好之后（5.3），`med-safety.spec.ts` 的药师用例在 8.2 病例上等 AI 评论：
  `expect(review.getByTestId("ai-concern").first()).toBeVisible()` 5 秒超时。页面停在 "The AI commentary is being written" 并显示 **Ask the AI now** 按钮。
  周日播种的数据（医院日是周一）上 3 次运行失败 2 次；周五的数据上从没失败过。
- **怎么查的：**
  1. 看失败快照：评论状态是 pending，测试的分支逻辑已经点过 "Ask the AI now"。
  2. 查 API 日志：`POST /api/med-safety/reviews/ORV-00183/commentary` 返回 200，紧接着 `GET` 也是 200，没有报错。
  3. 读 `service.comment()`：先 `_try_lock(store, review.id)`（事务级 advisory lock，拿不到就"留给正在做的人"），拿不到就原样返回 review，状态仍是 pending。
     后台的 `ai_step(store, limit=2)` 一次最多处理两条评论和两条核对（每条都要调用模型），会话的判断是这一步在**一个事务**里完成，而事务级锁要到整步提交才释放
     （"holds review locks across a multi-row transaction"）。
  4. 读前端：`useReview` 没有 `refetchInterval`，拿到一次 pending 之后不会再查，直到用户重新打开。
  5. 为什么和星期有关：后台循环先处理哪条取决于当天的队列顺序（先 blocking，再按严重度和等待时间）。周一的数据里排在前面的紧急评论少，后台循环更早轮到
     naproxen 这条，正好和药师点按钮的时间撞上。所以和星期几有关，但不是数据错。
  6. 第一次碰到时（08:03）重跑通过，会话把它记为"无关的竞态、以后处理"；全套里（09:31）又失败，"2 of 3 Sunday runs"，频率太高，不能当偶发，于是回来修。
- **根因：** 两个"写同一条评论"的入口（后台循环、用户按钮）用 try-lock 互相让步，这是对的；但让步的一方（请求）返回的是"还没好"，界面却把它当成最终状态，不再轮询。
- **怎么修：** `useReview` 在评论状态为 pending 时每 2 秒重新读一次（`refetchInterval` 回调，仓库里申请单详情页已有同样写法；队列本来就每 5 秒刷新）；
  spec 给 AI 评论最多 30 秒。
- **怎么证明：** 修复后在周日数据上重跑失败的 4 个 spec，med-safety 通过；周四数据上 med-safety 3/3。没有确定性复现这个竞态的测试。
- **经验：** 用 try-lock 让步的设计，被让步的一方一定要有"之后再看一眼"的机制（轮询、推送或等锁），否则界面会卡在中间状态。一个"有时失败、和数据有关"的
  界面测试，先问"它在等的那件事，是不是被另一个后台进程拿着"。一个失败重跑通过时先记下来；第二次再出现，就要当真查。
- **范围：** hospital（WP6）。
- **来源：** wt-hopeful-ellis-36d8fc 10-11 08:02–08:05、09:31–09:42；PROGRESS WP6 "Follow-up, the review panel follows a commentary that is being written"。
- **代码：** `apps/web/src/features/med-safety/api.ts`（`useReview`）、`apps/api/app/modules/med_safety/service.py`（`comment`、`ai_step`）；提交 95c8eef。

### 2.15 工作流在等一件已经做完的事：四个"人先做完、流程后来才问"的竞态

- **现象：** WP9 故事线离线（不开 Temporal）12 步都过了。开 Temporal 后跑同一个 Playwright spec，依次卡住四次：
  1. run 1：1–6 步过了，但病人的 `InpatientJourneyWorkflow` 进度一直是 `bedAssignment running`（"Boarding in the ED until a bed is free"），而她两步之前已经入院。
  2. run 3：卡在 `proposeSchedule waiting`（"Predicted 25 min over the booking: the OR coordinator decides"），而手术协调员在第 7 步已经把预约改成了 115 分钟。
  3. run 4：第 8 步的 NEWS2 卡片 2 分钟没出现（`Expected pattern: /^Task\//`），旅程停在 `orderReview waiting`。
  4. 跑 Temporal 报告时：第 11 步卡住，旅程是 `draftDischargeSummary waiting  By hand (invalid arguments)`。
- **怎么查的：**
  1. 第一个假设："admitted 信号没送到"。读 bridge 代码，逻辑看着没问题，于是直接查数据：`workflow_waits` 有 `Encounter/stay-j0001` 的等待行，
     `domain_events` 有 `patient.admitted`，`workflow_commands` 里这条 signal 的状态是 `sent`。信号发出去了，假设被推翻。
  2. 再看 Temporal 本身：`temporal workflow describe` 显示 `Pending Activities: 0`，说明流程在等信号或计时器，**进度视图可能落后于真实状态**。
     worker 日志里的 `SubjectGone: LookupError: No exception EXC-00508` 判断为噪音（之前一次重置清掉的容量异常的工作流），之后的排查都把它过滤掉。
  3. `temporal workflow show --output json` 看历史最后 25 个事件：`journey.orderReview` 活动已完成，接着是 `TIMER_STARTED`。也就是流程在等药师签字（带超时计时器），不是在等床。
  4. 查业务数据：`order_reviews` 里她只有一条被标记的医嘱（naproxen，`resolved`，已决定）。结论：药师在旅程走到 orderReview **之前**就在工作台里做了决定，
     旅程后来创建的签字 Task 永远等不到人。这和 Temporal 无关，是步骤逻辑少了"已经做完"这个分支。
  5. 举一反三，逐个检查后面的签字步骤：MedRec 步骤已有 "already signed final" 分支；OR 的 proposeSchedule 没有（run 3 就撞上了）；WP7 文书看起来有
     （`docStatus == "final"`），但只在草稿可以原样重放时才走到。
  6. run 4 不一样：这次旅程**先**问了，药师随后决定了 naproxen，签字 Task 却仍开着。确认决定已存在、`confirm_signoff` 也执行了；再查时间：签字 Task
     `task-03867` 的写入时间是 `04:28:20+00:00`，药师决定的审计时间是本地 `00:28:19`。两边相差 1 秒，各自读到的是对方提交之前的状态：
     后台去找 Task 时 Task 还没提交，步骤查决定时决定还没提交。
  7. 第 11 步：第 9 步医生在文书页里**编辑**了出院小结（删掉 lisinopril）再签字；旅程的步骤之后想重放草稿，草稿工具拒绝已 final 的文档（'invalid arguments'），
     于是退回成一个手工 Task，去等一个早已给出的签名。已有测试只覆盖"草稿没改就签"，编辑改变了重放参数，所以没测到。
- **根因：** 工作流步骤假定"流程先问、人后答"。故事线里人走在流程前面（旅程还在等床，药师、协调员、医生已经在各自页面完成了工作），步骤在创建等待之前
  不检查"这件事是否已经做完"。第 3 种是真正的并发窗口：API 和 worker 两个事务各自"先查再写"，都基于对方提交前的快照（和 2.6 同一模式，只是跨进程）。
- **怎么修：**
  - orderReview：`med_safety.journey.decided_before`，这次住院所有被标记的用药医嘱都已决定时，记录这些决定，不再开 Task；没有被标记医嘱的住院仍开确认 Task。
  - proposeSchedule：先重读预约（通过 Control Tower 的 gateway 读，因为 `workflow_engine` 的数据范围不含 Appointment），协调员已经约到预测时长就记录下来，不开 Task；
    Task 开着时协调员调整预约也算批准（`control_tower.service.adjust_booking`）。
  - WP7：`documentation.journey._signed` 起草前先找已签 final 的文档。
  - 1 秒窗口：`recheck_signoffs`，药师做出决定后 2 分钟内，API 后台循环每 5 秒再跑一次 `confirm_signoff`（`RECHECK_S, RECHECK_EVERY_S = 120.0, 5.0`），
    以最后做决定的药师的名义关闭晚到的签字 Task。记录里只说这样做 "cheaply"，没有讨论为什么不用 2.6 那样的锁。
- **怎么证明：** 新测试 `test_a_journey_signoff_written_while_the_last_decision_was_saved_is_confirmed_soon_after`（先决定最后一条标记医嘱，再写签字 Task，
  再看 recheck 把它关掉）；`test_documentation.py` 加了"编辑后签名"的用例（12 passed）。Temporal run 5 整条通过，旅程 `journey-stay-j0001` completed（26.9 分钟）；
  Temporal 报告 12/12、旅程 14/14；最终代码上 Temporal e2e 15.0 分钟通过。
- **经验：** 凡是"创建一个任务等人去做 X"，先查 X 是否已经做完，人不会按流程图的顺序操作。修好一个步骤后，把同类步骤逐个过一遍（这里一次找出 4 个）。
  进度视图可能落后，要以引擎自己的历史为准。两个事务"各自查、各自写"时可能都看不到对方：要么共用一把锁加重读（2.6），要么像这里事后再补查一次。
  值得一提：写离线测试时就已预见"旅程里有故事线没覆盖到的签字点，可能会卡住"，但具体是哪几个，要真跑起来才知道。
- **范围：** hospital（WP9，牵涉 WP6 用药安全、Control Tower 手术排程、WP7 文书）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 03:53–05:52；PROGRESS WP9 "WP6 in the journey, a race found in the online run"。
- **代码：** `apps/api/app/modules/med_safety/journey.py`（`decided_before`、`recheck_signoffs`）、`apps/api/app/modules/documentation/journey.py`（`_signed`）、
  `apps/api/app/modules/control_tower/service.py`（`adjust_booking`）；提交 8d3cd5e。

---

## 三、真实模型（Gemini）上线问题

这一章来自 2026-10-12 第一次用真实 key（Gemini 3.5 Flash，经 Vertex AI）跑完整套件。之前所有评测都用 mock 模型，
mock 的输出是作者写的，永远符合 schema、永远秒回。所以下面这些问题**只有换成真实模型才会出现**。

### 3.1 400 INVALID_ARGUMENT：Gemini 拒收带大 `maxItems` 的 schema

- **现象：** `medication_reconciliation` 每次调用都失败：`Agent Platform 400 request failed. Google: Request contains an invalid argument.`，
  约 1 秒返回，消耗 0 个 token。别的 agent 都正常。
- **怎么查的：**
  1. **0 token、1 秒**说明请求在模型运行之前就被 Google 拒了，问题在请求本身，不在模型的回答。
  2. 同一个 provider 其他 agent 都正常，所以差别在这个 agent 独有的东西上，最可能是它的输出 schema。
  3. 把 schema 打出来，有 `maxItems`、`maxLength`、`minimum/maximum` 几种约束。写一个小脚本**直接调用 provider**（绕过整个应用），
     每次去掉一类关键字（二分）：
     - 原样发送：400
     - 去掉 `maxLength`：400；去掉 `minimum/maximum`：400
     - 去掉全部：成功
     - 只保留 `maxItems`：400；只保留 `maxLength`：成功；只保留 min/max：成功
     - `maxItems` 从 40 改成 10：成功
  4. 结论：Vertex AI 拒绝**对象数组上较大的 `maxItems`**（40、60 不行，10 可以）。
  5. 扫描全部 schema，找出同样带大 `maxItems` 的：除了用药核对，还有 3 个文书 agent（出院小结、患者指导、护理交接）也受影响，评测还没跑到它们就已经知道了。
- **根因：** Gemini 的结构化输出只支持 JSON Schema 的一个子集，较大的数组长度限制会被拒绝（PROGRESS.md 里早就提醒过这个风险，但没有真实调用验证过）。
- **怎么修：** 在 `GeminiProvider` 里发送前把 `maxItems` 去掉（`gemini_schema()`）。这样不会失去约束：网关收到回答后仍然用**完整的 Pydantic 模型**校验，
  列表超长会校验失败并重试一次。修在 provider 这一层，因为这是 Gemini 的限制，不该为它逐个改 6 个输出模型，也不该影响 Claude。
- **怎么证明：** 单元测试（假客户端）检查发出去的 schema 没有 `maxItems`，并且超长回答仍被网关拒绝；真实调用中受影响的 5 个 schema 全部从 400 变成合法回答；
  故事线第 6 步（入院用药核对）在真实模型上通过。
- **经验：** 先用"耗时和 token 数"判断错误发生在哪一层；把复现脚本直接打到最底层（provider），每次只改一个变量；
  在约束真正生效的地方保留约束（服务端校验），对外部 API 做兼容性裁剪。
- **范围：** 平台（LLM 调用层）。
- **代码：** `apps/api/app/llm/providers.py`（`gemini_schema`）；`apps/api/tests/test_gemini.py`；提交 434acf0。

### 3.2 思考 token 占满输出上限，JSON 被截断

- **现象：** 修好 3.1 后，文书评测仍然在第 1 个病例中断，这次是 `model needs_human (synthesis)`：综合（synthesis）这一步的回答两次校验都失败。
- **怎么查的：**
  1. `needs_human` 表示两次都没通过校验，可能是 JSON 坏了、字段不对，或者模型拒答。先看网关记录的错误：
     `Invalid JSON: Unterminated string starting at: line 1 column 931`，JSON 在中间断掉了。
  2. 假设：**输出被截断了**。为什么会截断？Gemini 3 默认会"思考"，而思考 token 计入 `max_output_tokens`（这里是 8000）。
  3. 设计验证实验：复现这个病例，并在 provider 的调用处记下每次返回的 `finish_reason`、可见输出 token 和思考 token。结果：
     - 前 5 次分段调用：`STOP`，思考 829–4,400 token；
     - 综合调用：**`MAX_TOKENS`，思考 7,676 token，可见输出只有 309 token**，两次都一样。
  4. 假设证实。再做一个对照：同一病例把思考等级设成 `LOW`，6 次调用全部 `STOP`，综合输出 2,440 token，校验器 20/20 条可溯源，每次 3–9 秒。
- **根因：** 模型把输出预算几乎全花在思考上，留给答案的不够。应用没有设置思考等级（PROGRESS.md 把"没有调思考等级"列为已知限制，但没意识到它会导致截断）。
- **怎么修：** 这个改动会影响所有 Gemini agent 的行为，需要负责人决定，所以先记录证据、提出方案：按模型档位设思考等级
  （推理和快速档 LOW、语音档 MINIMAL），把 `MAX_TOKENS` 作为单独的错误报出来（而不是让它表现为"JSON 无效"），然后重跑真实评测。
- **经验：** "JSON 无效"只是表面现象，要追问"为什么无效"。把模型返回的元数据（结束原因、各类 token 数）记下来，通常一眼就能看到原因。
  同时注意，**同一个配置问题可能有多个症状**：这里的默认思考同时造成了截断（3.2）、超时（3.3）和电话 agent 慢（4.7 秒，目标 1.5 秒）。
- **范围：** 平台（LLM 调用层，影响所有 Gemini agent）。

### 3.3 504 超时：30 秒截止时间 + 默认思考

- **现象：** 全量评测约 800 次请求里约 9% 返回 `504 DEADLINE_EXCEEDED`，语音评测里有 15–18%。分诊评测 10 个失败里有 4 个是超时，根本没有答案。
- **怎么查的：** 按套件统计每种 HTTP 状态码的次数（日志里每次请求都有一行）。超时集中在输出长、推理重的调用上；p95 延迟在 33–39 秒，
  正好卡在"30 秒超时加一次重试"附近。SDK 把应用的超时时间作为服务器端截止时间发过去，所以是 Google 返回 504。
- **根因：** 和 3.2 同源：默认思考让单次调用经常超过 30 秒。
- **怎么修：** 同 3.2 的方案（降低思考等级），必要时调大 `LLM_TIMEOUT_S`。
- **经验：** 算评测分数时要把"超时没答"和"答错了"分开统计：分诊 0.80 看起来没达标（门槛 0.85），去掉 4 个超时后其实是 40/46 = 0.87。
- **范围：** 平台（LLM 调用层）。

### 3.4 429 限流：看起来像图片问题，其实不是

- **现象：** 故事线病人的胸片（ST-DEMO1）草稿连续两次失败，提示 "AI is temporarily unavailable"，而其他胸片都成功。
- **怎么查的：**
  1. 同一个检查连续失败两次，很容易怀疑是这张图片的问题（太大？格式？）。
  2. 先查调用日志：两次都是 `429 RESOURCE_EXHAUSTED`（限流），不是图片错误。
  3. 再做排除实验：单独直接调用模型，ST-DEMO1 发两次、另一张发一次。结果全部成功，两张图片都是 512×512、200 KB，
     模型正确识别出右上肺的孤立结节。
  4. 回看两次失败的时间：都发生在有**另外两三个进程同时调用 Gemini**的时候（评测加 API 后台 agent）。
- **根因：** 并发调用触发 Vertex 限流，而 SDK 只重试一次、间隔 1–2 秒，扛不过去。两次都落在同一张图上是巧合。
- **怎么修：** 建议对 429 和 504 做指数退避重试（SDK 的 `HttpRetryOptions` 支持次数和间隔）；跑真实评测时一次只跑一个进程。
- **经验：** "总是同一个东西失败"不一定是那个东西的问题，先看错误类型，再做一个**只改一个条件**的对照实验去证伪。
- **范围：** imaging（报告草稿）。

### 3.5 AI Studio 的 key 在 Vertex 上只返回一个光秃秃的 401

- **现象：** 早期接 Gemini 时，配了 `GEMINI_API_KEY` 却只得到 401，看不出原因。
- **根因：** Google AI Studio 的 key 和 Vertex AI（Agent Platform）的 key 不是一回事，前者在 Vertex 端点上无效。而 provider 只记录了状态码，
  丢掉了 Google 返回的错误原文。
- **怎么修：** 错误信息里附上 Google 自己的说明（它会写明真正原因）；不再把 `GEMINI_API_KEY` 当 Vertex key 用，只设它时启动日志给出警告。
- **经验：** 包装第三方错误时**保留原始错误文本**。"请求失败"这种概括会把唯一有用的线索扔掉。
- **范围：** 平台（LLM 调用层）。
- **代码：** `apps/api/app/llm/providers.py`、`apps/api/app/core/config.py`；提交 2df8338。

### 3.6 分数低不等于模型差：先核对标签和评分口径

- **现象：** 申请单抽取只有 0.44（门槛 0.75），几乎全错在 `prior_imaging`（既往影像）字段，而且没有一次调用失败。
- **怎么查的：**
  1. 失败用例的共同点：原文都有 "Previous imaging: None known"。
  2. 看评分代码：只判断"抽出的列表是否为空"是否等于"标签是否有外院名称"。
  3. 直接调用两条看实际输出：
     - req-00 抽出了 "chest X-ray"，因为临床信息里写着 "seen on recent chest X-ray"；
     - req-07 抽出了临床信息里的 "normal CT in emergency"，**还把字面的 "None known" 也当成一条**。
- **根因：** 两个原因混在一起。一是**口径不一致**：规格里这个字段是"提到的既往影像"，标签却只算"外院的既往检查"（用于调片），
  Gemini 的理解其实说得通；二是**模型真错**："None known" 不应该成为一条记录。
- **怎么修：** 需要负责人先定这个字段到底是什么意思，再同时改 prompt 和标签（"None known" 应得到空列表）。
- **补充：** 同类的口径问题更早就出现过：WP4c 的分诊回归 11/12，失败的是一条停车相关消息，prompt 的定义标成 "other"，mock 标成 "administrative"（PROGRESS WP4c）。
- **经验：** 评测分数低时，先看几条失败用例的**原始输入和模型原始输出**，再看评分代码，最后才下"模型不行"的结论。
- **范围：** imaging（申请单抽取）。

### 3.7 空闲的 API 在花钱

- **现象：** 只是把 API 开着、什么都不操作，日志里 Gemini 调用不断增加：12 分钟 19 次。
- **怎么查的：** 查 API 自己的调用日志表（`llm_calls`），按任务分组：`order_review` 12 分钟 18 次、$0.77；`medication_reconciliation` 失败后约每分钟重试一次。
- **根因：** 医院模拟会持续产生新的医嘱，每条都由 AI 审核；失败的核对会被后台循环反复重试。用 mock 时这不花钱，换成真实 key 就是每小时约 $4。
- **怎么修：** 测试时不用就关掉 API。规格要求公开部署要有访问口令和 AI 调用限额，限额目前还没做。
- **经验：** 从 mock 换到真实服务时，要盘点所有**自动**触发的调用（定时任务、后台循环、重试），它们才是成本的大头。
  另一个悄悄花钱的入口是测试本身：Playwright 和本地容器读了开发者 `.env` 里的真实 key（10.6）。
- **范围：** hospital（医嘱审核、用药核对的后台循环）。

### 3.8 评测报错，其实是数据库落后了 4 个迁移

- **现象：** 全量真实评测里，控制塔、用药核对、医嘱审核和 3 个文书 agent 全部报错：`relation "flow_exceptions" does not exist`。
- **怎么查的：** 表不存在，先查数据库版本：开发库 `ioc` 的 `alembic_version` 是 0006，而代码有 0007–0010 四个迁移，这些表都在后面的迁移里。
- **根因：** 评测默认连开发库，而开发库很久没跑过迁移。这些报错和模型无关，也没花钱。为什么会"很久没迁移"：WP4 会话就记录过，开发库是被
  Playwright 运行顺带迁移和重置的（"The dev database has already been migrated and reset by the Playwright runs"），所以开发库是不是最新，
  取决于上次在它上面跑 Playwright 是什么时候。
- **怎么修：** 新建一个数据库，让 API 启动时自动迁移并播种，再在上面重跑这些套件。
- **经验：** 报错先分清是**代码或模型问题**还是**环境问题**。`relation ... does not exist` 这类错误第一步就该看迁移版本。
  另外，评测进程连哪个库，要连那个库的 PHI 密钥（见第六章 `InvalidTag` 一行）。
- **范围：** 平台（评测与开发库）。

### 3.9 一个用例失败，整个评测套件就中断

- **现象：** 2026-10-12 真实 key 跑文书评测，第 1 个病例的出院小结合成调用超时（把 `LLM_TIMEOUT_S` 设到 90 秒后，Gemini 停在 `MAX_TOKENS`），`discharge_summary`、`patient_instructions`、`nursing_handoff` 3 个 agent 在评测报告里全部记为 `error`，30 个病例一个分数都没有。
- **怎么查的：**
  1. 看报错：`DocProblem: The AI draft could not be written (model unavailable (synthesis)); use the template`。这是产品代码 `draft_summary` 在模型不可用时主动抛的（提示医生改用模板），本身是对的。
  2. 假设异常从按病例的循环里冒出来、没人接。读 `app/modules/documentation/evals.py` 的 `run()`，确认循环里 `docs.draft_summary(...)` 没有 try/except；异常一路冒到 `app/aiops/evalrun.py` `run_suite()` 顶层的 `except Exception`，那里把套件里**每个** agent 都记成 `error`——所以一个调用失败，3 个 agent、30 个病例全没了。
  3. 加测试复现：mock provider 只在第 1 个病例（`ev-doc-02-stay`）的 `SYNTHESIS` 调用抛 `ProviderUnavailable`，跑整套 `run_suite`，确认未修时整套 error。
  4. 顺手读其它套件的 harness，确认只有文书评测会这样（见“经验”）。
- **根因：** 评测 harness 把“每个病例、每份文书”当成一个不可分割的整体跑，任何一步抛 `DocProblem` 都会让整套评测挂掉；而真实模型本来就会偶发不可用/超时。
- **怎么修：** `run()` 改成把每个病例的三份文书各作为一个 step（`_summary_step` / `_instructions_step` / `_handoff_step`），用 `_step()` 统一捕获 `DocProblem`：哪份文书没出来就把**那一份**记为 `<step>_status="failed"` 并带上原因 `<step>_error`，继续跑。患者指引是从已签发的小结生成的，所以小结失败时它记为 `skipped`；交班（handoff）不依赖小结，照常生成（连小结 `insufficient` 的病例也生成，以前这种会整例跳过）。指标只统计“真正出了的文书”（全失败时均值用 `None`，让 proxies 闸门判失败而不是空集判通过）；schema validity 把失败计入分母。`app/aiops/suites.py` 的 `_documentation_run` 对每个 agent、每个病例都产出一行，失败/跳过的行 `schema_valid=False`、`passed=False`，原因放在 `got`；`scripts/documentation_eval.py` 的报告、文档、评分表也改成能渲染失败的病例，而不是因为少字段崩掉。
- **怎么证明：** 新测试 `tests/test_documentation_eval.py::test_a_document_the_model_cannot_draft_fails_its_case_not_the_suite`：第 1 个病例合成调用抛 `ProviderUnavailable` 后，没有 agent 记为 `error`；该病例小结 `failed`（原因含 `model unavailable (synthesis)`）、指引 `skipped`、交班照常；其余两个病例正常测到；小结 schema validity = 2/3，其它闸门在这两例上照常计算。自己的测试库 `ioc_test_evalrobust`（5433）上，`test_documentation_eval.py`、`test_script_stdio.py`、`test_module_readmes.py`、`test_aiops.py` 共 64/64 通过。
- **经验：** 评测/批处理要“单条失败不影响整体”，否则一次偶发错误就什么数据都拿不到。排查时顺手把同类 harness 都查一遍：本仓库其它套件没有这个问题——影像各套件、分诊、去标识、通用回放都按病例读 gateway 状态；医嘱审核和用药核对（`comment`、`settle`）把失败记为 `unavailable` 并回退到规则；控制塔叙述回退到模板；电话 agent 捕获 `ProviderUnavailable`、通话小结记 `Summary unavailable`；注入集把崩掉的用例算失败。runner 顶层仍在 harness 真抛异常时把套件记 `error`，这对“harness 有 bug”是对的。同一个道理在产品里也成立：一条记录的权限失败不该让整个列表报错（12.2）。做“优雅降级”的改动，别忘了把“全降级”（某类文书一份都没出）的边界也处理掉，否则 `sum(...)/len()` 会除零。
- **范围：** hospital（WP7 文书评测）/ 平台（`app/aiops` runner）。
- **代码与提交：** `apps/api/app/modules/documentation/evals.py`、`apps/api/app/aiops/suites.py`、`apps/api/scripts/documentation_eval.py`、`apps/api/tests/test_documentation_eval.py`。在 worktree 分支 `claude/dazzling-northcutt-41fb23`，提交 `6be8255`（换模型前的实现）。

**复盘：这个“优雅降级”修复自己又引入了几个问题（合并前复核，每条两名复核者确认，提交 `db6568e`）。** 教训是：**给批处理/评测加“单条失败不致命”时，最容易在聚合量和闸门上留新坑**，因为老代码默认“所有病例都成功”。

1. **闸门张冠李戴（中）：** `nursing_handoff` 的模块闸门原来取 `automated_proxies_mean_ge_4`——三类文书 proxy 的**合并**均值。加了 `None` 处理后，只要小结或指引全失败，合并均值里就有 `None`，合并闸门判失败，于是**交班明明每份都过，却被判 below**。改成每个 agent 看自己那一类：`evals.py` 新增 `handoff_proxies_mean_ge_4`，`suites.py` 的 `_documentation_run` 用它。
2. **except 太宽（低）：** `_step` 把 `_summary_step` 里**签发**时抛的 `DocProblem`（`Untraceable items block signing`）也一起吞了。但模型其实把草稿写出来了，是校验器在拦——这是校验器在干活，不是模型失败。被当成 `failed` 后，这个病例的 raw red / oracle 项就从 `raw_hallucination_rate`、`validator_recall` 里掉了。改法：区分 draft 阶段的 `DocProblem`（不可用/被拒 → 病例 `failed`，无数据）和签发被拦（→ 病例 `unsigned`，保留“校验前”指标）；raw/validator 指标改成在**已起草**的小结上统计，签发后指标（signed-only）才只看**已签发**的。
3. **空集合=满分（低）：** `_ratio(x, 0)` 返回 `1.0`、`sum([]) == 0`，于是一份文书都没起草时，`validator_recall`、`untraceable_after_validator`、`schema_validity` 这些闸门**空集判通过**。改成空集合时指标给 `None`、闸门判失败；监控比率空集给 `None`，报告打印 `n/a`。
4. **只修了一半的入口（低）：** 只有 `scripts/*.py` 调了 `utf8_stdio()`，但上线真实跑的 `python -m app.aiops.evalrun`（以及 `promptgate`、`app.modules.evals.run`/`.build`、`app.modules.clinical_kg.terms`、`app.core.db.crypto`、`app.seed`）重定向时照样写 GBK。把 helper 提到 `app/core/stdio.py`，这些 `__main__` 也调用；`scripts/_stdio.py` 改成从它 re-export（两个从不 import `app` 的生成脚本保留内联兜底）。

- **怎么证明：** `tests/test_documentation_eval.py` 加三个用例（签发被拦 → `unsigned` 且保留 raw 指标；一份都没起草 → 闸门全失败不空判；指引全失败 → 交班闸门仍通过），`tests/test_script_stdio.py` 加两个（`app.core.stdio` 在 `PYTHONIOENCODING=gbk` 子进程下写 UTF-8；每个 `python -m` 入口都在 `__main__` 首行调 helper）。自己的测试库 `ioc_test_evalrobust` 上，三个被动文件 24/24 通过。
- **经验：** 降级改动要把**每个聚合量、每个闸门**按“空集 / 部分失败”重新审一遍——`x/0`、`sum([])`、跨维度合并的闸门、以及“顺手 try 一下”的过宽 `except`，都会把“没数据”或“别的东西失败”误报成“通过”。入口修复要覆盖**所有**会打印的入口（脚本和 `python -m` 都算），否则“不再需要 PYTHONUTF8”的结论只对一半成立。

> **附：Claude 的一条接口兼容性记录**（PROGRESS WP4b Notes）：Claude Sonnet 5.5 拒绝非默认的采样参数，所以不发送 temperature，也不声称有上限。

---

### 3.10 合并后全量测试 5 个失败：不是合并的锅，是工作区里未提交的真实运行记录

- **现象：** 把 Gemini 调优和评测修复两个分支合进 `main` 后，全量后端测试 797 过、5 失败：`test_every_existing_agent_is_registered_with_its_prompt_version`（`order_review` 期望 `pending`，实际 `failed`）、注册表 API 的 "not evaluated" 标记、两个 agent runtime 测试、`test_the_safety_doc_is_current`（`docs/safety-tiers.md is out of date`）。
- **怎么查的：**
  1. 先看失败内容而不是失败数量：5 个失败都和"agent 的发布状态"有关，没有一个碰合并改过的 provider、网关或评测代码。
  2. 假设：发布门禁从 `evals/runs/` 里**最新的运行记录**推导每个 agent 的状态，而工作区里有 4 个未提交的真实运行记录（10-12 的首轮真实测试），它们把一部分 agent 从 `pending`（mock 基线）改成了 `passed`/`failed`；测试和 `safety-tiers.md` 都是按 mock 基线写的。
  3. 能推翻它的实验：把这 4 个未提交文件临时移到 scratchpad，只重跑这 5 个测试。结果 **5/5 通过**；移回原处。合并后的代码等于 802/802。
- **根因：** 测试读的是仓库里的运行记录（共享状态），工作区里多出的未提交数据改变了被测对象。
- **怎么修：** 不改代码。记下结论：如果要提交真实运行记录，必须**同时**更新这些测试的期望并重新生成 `docs/safety-tiers.md`（`uv run python scripts/safety_doc.py`），否则 `main` 上会有 5 个红测试。
- **经验：** 合并后出现失败，先问"失败和改动面有没有交集"；没有交集时，去找**环境里多出来的东西**（未提交文件、别的会话留下的数据库、缓存）。用"移走 → 重跑 → 移回"这种只改一个条件的实验定性，比读代码猜快。
- **范围：** 平台（发布门禁、`evals/runs/`）。
- **来源：** 本会话 2026-10-12 合并 `7f3f91a`、`a55a7a6` 后的全量测试。
- **代码：** `apps/api/app/aiops/release.py`、`apps/api/tests/test_agent_registry.py`、`apps/api/tests/test_roadmap.py`。

### 3.11 504 重试永远不会触发：重试预算被一次超时吃光了

- **现象：** 合并的 Gemini 改动对 429/504 做指数退避重试，单元测试全绿；但合并后的真实测试里，文书 agent 仍有 20 个病例因为一次 `unavailable` 直接失败，日志里看不到任何重试。
- **怎么查的：** 失败分析员按时间看失败调用：每次都是约 26.1–27.7 秒后返回、0 token。再读 `providers.py` 的重试逻辑：总预算就是 `LLM_TIMEOUT_S`（30 秒），SDK 把它作为服务器端截止时间发出去；Vertex 在约 27.5 秒回 504，剩下约 2.5 秒，小于"退避至少 2 秒 + 每次尝试至少 `MIN_ATTEMPT_S` 5 秒"，于是直接放弃。两名复核者读代码确认。
- **根因：** "总时间不超过超时"和"超时本身就是失败模式"放在同一个预算里：只要失败是**超时**，就永远没有剩余预算去重试。单元测试用的假客户端瞬间返回 504，测不到这一点。
- **怎么修：** 卡片给了两个方向："每次尝试的截止时间短于总预算"，或"总预算至少容纳一次完整重试"。只加大总预算不够：SDK 把整个超时都作为服务器截止时间发出去，卡顿就会一直卡到新的截止时间，还是没有剩余预算。所以选了前者：`GeminiProvider._generate` 里每次尝试最多用总预算的 `ATTEMPT_SHARE`（0.5），而且不超过剩下的时间。卡住的那次尝试在预算一半左右收到 504，退避 2 秒后还有一次完整的尝试，整个调用仍然不超过预算。比例的选法看了数据：`run-2026-10-12T225622` 里 LOW 思考下成功调用的延迟，p50 在 1–5 秒，30 秒预算的 agent 里最慢的成功调用约 20 秒（用药核对），文书最慢约 26 秒（交接）。所以文书 agent 用 60 秒（两次 30 秒，和以前一次尝试一样长），通过 agent 注册表新加的 `model_policy.timeout_s` 配置。其他 agent 保持 `LLM_TIMEOUT_S` 30 秒（两次 15 秒），电话 agent 没有变长。网关把 `timeout_s` 传给 provider，Anthropic 也用它作为单次请求的超时。
- **怎么证明：** 单元测试的假客户端会模拟**慢**失败：`stalling()` 读取这次尝试的截止时间，让时钟走到截止前 2.5 秒再返回 504，和真实情况一样。`test_a_stalled_attempt_is_retried_within_the_call_timeout` 断言两次尝试的截止时间都是 15000 ms，504 出现在 12.5 秒，第二次在 18.5 秒成功。同一个测试把 `ATTEMPT_SHARE` 设成 1.0（7f3f91a 的设计），断言只尝试 1 次、在 27.5 秒失败，把旧 bug 也钉住。另有：两次卡顿在 27 秒放弃（不超预算）；单次调用传入的预算替换 provider 的预算；agent 的 `timeout_s` 经过网关生效；三个文书 agent 是 60 秒，电话 agent 不变。`tests/test_gemini.py` 50 个测试通过；全量 807 过 1 挂，挂的那个在没改过的 a347f78 上一样挂（见 PROGRESS.md）。改电话层默认值后又跑了 `test_gemini.py` 和 `test_agent_registry.py`，60 个全过。真实重跑 `run-2026-10-13T021309`：44 次调用至少卡过一次，重试救回 29 次（66%）；出院小结 10/30 → 21/30，患者指导 8/30 → 20/30。没救回的 15 次见 3.15。
- **经验：** 设计重试时，把"失败要花多长时间"算进去：快速失败（429）和慢速失败（超时）需要的预算完全不同。测试替身要能模拟**慢**失败，否则重试逻辑最关键的分支没有被测到。修好以后，把旧设计的参数放进同一个测试再跑一遍，确认测试真的能区分新旧两种行为。
- **范围：** 平台（LLM 调用层）。
- **来源：** 2026-10-12 合并后真实测试（`run-2026-10-12T225622`）的失败分析，复核 2/2；修复和重跑在 2026-10-13 的侧会话（分支 `claude/trusting-hypatia-72ce4a`）。
- **代码：** `apps/api/app/llm/providers.py`（`GeminiProvider.ATTEMPT_SHARE`、`_generate`）、`apps/api/app/agents/registry.py`（`ModelPolicy.timeout_s`）、`apps/api/config/agents/{discharge_summary,patient_instructions,nursing_handoff}.yaml`、`apps/api/app/llm/gateway.py`、`apps/api/tests/test_gemini.py`。
- **提交：** `2a92b10`（分支 `claude/trusting-hypatia-72ce4a`）。

### 3.12 文书只过了 10/30：用"单次调用失败率"推算整份文书的通过率

- **现象：** 合并后真实测试里，出院小结 30 个病例只签发 10 个，`schema_validity` 0.3333；患者指导 8/30。看起来像模型质量很差。
- **怎么查的：**
  1. 把 20 个失败按原因分类：每个都是某一次调用 `unavailable`（约 27 秒、0 token），**没有一个**是截断、校验失败或拒答；写出来的 10 份小结全部干净（不可溯源 0、幻觉率 0、召回 1.0、5 次注入全部拦下）。
  2. 看时间分布：卡顿集中在 22:57–23:22（文书套件最先跑），同一个用药核对 prompt 在这个窗口里卡了 5 次，到 23:45 再跑 19 次 0 卡顿。所以是 Vertex 端某段时间变慢，不是思考等级。
  3. 用数字验证解释：窗口内单次调用卡顿率约 21%（93 次里 20 次）；一份小结最多 5 次串行调用（4 段 + 合成），遇到第一次失败就停，期望通过率 0.79⁵ ≈ 0.30，**和观察到的 0.33 吻合**。患者指导依赖签发的小结，20 个被跳过。
  4. 还发现 `schema_validity` 的口径是"已签发 / 全部"，没输出的病例也记成 schema 不合格，所以这个数其实在报"可用性"。
- **根因：** 外部服务在一段时间内的可用性问题，被多次串行调用放大；加上 3.11 的重试失效，一次卡顿就丢掉整个病例。
- **怎么修：** 见 3.11 的卡片（重试和长调用截止时间），之后在负载低的时段重跑文书。
- **经验：** 分数很低时先分类失败原因，再用简单的概率模型（单次成功率的 n 次方）检验"是不是可用性问题"：数字对得上，就不要去调 prompt。也要看指标的**口径**，名字叫 schema_validity 不代表它只量 schema。
- **范围：** hospital（WP7 文书）/ 平台。
- **来源：** `run-2026-10-12T225622` 失败分析（4 名分析员、每条结论 2 名复核者）。
- **代码：** `apps/api/app/modules/documentation/evals.py`（约 384 行，schema_validity 口径）。

### 3.13 故事线第 9 步被拦：验证器抓到模型编造的"房颤"

- **现象：** 合并后用真 key 走 12 步医院主线，前 8 步通过（总耗时 271 秒，上次 650 秒）。第 9 步出院小结这次**写出来了**（上次超时），但移除预埋的不可溯源药物后仍被拦：`medications.continued[2]` 里写了 "atrial fibrillation"，红色 `untraceable_diagnosis`："condition 'atrial fibrillation' in the text is not a diagnosis of the input"。
- **怎么查的：** 故事线脚本只预期一个红项（预埋的陷阱）。看第二个红项的内容：病人在用华法林，模型给它补了一个"常见适应证"房颤，而病人的诊断列表里没有房颤。
- **根因：** 模型的"合理补全"在临床文书里就是幻觉；可溯源校验器按设计把它拦下。
- **怎么修：** 不改校验器（它在正确工作）。后续可以在 prompt 里明确"适应证只能来自输入的诊断"，故事线脚本也要能处理模型额外产生的红项。
- **经验：** 真实模型和 mock 的差别常常不在"答错"，而在"多说了一句看似合理的话"。这正是可溯源校验存在的理由：让每句话都能对回输入，多出来的就会被抓住。
- **范围：** hospital（WP7 文书、WP9 故事线）。
- **来源：** 2026-10-12 合并后的故事线实跑（`scripts/storyline_report.py --api`，未写报告）。
- **代码：** `apps/api/app/modules/documentation/validator.py`、`apps/api/app/modules/storyline/walk.py`。

### 3.14 电话 agent 的"中文失败"：其实是 MINIMAL 思考，和语言无关

- **现象：** 把电话层思考等级调成 MINIMAL 后，`voice_agent` 从 19/20 降到 16/20。新的 3 个失败（fd-02、fd-08、fd-18）**都是普通话通话**，中文从 7/7 掉到 4/7，英文和法文没变。失败分析的结论是"MINIMAL 有可能，未证明"：每种设置只跑了一次，19/20 对 16/20 的 Fisher 检验 p≈0.34。
- **怎么查的：**
  1. 列出假设。H1：中文通话本身有问题（prompt、去标识化或语言处理），MINIMAL 只是巧合。H2：MINIMAL 让模型"想得太少"，多步工具调用时提前收手。H3：纯属抽样波动。
  2. 设计能区分它们的实验：同一份代码、同一个数据库，**交替**跑 MINIMAL、LOW、MINIMAL、LOW，每次 20 个通话。交替是为了让 Vertex 的时段状态和机器负载对两组一样（只用环境变量 `GEMINI_THINKING_VOICE` 切换，运行记录写到 `EVAL_RUNS_DIR=evals/runs/voice-thinking-ab/<等级>`，不影响发布门禁）。
  3. 结果：LOW 20/20、20/20，MINIMAL 15/20、14/20。40/40 对 29/40，p = 0.0004，H3 被推翻。
  4. 看 MINIMAL 的失败分布：除了中文的 fd-02、fd-08、fd-18，英文的 fd-04、fd-07、fd-09、fd-17 也失败。H1 被推翻：之前"全是中文"是一次小样本的巧合。
  5. 看失败的样子，只有两种：查完预约（`lookup_appointment`）就结束，没有确认或取消，通话记为 abandoned；身份核验三次失败后不调用 `transfer_to_human`。两种都是"该调下一个工具时改成了回一句话"，符合 H2。
  6. 再看 MINIMAL 换来了什么：平均响应 MINIMAL 约 2.0–2.2 秒，LOW 约 2.4 秒，1500 ms 的门槛两种都过不了。所以 MINIMAL 的延迟优势在门禁上不存在，代价却是 1/4 的通话失败。
- **根因：** MINIMAL 思考下，Gemini 3.5 Flash 在多步工具循环里更容易提前结束这一轮。语言不是原因。
- **怎么修：** `GEMINI_THINKING_VOICE` 默认值从 MINIMAL 改为 LOW（`apps/api/app/core/config.py`，注释写明 A/B 的数字），`test_thinking_levels_come_from_the_environment` 跟着改。
- **怎么证明：** 真实重跑 `run-2026-10-13T021309`（LOW，机器没有其他负载）19/20，中文 6/7。唯一的失败 fd-18 是已知的 prompt 缺口（身份失败没有映射到转人工，live-run 报告发现 7c）。LOW 三次合计 59/60，MINIMAL 三次合计 45/60。每个通话成本 $0.0071 → $0.0114，仍远低于 $0.05 的门槛。
- **经验：** 小样本里的失败恰好集中在某个属性上（"全是中文"），很容易让人编出一个错误的故事。先列出能互相区分的假设，再做**交替、重复**的 A/B，然后看**全部**失败的形态，不只看那个子群。另外要问调优到底换来了什么：如果 MINIMAL 省下的延迟仍然过不了门槛，这个取舍就不成立。
- **范围：** 平台（LLM 调用层）/ imaging（系统 4 前台电话）。
- **来源：** 2026-10-13 侧会话的 A/B（`evals/runs/voice-thinking-ab/`，未提交）。
- **代码：** `apps/api/app/core/config.py`（`GEMINI_THINKING_VOICE` 默认值）、`apps/api/app/modules/frontdesk/agent.py`（`LlmAgent`）。
- **提交：** `2a92b10`（分支 `claude/trusting-hypatia-72ce4a`）。

### 3.15 重试救回 2/3，剩下的是"连环卡顿"：代码没动的 call_summary 反而掉分

- **现象：** 修好 3.11 后重跑，文书分数翻倍，但**完全没改过**的 `call_summary` 从 15/20 掉到 11/20。出院小结还有 8 个病例、患者指导 2 个、交接 1 个失败，失败调用都在约 57 秒（两次 30 秒尝试）。紧接着的 12 步故事线，第 9 步等出院小结草稿超时。
- **怎么查的：**
  1. 看 `call_summary` 每个病例的延迟和输出。4 个失败的 summary 为空、延迟 26.9–27.2 秒，正好是两次 15 秒尝试各在 12.5 秒收到 504，中间退避 2 秒（12.5 + 2 + 12.5）。另有 4 个延迟 16.5–18 秒，成功：卡一次，重试 2–3 秒完成。其余 5 个失败是老问题（`follow_up_needed` 的定义）。上次重跑 `call_summary` 一次都没卡过，最慢 2.6 秒。
  2. 假设 A（担心是自己引入的回归）：15 秒的单次截止时间把"慢但正常"的调用砍掉了。反证：正常的 `call_summary` 调用只要 1.5–2.6 秒；服务器总是在"我们给的截止时间减 2.5 秒"返回 504（截止 30 秒时 27.5 秒，15 秒时 12.5 秒），而且 0 token。说明它一直卡到截止时间才放弃，并不是在算一个要 20 秒的答案。这一点是**推测**：无法直接证明这些请求给 30 秒就能完成。不过上次的 30 秒设计下同样的卡顿也全部失败，所以最坏也只是"没变好"。
  3. 假设 B：卡顿成串出现，不是彼此独立。用 API 日志验证：412 次请求里 59 次 504。每个失败的调用占 2 次（15 个调用、30 次），每个被救回的占 1 次（29 个），所以 44 个调用至少卡过一次。第一次尝试的卡顿率约 12%（44/约 363），卡过一次后重试再卡的比例是 34%（15/44）。如果卡顿独立，第二次也应该约 12%，B 成立。时间上也对得上：前台套件最后跑（02:55–03:00），20 个通话摘要里 8 个卡过；故事线（03:02–03:11）99 次请求里 57 次 504（58%）。
  4. 故事线第 9 步：walk 同步 POST 出院小结草稿，两次都收到 `409 Conflict`（后台 journey 正在起草同一份文书，起草按文书串行）。它只等 180 秒（`--timeout`），而 60 秒预算下一份小结最长可能要 5 × 60 秒。在 58% 卡顿率的窗口里，这一步注定失败。
- **根因：** Vertex 的卡顿成串出现，一次重试只救得了零星的卡顿。`call_summary` 掉分是因为它刚好排在一串卡顿里，不是代码回归。
- **怎么修：** 卡顿本身是 Vertex 那边的事，修不掉。先把三个方向逐个拿数据检验，再请 owner 选：
  1. **(a) `ATTEMPT_SHARE` 改成 1/3，换来第三次尝试。** 前提是"每个正常调用都能在 1/3 预算内完成"，所以先检验这个前提。数据用 `run-2026-10-12T225622`：那时每次尝试还有完整的 30 秒，慢的正常调用没被截断，延迟分布是完整的。运行记录只给**失败**病例存了 trace（调用级延迟），所以样本是"失败病例里的成功调用"，单调用的 agent 再补上通过病例的病例延迟。截断点按"服务器在截止前约 2.5 秒回 504"推算（这是从卡顿观察到的，对"慢但在算"的请求是否同样提前 2.5 秒截断，没有直接证据）：30 秒预算的 agent 从现在的 12.5 秒降到 7.5 秒，60 秒预算的从 27.5 秒降到 17.5 秒。结果：order_review 58 次里 6 次（10%）、medication_reconciliation 53 次里 6 次（11%）、nursing_handoff 23 次里 2 次（9%）、discharge_summary 58 次里 1 次超过新截断点；call_summary（最慢 4.2 秒）和 voice_agent（最慢 1.6 秒）一次都没有。被截断的慢调用，如果慢是因为输出长，重试时还是一样慢，三次全被截断，等于把"慢但成功"变成失败。所以全局 1/3 不可取；只给 call_summary、voice_agent 这类快 agent 配第三次尝试是可行的变体。
  2. **(b) 进程级熔断：最近 N 次 504 之后，所有调用退避一段时间。** 推演两种做法。"快速失败"：故事线那段 58% 卡顿的窗口里，仍有 42% 的请求没卡，熔断会把它们也丢掉，通过率只会更低。"先等再发"：卡顿串持续好几分钟，单次调用的预算只有 30 或 60 秒，等不过去。剩下的好处只是交互用户更快看到模板回退、少发无用请求，代价是全进程共享状态：文书卡顿会连带电话 agent 一起降级。所以不提高通过率。
  3. **(c) 在每个运行记录里写下卡顿率。** 不改任何行为，成本低，回答的正好是"这次的分数是不是卡顿造成的"。
  4. **owner 选了 (c)，加上故事线的等待时间。** 实现：
     - `GeminiProvider._generate` 把每次调用里每次尝试怎么结束的（成功；卡顿 = 504 或客户端自己超时；429；其他错误）按 task（agent id）记进 `AttemptStats`。默认记到进程级的 `ATTEMPT_STATS`，因为有的评测 harness 自己建网关（`app/modules/evals/run.py`），它的调用也算这次运行；API 里后台循环和请求线程会并发调用，所以计数加锁。
     - `evalrun.run` 在开始和结束各取一次快照，差值写进运行记录的 `stalls`（总数，加 `by_agent`）和每个 agent 条目的 `stalls`。两个比率：`first_attempt_stall_rate`（第一次尝试就卡的比例）和 `stall_after_stall_rate`（卡过之后的下一次尝试又卡的比例），就是本案"怎么查的"第 3 步手算的 12% 和 34%，现在每次运行自动算。CLI 和运行记录的 `note` 写明卡了几次、重试救回几个、丢了几个。mock 和 Anthropic 运行没有这个计数，`stalls` 为 null。
     - 故事线第 9 步：`walk.summary_draft_s()` = `SUMMARY_CALLS`（5）× 出院小结 agent 的 `model_policy.timeout_s`（60 秒）= 300 秒。`scripts/storyline_report.py --api` 让第 9 步等 `--timeout` + 300 = 480 秒（后台 journey 可能正在起草同一份，walk 先收到 409，直到它结束），HTTP 客户端超时从 300 秒提到 360 秒，容得下一次同步起草。其他步骤仍等 `--timeout`。5 这个数先验证过：在 mock 下走完整条故事线，读签发的小结里的 `inputs.chunks`，正好 4 段（诊断、用药、观察、计划），加合成 = 5 次调用。
     - 顺带发现：检验 (a) 时看到，order_review 和 medication_reconciliation 已经有 2–3 个正常调用（14–20 秒）超过**现在**的 12.5 秒截断点。这是 3.11 把比例定为 0.5 的副作用，列为后续（给这两个 agent 设 `timeout_s: 45`，见 3.16）。
- **怎么证明：** 卡顿的数字来自 `run-2026-10-13T021309.json`（每个病例的延迟和输出）和 API 日志里的 HTTP 状态计数，统计方法写在 live-run 报告 "After the timeout fix" 一节。修复的证明：`test_attempts_are_counted_per_agent_with_the_stalls_a_retry_saved_and_lost` 用 `stalling()` 和 FakeClock 走五种情况（一次成功；卡一次后救回；卡两次后丢掉；客户端超时再 429 再成功；400 不重试），断言每个 agent 的全部计数和 `since()` 的差值。`test_a_live_eval_run_records_the_stall_rate_it_ran_under` 用 `FixtureGemini`（Gemini 的重试循环包着 mock 夹具的答案）真的跑一次 `evalrun.run(..., live=True)`，前四次调用分别卡 1、2、0、1 次，断言运行记录里 3/N 次首次卡顿、3 次卡后重试里 1 次再卡、救回 2 个、丢 1 个，agent 条目、`note` 和 CLI 输出一致。变异检验：删掉失败分支的 `self.stats.record(...)`，这两个测试都挂。故事线：`test_the_whole_story_offline` 加了"段数 + 1 == `SUMMARY_CALLS`"，把常数钉在数据上（病人数据变了，测试会提醒改等待时间）；`test_the_summary_step_can_wait_for_a_whole_draft_against_a_live_model` 断言只有第 9 步用长等待，不传时退回 `timeout_s`。`test_gemini.py`、`test_aiops.py`、`test_storyline.py` 全过；全量 811 过 1 挂，挂的是 `test_template_only_without_ai_consent`（只在周二挂，在没改过的 a347f78 上一样挂，另一个侧会话已查明是测试本身的假设）。没有做真实重跑。
- **经验：** 修复之后，没改过的东西分数掉了，先看那几个失败病例的**延迟特征**：两次截止时间之和，说明是环境原因。要判断"多重试几次有没有用"，比较"卡过一次后再卡"的条件概率和总体卡顿率：两者接近，说明卡顿独立，多试几次有用；条件概率高得多，说明卡顿成串，多试几次只是等更久，得换策略（熔断、整体退避、换时段）。选方案之前，先用已有数据检验每个方案的**前提**（"正常调用都能在 1/3 预算内完成"），并说清样本从哪来、有什么偏差（只有失败病例存了 trace）。外部问题修不掉时，至少让它在结果里看得见：把诊断时手算的数变成每次运行自动记录的字段，下次就不用再翻日志。
- **范围：** 平台（LLM 调用层、评测）/ hospital（WP9 故事线）。
- **来源：** 2026-10-13 侧会话的重跑和故事线实跑；方案检验和实现在之后的侧会话（分支 `claude/reverent-babbage-aad5a2`，基于 `claude/trusting-hypatia-72ce4a`）。
- **代码：** `apps/api/app/llm/providers.py`（`_generate`、`AttemptStats`、`ATTEMPT_STATS`）、`apps/api/app/aiops/evalrun.py`（`_stall_summary`、`_stall_note`）、`apps/api/app/modules/storyline/walk.py`（`_summary`、`wait`、`SUMMARY_CALLS`、`summary_draft_s`）、`apps/api/scripts/storyline_report.py`（`live`）、`apps/api/app/modules/documentation/service.py`（起草串行，409）、`apps/api/tests/test_gemini.py`、`apps/api/tests/test_storyline.py`。
- **提交：** `967fc77`（分支 `claude/reverent-babbage-aad5a2`）。

### 3.16 一半预算的副作用：用药的两个 agent 里慢而正常的调用在 12.5 秒被截断

- **现象：** 3.15 检验方案 (a) 时顺带发现：3.11 让每次尝试最多用预算的一半，30 秒预算的 agent 每次尝试就只有 15 秒；Vertex 在截止前约 2.5 秒回 504，实际截断点约 12.5 秒。order_review 和 medication_reconciliation 有几个**正常**调用比 12.5 秒还慢，它们会被当成卡顿砍掉再重试。owner 要求把这件事单独做，不和卡顿率的改动放在一起。转述的理由是"慢是因为输出长，重试还会一样慢"。
- **怎么查的：**
  1. 先自己重算转述里的数字。方法同 3.15 (a)，数据用 `run-2026-10-12T225622.json`：那时每次尝试还有完整的 30 秒，慢的正常调用没被截断。样本 = 所有套件失败病例 trace 里这个 agent 的 `status == "ok"` 调用（按 `call_id` 去重：文书套件的 trace 里也有用药核对的调用），加上本 agent 套件通过病例的 `latency_ms`（这两个都是单调用 agent；有 1 个通过病例没有 `latency_ms`，去掉）。order_review 58 次（trace 24 + 通过 34），超过 12.5 秒的 3 次：12.7、13.9、14.3 秒；medication_reconciliation 53 次（trace 37 + 通过 16），2 次：13.2、20.2 秒。和转述一致。
  2. 算新的截断点：45 秒预算 → 每次 22.5 秒 → 约 20.0 秒截断。order_review 0/58 超过；medication_reconciliation 还剩 1/53：20.2 秒那次只比截断点慢 0.2 秒，**没有余量**。要全部盖住得 50 秒（约 22.5 秒截断）。
  3. 检验转述的前提"慢是因为输出长"。先查指标口径：`GeminiProvider._usage` 的输出 token 是 `candidates_token_count + thoughts_token_count`，含思考。整体上延迟和输出长度中等相关（Spearman：order_review 0.44，用药核对 0.48），但**最慢的几次并不长**：20.2 秒那次输出 417 token（48 ms/token），13.2 秒那次 180 token（73 ms/token），而 6 秒以内的用药核对调用输出中位数是 476 token，每 token 中位数约 6 ms；order_review 最慢的 13.9 秒只有 570 token（24 ms/token），6 秒以内的中位数是 751 token。尾巴上的慢不是输出长造成的，前提不成立。
  4. 那尾巴是什么？把 trace 的 `started_at` 和卡顿排在同一条时间线上：两次慢的用药核对（22:58:58 的 20.2 秒、23:18:15 的 13.2 秒）都落在文书套件 22:57–23:22 的卡顿窗口里；order_review 的两次慢调用（23:43:43、23:43:57）相隔 14 秒，紧挨着 23:44:29 的一次 order_review 卡顿。慢而正常的调用和卡顿**扎堆出现**，像同一段 Vertex 变慢的时间：有的请求卡到截止，有的慢慢算完。所以截断后的重试会落在同一段时间里（3.15：卡过一次后再卡的比例是 34%）。
  5. 看修复之后的数据：`run-2026-10-13T021309`（2a92b10 之后，每次尝试 15 秒）没有跑用药套件，但文书失败病例的 trace 里有 24 次用药核对调用，其中 5 次在 16.2–19.4 秒成功。单次尝试不会超过 15 秒，所以这 5 次都是第一次在约 12.5 秒被截断，退避 2 秒，重试 2–5 秒完成。trace 不记每次尝试，所以分不出它们是真卡顿还是慢的正常调用被截断；能说明的只是被截断后的重试**大多很快**。
  6. 列出所有等这两个 agent 的地方（预算从 30 秒变成 45 秒，每个等待方都得容得下）。两个 agent 每次运行只调一次模型（`run.model` → `gateway.structured(task=agent_id)`；网关的 schema 或截断重试最多再调一次，即 2 × 45 = 90 秒）。order_review 的模型调用只在 `service.comment`：后台 narrator 循环的 `ai_step`，或审核页的按钮 `POST /api/med-safety/reviews/{id}/commentary`；journey 的 `orderReview` 步骤只建 Task，不调模型。medication_reconciliation 在 `service.settle`：后台 `ai_step`；journey 的 `medRecAdmission` 活动（`ACTIVITY_TIMEOUT` 2 分钟）；药师打开、处理或签署一个还在 pending 的核对（`session_view`、`handle_item`、`sign` 都同步调用 `settle`）；故事线的出院核对（快进任务排空事件时调用 `discharge_medrec`，walk 最多等 900 秒）。前端请求没有客户端超时；故事线第 6 步等入院核对的 AI 步骤用 `--timeout`（180 秒）。全都容得下。真正变长的是 narrator 循环（`app/main.py` `_narrator_loop`）：一轮最多 2 个点评 + 2 个核对，串行；四个都遇到两连卡时，一轮从约 4 × 27 秒变成约 4 × 42 秒，排在后面的 Control Tower 叙述和文书 intake 要多等。
- **根因：** `ATTEMPT_SHARE` 0.5 等于给每个 agent 设了一个"卡顿还是慢"的分界线（预算的一半减 2.5 秒）。3.11 按全体 30 秒 agent 统一定了这条线，而这两个 agent 在 Vertex 变慢的时段里，正常延迟的尾巴比 12.5 秒长。
- **怎么修：** `config/agents/{order_review,medication_reconciliation}.yaml` 的 `model_policy` 加 `timeout_s: 45`：每次尝试 22.5 秒，约 20 秒截断，盖住 58 次 order_review 和 53 次用药核对里的 52 次。代价也写明：真卡顿要等约 20 秒（原来约 12.5 秒）才放弃并重试，两连卡约 42 秒才放弃（原来约 27 秒）。换来的是：变慢时段里慢而正常的调用不再被砍掉，再到同一个时段里去重试。粗算（样本很小）：12.5–20 秒之间的正常调用约 5%（111 次里 5 次）；30 秒预算下，其中约 1/3 的重试会再卡，每 100 次调用约丢 1.5 次，45 秒下不丢。卡顿约占第一次尝试的 12%，每次多等约 7.5 秒。两个 agent 主要在后台跑，等待方都容得下，所以这个取舍成立；要给 20.2 秒那次留余量，就用 50 秒。
- **怎么证明：** `tests/test_gemini.py::test_order_review_and_medication_reconciliation_attempts_outlast_their_slow_healthy_calls`：断言两个 agent 在注册表里都是 45；再用真实的注册表条目经网关各调一次（假 Gemini 客户端），断言发出的单次截止时间是 22500 ms（45 秒的一半），证明配置值真的到了 provider。变异检验：删掉 medication_reconciliation 的 `timeout_s` 一行，测试以 `assert None == 45` 失败。`test_gemini.py` 54 个全过；全量 812 过 1 挂，挂的还是 `test_template_only_without_ai_consent`（只在周二挂，和本改动无关，见 3.15）。没有做真实重跑：效果要等下次跑用药套件时，看运行记录里的 `stalls` 和这两个 agent 的延迟分布。
- **经验：** 一个全局比例其实是给每个 agent 定了一个阈值，阈值要看**每个 agent 自己的**正常延迟分布，所以做成按 agent 配置。核对别人给的前提（"慢是因为长"）时，先查指标口径（输出 token 含思考），再单看尾巴上的样本：整体 0.44 的相关，到尾巴上完全不成立。把慢的正常调用和卡顿排在同一条时间线上，就能看出它们是不是同一个现象。改超时之前，把所有等这个调用的地方列出来，逐个确认它们的上限比新预算长。
- **范围：** 平台（LLM 调用层）/ hospital（WP6 用药安全）。
- **来源：** 3.15 的后续，2026-10-13 侧会话（分支 `claude/stoic-sutherland-d96ab0`，基于 `claude/reverent-babbage-aad5a2`）。
- **代码：** `apps/api/config/agents/order_review.yaml`、`apps/api/config/agents/medication_reconciliation.yaml`、`apps/api/app/llm/providers.py`（`ATTEMPT_SHARE`、`_usage`）、`apps/api/app/modules/med_safety/service.py`（`comment`、`settle`、`ai_step`）、`apps/api/app/main.py`（`_narrator_loop`）、`apps/api/tests/test_gemini.py`。
- **提交：** `d0e1241`（分支 `claude/stoic-sutherland-d96ab0`）。
### 3.20 交班 SBAR 的"未记录"无处可引：prompt 和校验器互相矛盾，代理指标又把一个错扣了两次

- **现象：** 合并后的真实运行 `run-2026-10-12T225622` 里，`nursing_handoff` 写出 22/30 份交班（8 份是 Vertex 卡顿），只过 8 份；代理指标门槛失败：accuracy 2.95、format 3.73（completeness 5.0、readability 4.14）。14 份交班恰好是 accuracy 2.0 + format 3.0，其余 8 份是 5/5 或 4/5。mock 基线全是 5.0，`handoff_red` 0。
- **怎么查的：**
  1. **先读口径，不读模型。** 运行文件只留了每份交班的红项数和代理分，**没有交班内容**。看 `evals.py` 的 `_handoff_proxies`：format 只有在某句 `source_refs` 为空时才是 3.0，所以这 14 份每份至少有一句没引用来源，也就是 `HandoffValidator._cited` 报的红色 `no_source`；accuracy 是"有红 2.0、只有黄 4.0、都没有 5.0"的台阶。算一下：5 − 3×14/22 − 1×3/22 = 2.95。**2.95 只是"有红项的交班占比"换了个刻度**，而且同一句没引用的话在 accuracy 和 format 各扣一次。
  2. **列假设：**
     - A（读 prompt 得出）：`nursing_handoff@1` 要求 assessment 写"出入量"，缺了就写 "not recorded"；同时又要求每句都带 `source_refs`。`handoff_agent.build_facts` 的事实里**根本没有出入量**，所以一句如实的"出入量未记录"没有任何资源可引。
     - B：模型随机漏引用。
     - C：`unknown_ref` 重试把引用弄丢了。看运行文件：30 个病例正好 30 次模型调用，重试一次都没发生；而且编造的 id 报的是 `unknown_ref`，不是 `no_source`。**C 排除。**
  3. **mock 实验：** 写了一个复现脚本（自己的库 `ioc_repro_handoff`，只起草交班，事务回滚），在 doc-01、04、19、22、25 上跑 mock：0 红。看 `mocks.py`：mock 的"Intake and output: not recorded this shift."引用的是生命体征观测（没有就引 Encounter）。**mock 的作者替模型把矛盾"解决"了**：随便挂一个来源。所以 mock 永远看不到这个问题，而那个引用其实什么也证明不了（生命体征并不能说明出入量没记）。
  4. **真实模型实验（负责人同意，10 次调用）：** 同样 5 个病例各起草 2 次。6 次是 Vertex 卡顿（0 token），4 次写出来了：**4 份全都有 "Intake and output were not recorded."**；其中 3 份 `source_refs: []`，而且这是每份交班**唯一**的红项；doc-25 那份引了 Encounter，于是"通过"了，但那个引用是凑数的。其他所有句子都正常引用，**B 排除，A 成立**。
  5. **顺带发现：** 这 4 份里有 3 份带黄色 `number_not_in_source`，值是 "01" 和 "00"：模型写 "Laboratory results at 01:00 ..."。事实给了化验的时间（`time: "01:00"`），但校验器看的来源 `Source.detail` 只有 "名称 数值"，没有时间。又是一处事实和校验器对不上，正是这类假黄项让 doc-25 拿了 accuracy 4.0。
- **根因：** 两条规则不能同时满足：prompt 要求把"缺失"写成一句话，校验器要求每句话都能对回一个输入资源。"没有"这件事没有资源可引。模型选择了如实写、不引用，结果被判为不可溯源。mock 和用例是同一个作者写的，矛盾被 mock 悄悄绕过，所以只在真实模型上暴露。另外，代理指标不是互相独立的：一个缺引用同时拉低两项，accuracy 又是台阶函数，整体只剩"有没有红项"这一个信息。
- **怎么修：**
  1. **给"缺失"单独一个通道**（规格 7.3 本来就要求"输入缺关键资源时输出缺失清单"）：`Handoff` 加 `not_recorded: list[GapTopic]`，取值固定为 `intake_output`、`vital_signs`、`pain`、`laboratory_results`、`open_tasks`、`flags`。它不是一句话，不需要引用；`HandoffValidator.check_document` 拿它**对照全部输入**：输入里明明有却声明"未记录"（比如有生命体征却写 `vital_signs`），就是红色 `recorded_not_missing`，因为那会让下一班漏看数据。
  2. 新 prompt `nursing_handoff@2`：缺失的话题放进 `not_recorded`，任何一句话都不许说"没有、未记录、不可用"。注册表改为 `nursing_handoff` 0.3.0 → `@2`，mock 跑完整套件 30/30，`--stamp` 写进头部（`run-2026-10-13T151242`），prompt gate 通过；`@1` 保留为上一版。
  3. mock 改成同样的契约（不再给缺失挂凑数的引用），情况栏也改成一句话；`render_text` 和网页的 SBAR 面板显示"Not recorded: intake and output"。
  4. 化验来源的 `detail` 加上时间（`... at 01:00`），和给模型的事实一致。
  5. 代理指标重新定义，**一个缺陷只动一项**：completeness = 需要的内容在不在（四段、待办和标志都进了建议、生命体征趋势引用了观测、疼痛和出入量要么有一句、要么在 `not_recorded`）；accuracy = 1 + 4 × 没有任何校验问题的论断占比（论断包括每句话和每条 `not_recorded`）；readability 不变（最长一句 ≤ 45 词）；format = 1 + 4 × SBAR 模板检查通过的占比（情况栏只有一句；没有一句话在陈述"缺失"）。
- **怎么证明：**
  1. 新测试：`test_documentation_validator.py` 两个（未记录的话题对照全部输入，包括"尿量是出入量不是化验"；化验时间在来源里，不再报黄），`test_documentation_eval.py` 六个（干净交班四项 5.0；缺一个引用只扣 accuracy 且按比例；一个数字黄项按比例而不是台阶 4.0；带凑数引用的"未记录"句只扣 format；不提疼痛和出入量扣 completeness；情况栏多句扣 format），`test_documentation.py` 的交班测试改为检查 `not_recorded` 和文本里的 "Not recorded: intake and output"。
  2. **证明新测试能抓住旧问题：** 用一个临时 pytest 插件把旧的 `_handoff_proxies` 换回去再跑这六个测试，5 个失败（比如缺引用：旧 accuracy 2.0，新的应为 4.43；带凑数引用的句子：旧 format 5.0，新的应为 4.5），只有"干净交班 5.0"那个两边都过。
  3. 真实模型（`@2`，同 5 个病例 × 2，负责人同意）：7 次 Vertex 卡顿，写出来的 3 份**全部 0 个问题**，都把 `intake_output` 放进了 `not_recorded`，没有病人标志的两例还声明了 `flags`（校验器确认确实没有）；"from 01:00" 不再报黄。样本小，结论是"契约被遵守"，不是"通过率"。
  4. 用新代理重新给 `@1` 的 4 份真实草稿打分：accuracy 从 2.0 变成 3.86–4.5，format 从 3.0 变成 4.5（那句"未记录"现在既算不可溯源又算放错地方：是两个缺陷，各扣一项）。
  5. 注册表、prompt gate、AI Ops、文书、权限和工作流测试共 109 个，108 个通过（`test_agent_registry.py` 改为期望 `nursing_handoff@2`）；失败的 `test_template_only_without_ai_consent` 在 a347f78 上本来就失败（并行会话已记录），和本改动无关。`npx tsc -b` 通过。
- **经验：**
  1. 给模型的每条"缺了就写 X"都要拿去和引用契约对一遍：**"没有"不是一个事实资源，需要自己的通道**，否则模型只能在"如实"和"可溯源"之间二选一。
  2. mock 和用例同一个作者时，mock 会默默替模型解决规格里的矛盾；mock 满分不证明契约是对的。
  3. 运行记录不保留内容时，不要凭分数猜：挑几个病例复现，先 mock 再真模型，对照着看。
  4. 检查代理指标的办法：**故意放一个缺陷，数一数有几项分数动了**。超过一项就是重复计分；台阶函数则会把"多少"压成"有没有"。
- **范围：** hospital（WP7 文书，交班 SBAR）。
- **来源：** `run-2026-10-12T225622` 失败分析；复现脚本和两轮真实调用（2026-10-13）。
- **代码：** `apps/api/prompts/nursing_handoff/2.md`、`apps/api/app/modules/documentation/models.py`（`Handoff.not_recorded`）、`validator.py`（`HandoffValidator.check_document`）、`handoff_agent.py`（化验来源的时间、`render_text`）、`mocks.py`、`evals.py`（`_handoff_proxies`）、`apps/web/src/features/documentation/HandoffPanel.tsx`、`apps/api/config/agents/nursing_handoff.yaml`。
- **提交：** f5e59f3（分支 `claude/vibrant-neumann-40593c`）。

---

## 四、隐私：去标识化的漏洞

4.1、4.2 是由评测里的硬门槛抓出来的：`calls_with_raw_identifiers_to_model = 0`，即"发给模型的文本里原始标识必须为 0"。
没有这个门槛，泄露不会让任何功能出错，因此很难被发现。4.3、4.4 是去标识化本身的 bug，靠"满分要怀疑"和"往返测试"抓到。

### 4.1 来电者口述的出生日期没有被去标识化

- **现象：** 系统 4 的电话 agent 第一次有评测集（14.1），第一次冒烟运行（10-10 15:36:59 UTC）两个 agent 都 20/20，但额外指标显示
  `"calls_with_raw_identifiers_to_model": 7`：20 通电话里 7 通，来电者的出生日期原样发给了模型，凡是中文或法文报生日的都漏了。
  当时它只是额外指标，不是门禁；会话没有在评测集的提交里顺手修，而是提成单独的后续任务。
- **怎么查的：**
  1. 先复现基线：自己的库播种后跑 `uv run python -m app.aiops.evalrun voice_agent call_summary`，两个 agent 20/20，
     `calls_with_raw_identifiers_to_model: 7`（fd-02、05、06、08、11、12、18）。
  2. `Pseudonymizer.redact` 只认 ISO 格式和英文 "Month d yyyy"，而来电者会说 "1984年5月11日"、"4 juillet 1967" 等各种说法。
  3. **主动扩大排查：** 推测通话摘要也会泄露，但网关的调用日志只存输入的哈希（`input_hash=sha256(text)[:16]`），没法从日志里查。于是写探针脚本
     probe_summary.py 截获摘要模型实际收到的文本：8 通泄露，其中 7 通是同样的出生日期，另外 fd-17 的 "Grace Brown"、fd-18 的 "Lin Huang" 是
     **未核验来电者**自己说的名字（网关的去标识化只认识已核验的病人）。
- **根因：** 去标识化的日期规则只覆盖书面格式；通话摘要的转写没有走去标识化，未核验来电者说出的名字也会进摘要。
- **怎么修：** 新增口语日期的正则（中文阿拉伯数字或汉字数字、英法月份、年在前或在后），只在电话 agent 的来电者话轮上启用
  （`redact` 的 `spoken_dates` 选项，默认关闭，"让其他 agent 的行为和评测指标不变"），替换成可还原的 `[DATE_n]` 占位符；
  解析出生日期的 `parse_dob` 能读回所有这些形式；摘要的转写也走同样的去标识化。两处细节：法语月份带重音（août、février、décembre），原来
  "日 月 年"分支用 `[a-z]+` 匹配不上，改成 `[^\W\d_]+`；审 diff 时又收紧一处，一个日期内部的分隔符要一致（和 `parse_dob` 一致），
  免得把 "10.30-11.45" 这种时间段当成日期。
- **怎么证明：** 两个 agent 都是 20/20，发给模型的原始标识从 7 次和 8 次降到 0（探针也是 0），这个指标随后成为门禁。用一组样例单独检查模式：
  `born 12/04/1968` → `[DATE_1]`、dob=1968-12-04，`version 1.2.3`、`on 12/04` 不被替换。`parse_dob` 和 WP8（`voice_services/identity.py`、`followup.py`）共用，
  放宽它可能改变 WP8 的行为，于是在同一个库、同一次运行里只换这个函数，跑 WP8 的三套评测对比：call_center、voice_bilingual 逐行一致；followup 第一次显示
  "不一致"，逐字段比对发现只差 `seconds`（耗时），结论"行为不变"。后端全量 678 通过（原 659 + 新 19），688.20 秒。
- **遗留：** 两个数都 ≤ 12 的数字日期按月在前读（`04/07/1967` 读成 4 月 7 日），而模型现在只看到占位符，没法按来电语言判断。修好后脚本兜底仍是 14/20：
  fd-06 和 fd-12 现在能核验身份了，接着卡在法语请求上（14.1）。
- **经验：** 多语言用户的输入远比测试数据多样。隐私这类"出错也不报错"的属性，必须有专门的评测指标和门槛去盯。日志只存哈希时查不到内容，
  想知道模型实际收到了什么，就在调用处截获。改一个共用函数前，用"只换这个函数"的对照证明别人的行为不变；比出"不一致"时逐字段看，可能只是耗时。
- **范围：** imaging（系统 4 前台电话）。
- **来源：** wt-adoring-raman-2c26f4 10-10 15:36–15:51（发现）；wt-frosty-chaum-60c1c4 10-10 16:53–17:41（修复）；PROGRESS "System 4 eval set"。
- **代码：** `apps/api/app/llm/deid.py`；提交 4fb2f6d。

### 4.2 两个字母的姓（Hu、Li）泄露

- **现象：** "I am Hu's son" 这样的句子里，姓 Hu 没被替换。
- **怎么查的：**
  1. 第一次是 ICU 重新校准后的星期扫描看到的（5.9）：周二数据上 `test_untrusted_text_goes_in_its_own_block_and_cannot_close_it` 失败，发给模型的文本里
     "I am {family}'s son" 没被替换，因为排在第一个的 MEDA 住院病人姓是两个字母。查到 `freetext_deid.add_person` 只收 `len(part) >= 3` 的名字部分（第 263 行）。
     旧生成器的周二数据上这个测试是通过的，是新数据把这样的病人排到了前面（新的周二数据上 Medicine A 的第一个住院病人姓 Hu）。
     当时 `test_agent_runtime.py` 先用 `family_min=3`（只挑三个字母以上的姓）绕过，并提出后续任务。
  2. 为什么只收三个字母以上：为了不把 "He"、"Ma" 这类普通单词误当成名字。生成器的 18 个中国大陆姓氏里有 6 个是两个字母（Li、Wu、Xu、Ma、Hu、He），
     台湾姓里还有 Wu，它们单独出现时就会漏。
- **根因：** 为了压误报，去标识化只把三个字母以上的名字部分加入词典，两个字母的姓单独出现时就漏掉了。
- **怎么修：** 两个字母的名字部分分两类：不是单词的（Hu、Wu、Xu）只要首字母大写就算名字；同时也是单词、医学符号或姓氏前缀的
  （He、Ma、Li、Le 等）只在"像名字的位置"才算：所有格前、同一人名字的另一部分旁边、称谓或亲属关系之后。"He said"、"Li 0.6"、"Le Fort"、"45 HU" 保持原样。
  第一版规则有误报：别人名字旁边的代词也被替换了，于是把"相邻"限定为**同一个人**名字的其他部分；`SHORT_WORDS` 加了人名小品词 "Le Fort"、"De Quervain"、"La Crosse"。
- **怎么证明：** 评测集加入 24 条以单独出现的两字母姓结尾的笔记和一个 "He" 陷阱；规则层召回率 0.9889，去掉这条规则会降到 0.9793。评测生成器的新增部分
  用自己的随机流，另外 176 条笔记逐字节不变。测试原来用子串断言 `family not in sent`，对两字母姓不可靠（Hu 在 "Humalog" 里，He 在 "Heparin" 里），
  改成词边界正则（改这条断言时踩了 15.1 的退格符坑）。会话在周一到周四的播种数据上都跑了 runtime 测试（当天是周五），包括周二的 Hu。
- **经验：** 召回率和误报率总是互相牵制。不要一刀切，按上下文分情况，并为两边都准备测试用例（该抓的和不该抓的）。用子串断言"某个词不在文本里"，
  对短词不可靠。往评测集里加用例时用单独的随机流，旧用例保持逐字节不变，新旧分数才可比。
- **范围：** hospital（WP4 自由文本去标识化）。
- **来源：** wt-gracious-cray-14c437 10-09 14:13–14:14、20:22（发现）；wt-zealous-napier-1d0414（修复）；PROGRESS "WP1 data change · ICU occupancy"、
  "WP4 follow-up · two-letter surnames"。
- **代码：** `apps/api/app/llm/freetext_deid.py`；提交 85a4545。

### 4.3 去标识化评测：先差一点不达标，修好后满分，满分反而说明评测集太简单

- **现象：** 第一次跑自由文本去标识化评测（200 条合成笔记，2,430 个 PHI 片段），规则层召回率 `0.9770`，低于 0.98 的门槛：
  `FAILED (recall >= 0.98, precision >= 0.9)`。
- **怎么查的：**
  1. 按类型统计漏掉的片段：DATE 774 个抓到 720 个（0.9302），PHONE 179 个抓到 177 个，其他类型都是 1.0。
  2. 列出所有漏网的原文：全是 `'29/10/2026'`、`'4375550185'` 这种数字日期和 10 位号码。共同点：都紧挨在句末句号前面。
  3. 原因：前瞻断言 `(?![\w.])` 把句号也排除了，所以句末的日期和号码匹配不上。修好后召回率和精确率都是 1.0000（2430/2430）。
  4. 会话没有就此停下："Perfect scores mean the eval set is too aligned with the rules (I wrote both)"。评测集和规则出自同一个人，满分只说明两边对得上。
  5. 于是加难评测集：生成器加入真实的难例和误报陷阱（2,482 个片段）。重跑后漏了约 20 个：没有上下文提示的名字（'Peter'、'Marcus'）、小写的人名（'yamada'）、
     `"Aug 21st '26"`、`'second of November'`、`'905 5550123'`；误报约 60 个：分数 `'1/2'` 被当成 DATE，`'Practitioner'` 被当成 STAFF，`'Care'` 被当成 PERSON。
- **根因：** 两个问题。一是规则的 bug（句末前瞻）；二是评测集和规则同源，覆盖不到规则没想到的写法。
- **怎么修：** 只改能推广的部分：名字后跟"（亲属关系）"的模式、`'26` 式年份、3–7 位分组的电话、通用职务词、数量名词前的分数。剩下的漏网写法
  （没有提示的名字、小写的 "dr."、文字写的日期）写进 README 的已知失败模式。README 还写明：约三分之一的笔记有一个"故意没有规则针对"的难例；
  这些数字只说明流程能工作，不代表在真实临床文本上的表现。
- **怎么证明：** 规则层 0.9887 / 0.9992（2454/2482），加上第二遍（mock）后 0.9932 / 0.9992，日期偏移 93% 正确。
  `tests/test_freetext_deid.py::test_eval_meets_the_thresholds` 把门槛放进了后端测试。
- **经验：** 自己写规则又自己写评测集时，满分是一个警报。要加入规则没针对的变体和专抓误报的陷阱，再把剩下的失败模式写下来，不要只汇报分数。
  按类型统计漏网、把漏网原文列出来找共同点，比盯着总分快得多。同一个句末前瞻的错误，在文书的可溯源校验器里又出现了一次（14.4）。
- **范围：** hospital（WP4）。
- **来源：** main__40bdfa74 10-08 23:59–10-09 00:04；`evals/deid/README.md`。
- **代码：** `apps/api/app/llm/freetext_deid.py`、`apps/api/tests/test_freetext_deid.py`；提交 dd98b56。

### 4.4 占位符自己出错：还原时多出 `\x00-3`，MRN 的哈希占位符被第二遍又替换一次

- **现象：**
  1. 往返测试 `deid.restore(deid.redact(NOTE))` 和原文对不上。逐字符比较，第一个差异在位置 128：还原结果是
     `'Oct 5, 2026\x00-3; follow-up 10/15\x007'`，原文是 `'Oct 5, 2026; follow-up 10/15'`。
  2. 跑演示脚本 `scripts/platform_demo.py`、读它的输出时，看到 `(MRN [MRN_[ID_1]b7])`：一个占位符里面又嵌了一个占位符。
- **怎么查的：**
  1. 第一个：看 `token_for`。带相对天数的日期占位符是用 `self.pseudo.register(value + f"\x00{span.date_offset}", dated)` 登记的，`register` 会同时写反向映射，
     所以 `dated` 还原成了 `value\x00-3`；后面那句 `_reverse.setdefault(dated, value)` 因为键已经存在，什么也没做。
  2. 第二个：FHIR 去标识化（WP2）给 MRN 生成的是带密钥哈希的占位符 `[MRN_86016769b7]`，而自由文本模块用来识别"已有占位符"的 `TOKEN_RE` 只认 `_\d+`。
     第二遍（mock 检测器）把占位符里的数字当成了 ID，又替换了一次。
- **根因：** 占位符的登记和识别规则不一致。一个是正向和反向映射被同一个调用写死了；另一个是两个模块对占位符格式的约定不一样（一个是纯数字，一个是字母数字哈希）。
- **怎么修：** 日期占位符改用只写反向映射的 `self.pseudo.alias(dated, value)`；`TOKEN_RE` 改为 `\[(?:KINDS)_[0-9A-Za-z]+(?::D[+-]\d+)?\]`，并为 MRN 占位符加了回归测试。
- **怎么证明：** 修复后 `test_freetext_deid.py` 通过；演示输出变为 `(MRN [MRN_86016769b7])`，第二遍只补抓到 `['PERSON']`。
- **经验：** 去标识化必须测"往返"（redact 再 restore 等于原文），并逐字符比较，因为 `\x00` 这种字符肉眼看不到。多个模块共用占位符时，格式要有一处统一的定义。
  端到端跑一次演示脚本、认真读输出，能抓到单元测试漏掉的组合问题。
- **范围：** hospital（WP4，牵涉 WP2 的 FHIR 去标识化）。
- **来源：** main__40bdfa74 10-09 00:11–00:14、00:23–00:24。
- **代码：** `apps/api/app/llm/deid.py`（`alias`）、`apps/api/app/llm/freetext_deid.py`（`TOKEN_RE`）；提交 dd98b56。

### 4.5 两层去标识化叠加：网关的正则会把 FHIR 资源里的临床日期也换掉

- **现象：** WP2 给 25 种 FHIR 资源写了去标识化字段表，但 LLM 网关自己还会再跑一遍正则，会把资源里的 ISO 日期全部换成占位符，模型就看不到临床时间线了。
- **怎么查的：** 资料只记了结论，没有排查过程。WP2 的处理：在钩子做好之前，不让任何模型调用使用 FHIR 资源（记在 WP2 Limits）。
- **根因：** 两层去标识化叠加，第二层不知道第一层已经处理过、哪些日期是临床日期。
- **怎么修：** WP4 让网关接受"已去标识化"的输入（`structured(..., pseudonymizer=)`），只再替换已知的标识，FHIR 资源的临床日期保留；
  日期在自由文本里用带相对天数的类型化占位符（`[DATE_n:D-2]`）。
- **经验：** 多层隐私处理要明确"谁负责哪些字段"，后一层不能对前一层的输出盲目再跑一遍通用规则，否则会破坏有用的信息。4.4 的第二个问题是同一类：
  两层对"什么是占位符"的约定不一致。
- **范围：** hospital（WP2 发现，WP4 解决）。
- **来源：** PROGRESS WP2 "Limits"、WP4 "Free-text de-identification"。

---

## 五、和日期有关的测试：今天过、明天挂

这个项目的合成医院数据按"今天是星期几"生成（周六播种周五的数据，周日播种周一的），影像中心的数据按播种时刻生成。
结果出现一类很折磨人的失败：**同一份代码，周二全过，周日就有 5 个测试失败。** 这一章的统一解法是：
让测试和数据不依赖"今天"，并用**扫描**来证明：按一周里每一天、每隔几个小时播种一次，全部检查一遍。
数据持久化以后还多了一个方向：种子数据会"变老"（5.4、5.7）。

### 5.1 周日的数据里，候补名单上找不到 Mei，真正原因是首次请求要 11 秒

- **现象：** `storyline.spec.ts` 在周日的数据上失败：候补（Backfill）标签页里没有 Mei（等 `getByTestId('candidate-PT-DEMO1')` 5 秒超时）。
- **之前被当成"负载"放过至少四次**（这是本案最值得学的地方）：
  - 10-10 WP6 在线 Playwright：候补步骤在 5 秒处失败（同时有容量工作流在跑），单独重跑 3/3 通过。
  - 10-10 WP8 全量 Playwright：每次失败的步骤都不同（候补页找不到 `PT-DEMO1`、电话取消的回复没出现）。一条假线索：失败后查库，AP-DEMO2 仍是 confirmed、
    没有候补 case，一度以为"取消没保存"、怀疑自己新加的随访扫描干扰写入；后来弄清 Playwright 在失败后会重启 worker 并**重新跑 `beforeAll`（演示重置）**，
    失败之后看到的库并不是失败那一刻的状态。直接用 API 走同样的电话取消：成功，开出 BF-00003，候选第一位就是 PT-DEMO1；在浏览器手动打开候补页，203 毫秒就显示。
    失败快照停在 "Loading…"；`--trace=on` 重跑，网络记录里三个话轮 3353 / 559 / 4414 毫秒，`/api/frontdesk/calls/CALL-00012/end` 没返回（-1），
    随后的 `/api/scheduling/backfill` 和 `/api/scheduling/dashboard` 也都没返回。单独计时话轮 0.82 秒、`/end` 0.92 秒，于是判断是"争用"；
    在不改代码的前提下关掉自己的扫描重跑，仍然失败，排除了 WP8 的改动。结论记为"负载导致的不稳定，需要在空闲机器上再跑一次"：看到了 "Loading…"，
    但没有继续追"是谁在慢"。
  - 10-11 WP10 全量 Playwright：同一失败（`storyline.spec.ts:38`），在新启动的 API 上只重跑两个 spec，`6 passed (2.7m)`，判为 "load/long-run flakes" 写进 PROGRESS。
  - 10-11 hopeful-ellis（周日数据）：候补标签页 5 秒时仍是 `status: Loading…`，同时后端的 5 秒候补计时测试用了 15.6 秒（单独跑 2.4 秒），结论"CPU 负载下候补慢"。
  - 最后，合并全部 WP 后的协调会话在新建的库和新起的 API 上**只重跑这两个失败的 spec**，把偶发（中文随访结束页，见 10.3）和稳定失败（storyline 候补）分开，
    用 `git log e7a2319..HEAD -- <相关目录>` 列出之后改过这些代码的提交，另开会话专门查。
- **怎么查的（修复会话）：**
  1. 任务说明的假设是"周末取消的时段和 Mei 的约束不匹配"。先读种子里的故事线、`rank_waitlist`、`BackfillTab.tsx` 和前台取消的代码路径；后端的排序测试在周日数据上是通过的。
  2. 于是怀疑只在端到端测试里运行的东西：API 的后台循环（后端测试里 `BACKGROUND_WORKERS=0`，不跑这些循环）。
  3. 在主工作区的 `apps/web/test-results/` 里找到上一次失败留下的 `error-context.md`：候补标签页一直显示 "Loading…"，说明**不是数据错，是没加载完**。
  4. 起一个自己的 API（端口 9457，mock 模型，周日数据）单独跑 spec：失败了，但失败在另一步（电话那一步 5 秒内没等到 "you're verified"）；CPU 100%（4 核，和其他会话、Docker 共用）。
  5. 写脚本直接调 API 计时（time_storyline.py）：演示重置 50.06 秒；电话各轮 0.14–2.36 秒；第一次 `GET backfill` **11.09 秒**；`GET dashboard` 1.28 秒；
     第二次 `GET backfill` 0.14 秒。返回的 BF-00001 里 Mei（PT-DEMO1，P2）排第一。排序没错，是第一次请求慢。
  6. 追到后端：演示重置后的第一个排程请求（`get_store_ready`）要训练爽约模型，并为 168–246 个高风险预约逐个排队额外提醒，每个都要一次数据库往返。
     用 time-machine 固定在周日，对 `get_store_ready` 做 cProfile：重置 71.8 秒，`get_store_ready` 74.9 秒（commit 6.1 秒）；额外提醒 210 条；
     评分的未来预约 2,341 个、其中高风险 416 个；250 次 SQL execute 共 62.9 秒（每次约 0.25 秒）；爽约模型训练里的 `_prior_counts` 8.3 秒。规格要求 5 秒。
  7. 最后才正式排除"时段日期"的假设：按一周每天的 00:00、13:00、23:00 播种，并在播种时刻和 23:59 取消 AP-DEMO2，Mei 每次都是第一候选。
     理由也写进了 PROGRESS：AP-DEMO2 在 Lakeshore 下一个营业日的 10:00，至少在 10 小时之后，而增强 CT 只需 4 小时准备；其余 P2 条目都被排除
     （只接受 Eastview；5 天内没准备好），随机 CT 条目是 P3/P4。之前周六能通过，是因为当时机器比较闲。
- **根因：** 重置后第一个排程请求在请求里做大量补算（训练爽约模型、逐个排队额外提醒），周日的数据上要 11 秒，负载下 75 秒。
- **怎么修：** 把这项工作挪到生成器的最后一步（`_add_noshow_risk`），在内存里完成；`populate()` 和 `build_store()` 接受可选的 `now`，测试可以按任意时刻播种。
- **怎么证明：** 新的生成器步骤每次构建多花约 0.65 秒（重置只多约 0.7 秒）；首次候补请求从 11.1 秒降到 0.03 秒。案例编号变成了 BF-00002，会话专门查了一下：
  只有一个案例，编号是接着之前的计数排的。故事线 spec 在周日（3/3，用 `--timeout=180000`，因为负载下 `beforeAll` 的重置约 57 秒）、周六（API 用 time-machine
  拨回 1 天，Vite 注入的 `Date` shim 也拨回 1 天）、周二（拨快 2 天，时段是周三 10:00）都通过，都由 Mei 补位。影响检查：只有故事线 spec 读 outbox，
  而且只取 id 最大的那条消息，所以在种子里提前生成提醒不影响其他测试。
- **遗留（记在 PROGRESS，没修）：** 改动之前播种的数据库，第一次排程请求仍要做这些工作；排程页同时请求 dashboard 和 backfill，两个请求可能都去生成提醒
  （`if "extra_reminders_done" not in store.modules` 是先检查再写入）。做一次演示重置即可。
- **推测：** 7.2 里后端 5 秒候补计时测试在 10-10、10-11 的多次失败（约 10.4、9.3、15.6 秒），可能和本案是同一个根因：这个测试计时的是
  `POST /appointments/AP-DEMO2/cancel`，路由第一步就是 `service.get_store_ready()`，3c610c2 之前它会在 `extra_reminders_done` 不存在时跑 `queue_extra_reminders`
  （`apps/api/app/modules/scheduling/router.py` 第 92–95 行）。这是从代码推断的，没有验证。
- **经验：** **先看失败现场（截图、快照），再猜原因。**"找不到某条数据"和"页面还没加载完"在断言层面看起来一模一样。"单独重跑能过"只说明它对负载敏感，
  不说明没有 bug；失败点总在同一个页面的首次加载时，要追"是谁在慢"。负载会放大真 bug，也会掩护它。失败之后看到的数据库状态，未必是失败那一刻的状态
  （测试框架可能已经重跑了准备步骤）。
- **范围：** imaging（排程与候补，影像故事线）。
- **来源：** wt-wp6-med-safety-570354 10-10 01:59–02:02；wt-wp8-voice-services-addfd5 10-10 09:45–10:01；wt-wp10-roadmap-docs-6e66e7 10-11 09:47–09:52；
  wt-hopeful-ellis-36d8fc 10-11 09:42–09:46；main__7a422c19 10-11 15:01–15:07；wt-nifty-bose-77983d 10-12（修复）；PROGRESS 最后一节和 WP6、WP8、WP10 Results。
- **代码：** `apps/api/app/seed.py`（`_add_noshow_risk`）；`apps/api/tests/test_flagships.py`；提交 3c610c2。

### 5.2 周六技师的预约列表是空的

- **现象：** `radiology-ops.spec.ts` 每到周六就在第一步失败：`radiology-ops.spec.ts:31` 的 `locator('[data-testid^="complete-"]').first()` → `element(s) not found`。
- **怎么查的：**
  1. 最早出现在 WP7 的全量 Playwright（周六 10-10，30 通过、2 跳过、1 失败），单独重跑同样失败。WP7 查了 `AppointmentsTab.tsx` 里 Complete 按钮的渲染条件，
     认定是周末现象（"种子只在营业日预约影像"，WP7 没动过影像排程），记进 PROGRESS；WP8、WP10b 当天也看到同一失败，沿用这个判断，记成"周六的已知失败"。
     当天只判断出"是周末导致的"这个**条件**，具体**机制**要到专门修复时才找到。
  2. 修复会话从 spec 第一步的 "Mark done" 按钮（`complete-` 测试 id）找到 `AppointmentsTab.tsx`：默认日期是 `today()` 加一天。再用 API 核对数据：周六 10-10 播种，
     Lakeshore 当天 113 个预约（80 个未完成）、周日 10-11 为 0、周一 10-12 有 100 个未完成。证实是界面默认日期的问题。
  3. 为什么不改种子（任务说明倾向种子修复）：所有站点周日都不营业，在种子里种周日的检查会和站点的营业日矛盾。
  4. 扫描其他 spec 和后端测试：没有别处按星期挑影像日；business-compliance 已经通过 API 查 AP-DEMO2 所在的日子。
- **根因：** 预约标签页默认打开"明天"。周六的明天是周日，而所有站点周日都不营业，所以列表是空的。数据没错，错的是界面默认日期。
- **怎么修：** 默认打开所选站点**下一个营业日**（站点的营业日从 `/api/meta` 获取）。
- **怎么证明：** 新测试把浏览器时钟设到下周六和周日，期望打开下周一并有可完成的检查。动手前先 grep 确认前端不会拿浏览器时钟去比对令牌过期时间，
  否则改时钟会让登录失效。证明新测试有效：把旧的 `AppointmentsTab.tsx` 临时从 HEAD 换回来，新测试在"Day 是周一"这一步失败，再用 `cmp` 确认修复文件原样放回。
  扫描结果：周六（真实时钟）3/3；周日 2/2（周日 0 个预约，周一 101 个，技师把周一的一个标记完成）；周三 2/2；周五 2/2（下一个营业日是周六）
  （API 用 time-machine 改时钟，浏览器的 `Date` 同步偏移）。
- **经验：** 默认值里的"明天""本周"都暗含日历假设，要考虑周末和节假日。一个失败被几个工作包记成"已知失败"时，它就不再有人查；记的时候写清楚只知道条件、不知道机制。
- **范围：** imaging（排程，放射科运营）。
- **来源：** wt-wp7-documentation-c03e03 10-10 05:12–05:16；wt-cool-cartwright-7673d9 10-10 15:04–16:06（修复）；PROGRESS WP7 Results、阶段 3 Demo-scope notes。
- **代码：** `apps/web/src/features/scheduling/AppointmentsTab.tsx`；提交 a454d12。

### 5.3 用药安全的预埋病例依赖星期几

- **现象：** 周日（实际生成周一的数据）有 5 个后端测试和一个 Playwright 测试失败；周四的数据还有两个别的问题。最早是 WP9 在 10-11 看到的：
  `test_workflows.py` 和 `test_med_safety.py` 27 passed、5 failed（`the seeded hospital should have exceptions at 07:00 / assert 2 >= 3`；MedRec 的 AI 步骤
  `assert ('refused' == 'done'`），这些测试前一天全量都过了。WP10 的全量后端里也看到同样的 5 个（769 个测试，764 passed）。
- **怎么查的：**
  1. WP9 先怀疑日期依赖，在基线 a454d12 的干净 worktree 和一次性库上跑同样的 5 个测试：同样失败，确认是既有问题、与 WP9 无关，提议了单独的会话（见 14.5）。
  2. 修复会话写脚本对脱离数据库的 Store 按指定时刻生成数据（`uv run --with time-machine`），例如墙上时间 2026-10-10 03:30（周六）生成的是医院时间
     2026-10-09 07:00（周五）的数据；再对 8 个日期并行生成，打印"检测到的异常、各病房空床、quetiapine 病例的同意书和 AI 结果"。
  3. 逐个看失败原因，发现都和"预埋病例挑了哪个病人"有关：
     - 预埋的入院用药核对病例挑的是"最新入院的病人"，不管他是否同意 AI 处理；周一那位的 `ai_processing` 同意是 deny，周四是缺失，于是 agent 被拒绝
       （`consent_missing: medication_reconciliation needs the patient's consent (ai_processing: deny)`）。
     - 周四过去 24 小时没有髋部骨折入院，"髋痛用萘普生"的病例落到了一个脊柱融合病人身上，药物就没有对应的适应证。
     - 容量工作流测试要求 07:00 有 3 个开放异常，周一 07:00 只有 2 个，都是手术超时（周末没有择期入院，外科 59% 满；周二到周五是 7–15 个）。
       用 `git log -L` 追到"至少 3 个异常"的断言来自 WP4c 的 da35c54。
  4. 周四还有一个 spec 层面的假设：8.2 病人同时在用华法林和**依诺肝素**，所以有两条 major 的萘普生相互作用，而规则结果只按严重度排序，`finding().first()` 拿到的是依诺肝素那条。
- **根因：** 预埋病例用"最新的一个"挑病人，没把故事需要的前提写成条件；测试断言了数据在某些星期才满足的数量。
- **怎么修：** 预埋病例只挑同意 AI 处理的病人，优先挑髋部骨折；测试只要求实际用到的 2 个异常，并加了一条"批准后确实有要执行的动作"的断言，免得测试空转通过；
  spec 改为按内容找华法林那条（先确认 `source-rule` 标记在 finding 元素内部，限定范围的断言才成立）。新回归测试按周日和周四播种检查预埋病例是否符合故事设定。
  数据影响：周二、周三、周五不变；周一和周四的核对病例与重复检查病例互换病人；周四的 8.2 病例换成髋部骨折病人。
- **怎么证明：** 提交前把回归测试的检查直接跑在**基线提交的** `medsafety.py` 上：周日的核对病人同意书是 deny、周四是 missing、周四 8.2 病例的住院原因是 55705006
  （脊柱融合），全部失败，证明这个回归测试确实能抓住旧 bug。同一会话还修了 2.14（它在周一的数据上更容易出现）。
- **经验：** 预埋演示数据时，把故事需要的**每个前提**都写成挑选条件（同意书、诊断、科室），不要用"最新的一个"（同一个教训见 12.9）。按严重度排序的结果里
  "第一个"可能不是你想要的那个，按内容定位。
- **范围：** hospital（WP6 用药安全，WP4c 容量测试）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 04:46–04:59；wt-hopeful-ellis-36d8fc 10-11 07:21–09:54（修复）；PROGRESS WP6 follow-up。
- **代码：** `apps/api/app/ehr/seed/medsafety.py`；提交 95c8eef。

### 5.4 账单的预埋差异，播种一小时后就掉出窗口

- **现象：** 15:00 播种，16:00 刚过，"重复账单"这条预埋差异就找不到了，测试失败。最早是 ICU 校准会话重启后的全套测试里看到的：
  `test_reconciliation_finds_every_planted_discrepancy` 找不到预埋的重复账单 `AP-11325`（同一测试当天上午还通过）。
- **怎么查的：**
  1. 探针输出 `seed_time 2026-10-09 16:00:00 now 2026-10-09 16:04:39 window start now 2026-09-09 16:04:39`，确认是"播种时刻取整到小时、对账窗口按运行时刻算"的错位；
     按 16:00 播种时 `test_phase4.py` 30/30 通过。当时作为后续任务提出。
  2. 修复会话在内存里按 15:00 播种，然后在 +65、+120、+1440、+1505 分钟对账：+65 分钟就漏了 `('duplicate', 'AP-11325')`，+1440 分钟又多漏了 AP-11456、AP-11501 等。
     `populate` 把 now 向下取整到整点；26 条预埋从已完成的检查里挑、4 条从取消或爽约的检查里挑。
- **根因：** 种子在"播种时刻之前 30 天"里挑检查，对账却从"运行时刻往前 30 天"算。落在窗口第一天前几个小时的预埋检查，很快就滑出窗口。
- **怎么修：** 落在窗口第一天的挑选，和之后最早的未挑选检查交换位置。**不增加、不改变随机数的抽取顺序**，所以其余数据完全不变。
- **怎么证明：** 新旧种子各按 10-08 和 10-09 的每个小时播种（共 48 次）：排除三个每次运行都会变的字段（14.6）后，账单以外的数据完全一致；
  48 个时刻里 33 个移动了预埋记录（共 61 条），例如 10-08 00:00：`'missing AP-11266->AP-11392', 'not_performed AP-11326->AP-11390'`。新种子在播种时、
  65 分钟后、一天后都能找到所有预埋差异，旧种子有 33 个时刻一天后会漏（10-09 的 14:00、15:00 播种 65 分钟后就漏）。Playwright `business-compliance.spec.ts` 8/8
  （这个库是 17:00 播种的，正好有两条预埋被移动，说明走到了新代码）；`test_phase4.py` 在 7 个播种时刻、播种后约 1–22 小时测试，都是 31/31。
- **经验：** 时间窗口相关的逻辑，测试要覆盖"窗口边缘"和"时间流逝之后"。修种子时保持随机数抽取顺序不变，能把改动范围限制在最小。
  这次扫描还踩了三个工具坑：加载旧模块时的类对象（13.5）、对照基线本身不确定（14.6）、Git Bash 的 `date` 误解析（第六章），合并 PROGRESS 时又遇到换行符假冲突（16.4）。
  它的前身是 5.7："种子变老"的另一个方向。
- **范围：** imaging（系统 18 账单）。
- **来源：** wt-gracious-cray-14c437 10-09 20:02–20:07（发现）；wt-lucid-mestorf-0d5ad0 10-09 20:41–21:11（修复）；PROGRESS ICU 一节 "Found on the way"、"Fixed afterwards"。
- **代码：** `apps/api/app/modules/billing/service.py`；提交 d2955ff。

### 5.5 小站点偶然更差，中文问卷有时不存在

- **现象一：** 演示需要 Westbrook 是 30 天平均分最低的站点，但小站点回答很少，偶尔会碰巧更低（72 个播种时刻里 6 个失败）。WP4b 的全量测试先看到它
  （`1 failed, 354 passed`，`assert 2.64 < 2.6`）。
- **怎么查的（一）：**
  1. 先确认不是自己造成的：用 `git worktree add --detach <scratchpad>/base HEAD` 检出 dd98b56，拷进 `.env`，在里面单独跑这个测试，同样失败，于是开了单独的修复会话。
  2. 复现：内存播种 22:00 时 `margin=-0.04`，`{'MTN': (2.6, 5), 'WBK': (2.64, 11)}`：Midtown 30 天只有 5 条回答（会话一开始把 MTN 误写成 "Mountain"，查站点表后改正）。
     另一个原因：种子的"上个月"按检查日期算，看板按回答日期算；小站点 30 天只有 2–8 条回答。
  3. 旧种子 72 小时扫描：6 个时刻失败（有的在播种一天后才失败，10-09 11:00 的 margin 是 -1.20）。
  4. 第一版修复（只把 Westbrook 最好的一条近期回答改成投诉）做压力测试：40 条随机流 × 72 小时 = 2,880 次播种，每次检查 4 个窗口，共 11,520 次，56 次达不到 0.25 的差距。
     详细输出显示 Midtown 30 天里可能只有 2–3 条回答、平均低到 1.33–2.5，光压低 Westbrook 不够，于是加第二个手段。等 Docker 和 Postgres 启动的时候，会话先在内存里做压力测试，没有干等。
- **修法（一）：** 抽完随机数后检查接下来一天里每个 30 天窗口，不满足就把 Westbrook 最好的一条近期回答改成等待时间投诉，或把最接近的站点最差的回答改成好评。
  压力测试 0 次未达标；421/2,880（约 15%）的播种需要改写，最多改 9 条，平均 1.87 条；新种子的 72 小时扫描 0 失败，最小差距正好是 +0.25。
  回归测试拿旧种子跑，24 个时刻里漏 4 个（10-06 05:00、08:00、11:00、14:00），证明它能抓到问题。
- **现象二：** 系统 19 的 Playwright 测试需要一份未完成的中文问卷，但它只从最近完成的 40 个检查里来，有些时刻一份都没有。
- **怎么查的（二）：** 第一次在 WP2 的 Playwright 回归里遇到。**第一个假设是错的**：会话以为是上一次误用真实 Gemini 的运行（10.6）留下的状态把中文问卷答掉了；
  重置数据后重跑仍然失败，于是承认"解释错了，不再猜"。再看失败快照：列表只显示最新的问卷，唯一的中文问卷（10-02）已回答；读 `seed()`：只有最近完成的 40 个检查里
  有中文病人时才会预埋未答的中文问卷；直接查当时的数据：未答问卷只有 `pa` 1 份、`en` 1 份，`zh` 为空。修复会话（任务说明还指出 `GET /api/feedback/surveys` 只返回最新 15 份）
  脱离数据库按 12:00–17:00 每小时播种，16:00 是 `BAD 0`，复现到具体时刻。
- **修法（二）：** 没有时取更早的中文病人检查补发一份。要求是不改变其他种子数据（随机数抽取顺序不变或用单独的流）。新测试
  `test_seed_plants_an_open_chinese_survey_even_without_a_recent_chinese_patient` 用 `git stash` 去掉修复后确实失败；从 2026-10-08 00:00 起每小时播种共 72 次全部 OK；
  按 16:00 播种的数据跑 spec 8/8（覆盖兜底分支），正常重播后再跑一次也过。
- **经验：** 随机数据加"演示需要的固定事实"时，先随机生成，再**显式校验并修正**那几个必须成立的事实。修正规则要拿很多条随机流做压力测试，
  只扫一条流的 72 小时会漏掉边缘情况。假设被推翻时，像这里一样明说"解释错了"，回到现场重新看。
- **范围：** imaging（系统 19 患者反馈）。
- **来源：** main__21cd9296 10-09 03:17–03:19；main__56196a27 10-09（Westbrook 修复）；main__4f2eefc4 10-08 20:29–20:36；main__bec5acdb 10-08 22:26–22:47（问卷修复）；
  PROGRESS WP4b Limits、WP2 Playwright regression。
- **代码：** `apps/api/app/modules/feedback/service.py`；提交 7efa9da、34602b9。

### 5.6 扫描所有星期，扫出了一个真 bug

- **现象：** 在周五播种的数据上，6 个医院测试失败。交给修复会话的任务说明假设这是"03:00 播种"的问题。
- **怎么查的：**
  1. 读代码 2 分钟后，会话就修正了任务说明的假设："The hospital generator keys off 07:00 on the seeding date, so results hinge on the weekday"。
  2. 用脱离数据库的生成器 55 秒扫完一周：每天打印第一个 ICU 病人是否在医生权限范围内及原因，以及入院数。只有周五（周六 03:00 播种也生成周五）显示
     `in scope: True ['attended:stay-01040:in-progress:ICU-09-A']`、`admit 7`；其他日子入院 19–26 人。ICU 住院病人里属于演示医生的：周五 3/7、周一 2/5、周三 1/4。
  3. 用 pytest 插件 `seedat`（把 `app.seed.datetime.now()` 冻结在 `SEED_AT`）在 HEAD 的原测试上以周五 03:00 播种，复现出完全相同的 6 个失败（每轮约 4 分钟，主要是播种）。
  4. 6 个失败里 5 个默认"第一个 ICU 病人不在 Medicine A 医生的权限范围内"。周五那位恰好是这位医生自己的病人，所以网关正确地放行，不需要 break-glass。
     数据和权限规则都没错，是测试的假设错了。`scripts/platform_demo.py` 也有同样的错误假设（ICU 病人对医生显示 "ok"，break-glass 场景失效），一起改了。
- **意外收获：** 为了确认修好，按每个星期几扫了一遍，结果发现**周二有一个真 bug**：在计划时间内决定入院、但到计划结束后才有床位的急诊病人，
  在计划里没有住院记录，模拟器的决策步骤抛出 `KeyError: 'stay-01162'`。周二 10-13 12:00 这个扫描点刚失败时，会话先怀疑是"播种时刻在真实时钟 4 天之后"造成的假象，
  单独重跑后才确认是真 bug。再按星期统计"计划里引用了、但计划里不存在的住院"：周一、周五也有这种悬空引用，但决定时刻不在计划日内，所以不会触发；
  只有周二有 9 个入院决定落在计划日内（14 个病人候床到 19:00 计划边界之后）。
- **怎么修：** 测试改为挑一个既不在 Medicine A、也不由该医生负责的 ICU 病人；`generate()` 只为"急诊入院决定落在计划日内"的病人补上住院计划，
  所以周一、三、四、五的计划完全不变，周二多 9 条。
- **怎么证明：** 扫描和全量套件在单独的 worktree 里只跑这个提交（16.1）：27 个播种点通过，全量 395 passed。还验证了"数据只取决于星期几"：周一播种和周日播种（生成周一）
  得到完全相同的 5,936 个就诊、64,475 个资源、1,653 个计划事件；相隔一周的两个周一，就诊、床位、主治、资源数和计划都相同。
- **一个误读：** 协调会话转述扫描进度时说 "Friday 03:00 passes … the 6 failures may not come from the seeding hour at all"，据此判断失败可能取决于测试运行时间或环境。
  这是误读：那次扫描跑的是**已经修好的**测试。转述别人的扫描结果时，要先弄清它跑的是哪个版本的代码。最终根因确实是"星期几"而不是"几点"，修复提交的标题也是按星期写的。
- **后续：** 1b4bbc6 之后，旧生成器在周二、周三的数据上 `test_bed_manager_moves_a_patient` 和 `test_bed_moves_are_refused_for_other_roles_and_states` 仍然 `StopIteration`
  （测试取第一个住院病人，而他所在病区没有空床），在 1c36c9e 里修好（5.9）。
- **经验：** **扫描**（把所有星期、所有时刻都跑一遍）不只用来验证修复，还会顺带发现你没在找的 bug。读任务说明里的假设时先读代码验证它，
  这里两分钟就把"几点"改成了"星期几"。
- **范围：** hospital（WP1 生成器、WP4 权限测试）。
- **来源：** main__04f34599 10-09 07:23–08:26；main__7a422c19 10-09 07:23–07:32（协调）；PROGRESS WP4b Limits 第二条。
- **代码：** `apps/api/app/ehr/seed/__init__.py`（`generate()`）、`apps/api/scripts/platform_demo.py`、`apps/api/tests/test_access.py`、
  `apps/api/tests/test_break_glass.py`；提交 1b4bbc6。

### 5.7 种子会变老：账单对账在持久化数据上多出一串 missing

- **现象：** `test_reconciliation_finds_every_planted_discrepancy` 的 `assert found == planted` 失败，多出一串连续预约的 `('missing', 'AP-16527')`、
  `('missing', 'AP-16535')`……（10-07 下午），晚上在 PostgreSQL 上又出现（`AP-16681`…`AP-16691`）。
- **怎么查的：**
  1. 下午第一次遇到时（部署配置会话），用 `git stash` 去掉自己的改动再跑：同样失败，确认是已有问题，看起来和时间有关，先记下没处理。
  2. 晚上在 PostgreSQL 上：先判断是不是和 14.7 一样的"测试拿着旧对象"问题。多出来的是一串**连续**的预约，更像真实结果。读 `reconcile()`：
     检查完成超过 `SUBMISSION_DAYS = 2` 天还没有账单就记 missing；种子的 `now` 被截到整点。
- **根因：** 种子生成时，最近完成的检查还没到"该有账单"的时间；时间往前走、跨过 2 天这条线以后，它们被**正确地**标成 missing。
  内存 Store 每次启动都重新播种，所以以前不明显；数据持久化以后，种子数据会一直变老。
- **怎么修：** 种子把自己的时钟存为模块状态 `seed_time`；测试排除"完成时间晚于 `seed_time - SUBMISSION_DAYS`"的 missing（`assert found - late == planted`）。
- **怎么证明：** 该测试通过，整套 154/154。
- **经验：** 和 5.4 是同一类问题的两个方向：一个是该有的掉出去，一个是不该有的长出来。数据一旦持久化，测试里所有"和现在比"的判断都要以种子的时钟为准，
  而不是以运行时的时钟为准。区分"测试的毛病"和"真实结果"时，看失败的形状：一串连续的记录更像真实的业务结果。
- **范围：** imaging（系统 18 账单，阶段 0 数据层迁移）。
- **来源：** main__b1ffba74 10-07 14:23–14:24；main__394ebe0a 10-07 23:22–23:24；PROGRESS 阶段 0 "Finishing work"。
- **代码：** `apps/api/app/seed.py`（`seed_time`）；提交 9458db0。

### 5.8 日程模拟器："快进到明早 8 点"在 7 点时变成今天 8 点；测试假设计划里一定有某个事件

- **现象：**
  1. `test_a_day_in_order_with_six_event_types_and_valid_resources`：`assert datetime(2026, 10, 8, 8, 0) == datetime(2026, 10, 9, 8, 0)`。
  2. 修好后同一个测试又失败：要求的事件类型集合里缺 `patient.transferred`。
  3. 全套 303 个里 1 个失败：API 测试 `KeyError: 'patient.admitted'`。
- **怎么查的：** 第 1 个看断言就清楚：医院时钟锚定在播种日 07:00，"明早 8 点"按"今天 8 点还没到就用今天"算成了今天 08:00。
  第 2、3 个看当天的计划：这个播种日的 36 小时计划里没有 ICU 转出（step-down transfer）；前 30 分钟里也不一定有入院。
- **根因：** 1 是时间计算的边界条件（只有凌晨才该算"即将到来的早上"）；2、3 是测试假设了生成数据里一定会出现某个事件，而生成器并不保证。
- **怎么修：** `next_morning()` 只有在 05:00 之前才取当天的 `hour:00`，否则取第二天；测试改为要求 7 种工作日一定出现的事件类型、总共至少 6 种；
  转运另写一个直接测转运处理函数的测试（计划里放一条转运）；API 测试不再假设前 30 分钟一定有入院。
- **怎么证明：** WP3 的 38 个测试和全套 304 个通过。
- **经验：** "明天""下一个早上"这类表达要写清边界（几点之前算今天）。依赖生成数据的测试只能断言生成器**保证**的东西，想测某个分支就自己构造输入，
  而不是指望随机数据里碰巧有。
- **范围：** hospital（WP3 日程模拟器）。
- **来源：** main__7a422c19 10-08 22:56–22:59、23:09。
- **代码：** `apps/api/app/ehr/simulator.py`（`next_morning`）、`apps/api/tests/test_day_simulator.py`；提交 5e9db1a。

### 5.9 改了数据就按星期扫一遍：用"新旧代码对照"分清新老问题，扫出两个模拟器边界 bug

- **现象：** 提高 ICU 比例之后（8.2），周五数据上的全套后端测试 416 过 1 挂（`test_planned_transfer_moves_the_patient_and_leaves_the_old_bed_dirty`：
  外科在 07:00 满床 32/32，测试假设外科有空床）。改好后，把医院测试文件（163 个）按周一到周四各播种一次并行跑：周一全过；周二 3 个、周三 1 个、周四 3 个失败。
- **怎么查的：**
  1. 怎么扫：在 scratchpad 写一个 pytest 插件 `seedtime`（`-p seedtime`，用 `SEED_AT` 指定播种时刻），每个星期几一个独立测试库（`ioc_test_icu_mon` 等），4 个并行。
     先用 SQL 确认插件真的换了医院日：四个库的 ICU 在院人数 10/9/8/10，和草稿脚本测的一致。
  2. 失败归成 4 类：床位移动测试 `StopIteration`（病人所在病区没有空床）；时钟停止测试 `assert state["remaining"] == 0`（得到 1）；ICU 激增测试
     `assert boarded >= 1`（得到 0）；不可信文本测试（周二，一个两个字母的姓没有被去标识化，即 4.2）。
  3. **对照实验**：把 `reference.py`、`simulate.py` 暂时恢复成 `git show HEAD` 的旧版本，在同样的周二到周四数据上只跑这 5 个测试。旧生成器：周四 5 个全过；
     周二、周三两个床位移动测试同样失败。结论：床位移动是**老问题**（换数据前就挂，见 5.6 的"后续"），其余是新数据暴露的。
  4. 周三的激增：查周三库的 ICU 床位状态：占用 8 张、空 3 张、待清洁 1 张。激增默认"填满 ICU 再加一"只数了空床，送来 4 个病人，正好有一张床清洁完，于是没人滞留。
  5. 周四的时钟：怀疑 `remaining` 把地平线之后的事件也算进去了。写一个临时探针测试，对比推进前后计划里超过地平线的事件：推进后多出一个
     `{'at': '2026-10-09T19:20:58', 'kind': 'bed_clean', 'bed': 'ICU-04-A', 'derived': True}`，而地平线是 19:00。临近结束时有病人腾床，模拟器派生出一个清洁事件
     （`CLEAN_MINUTES = (45, 100)` 分钟后），落在地平线之外，永远不会执行；旧数据的周四没有这么晚腾床，所以以前没暴露。
- **根因：** 两处模拟器边界：（1）`icu_surge` 的"空床"不含待清洁的床；（2）`simulator.status` 的 `remaining` 计入了地平线之后、永远不会执行的派生事件
  （延后入院的重试、清洁）。另有测试假设问题：床位移动和转科测试假设某个病区一定有空床。
- **怎么修：** 激增按"空床 + 清洁中"计数（周五没有清洁中的 ICU 床，所以周五的报告和演示不变）；`status()` 只数地平线之前的事件；测试改为挑一个
  "所在病区有空床"的住院病人（`_movable_inpatient`）；去标识化测试暂时挑三个字母以上的姓，并提出后续任务（后来的 4.2）。
- **怎么证明：** 第二次扫描周一到周四全部 163/163；重启后周五全套 416 过 1 挂（挂的是影像账单，即 5.4，和本分支无关）；Playwright 25/25。
- **经验：** 改了数据之后的失败要先分清"我引入的"还是"本来就有的"：把旧代码临时放回去，在**同一份数据**上重跑失败的测试，是最便宜的对照实验。
  边界 bug 常在"临近结束""刚好差一个"的地方：派生事件会越过时间地平线，"空闲"的定义可能漏掉中间状态。扫描前先确认扫描工具真的生效了（这里用 SQL 查各库的 ICU 人数）。
- **范围：** hospital（WP1 数据、WP3 模拟器、WP4 测试）。
- **来源：** wt-gracious-cray-14c437 10-09 14:00–14:28；PROGRESS "WP1 data change · ICU occupancy"。
- **代码：** `apps/api/app/ehr/scenarios.py`、`apps/api/app/ehr/simulator.py`、`apps/api/tests/test_fhir_gateway.py`（`_movable_inpatient`）；提交 1c36c9e。

### 5.10 计划时间范围差一天：随访时刻在范围外，时钟推进原地空转

- **现象：** WP9 进程内走查 1–10 步通过，第 11 步 `AssertionError: the clock did not reach followup`。
- **怎么查的：** 对照出院时刻和计划的 horizon。`HORIZON = timedelta(days=4, hours=6)`，注释写着 "07:00 on day 0 to 13:00 on day 4: discharge on day 3, call 48 h later"；
  而出院在第 3 天 11:00，48 小时后的随访在第 5 天，已经超出 horizon。`simulator.advance` 会把目标时间截到 horizon，所以推进循环在 horizon 边上原地打转直到超时。
  预置本身也只计划 horizon 内的事件（`if self.now < at <= self.now + HORIZON`）。
- **根因：** horizon 算少了一天：注释写了"48 小时后打电话"，数值却没覆盖到。
- **怎么修：** HORIZON 改为 5 天 6 小时（到第 5 天 13:00）；测试断言 horizon 晚于第 5 天 11:06；harness 和服务的推进在目标超出 horizon 时立即报错
  （"Past the hospital plan's horizon: reset the storyline"），不再空转。
- **怎么证明：** 之后第 11 步通过。
- **经验：** 时间边界（窗口、horizon、截止时间）手算容易差一。越界时"立即失败并说明原因"，比静默截断后等超时好查得多。
- **范围：** hospital（WP9 故事线预置）。
- **来源：** wt-wp9-storyline-7d11cd 10-10 23:44–23:45。
- **代码：** `apps/api/app/ehr/seed/journey.py`（`HORIZON`）；提交 8d3cd5e。

### 5.11 未查明：影像积压看板测试在全量里失败，单独跑通过

- **现象：** 全量后端测试（1018.93 秒，`1 failed, 419 passed`）里，`tests/test_phase3.py::test_board_updates_with_new_studies_and_sign_offs` 失败：
  `assert (3 > 1 and 199 == (199 + 1))`。`simulate-completion` 之后版本号变了，但未读数（`unread`）没有加 1。
- **怎么查的：** 先确认这次修改（两字母姓去标识化，4.2）和积压模块无关：grep 显示积压模块从不调用去标识化。读代码看到 `simulate-completion` 依赖时钟和当天的排程。
  单独重跑该文件：29/29 通过。
- **根因：** **未查明。** 会话的判断是"和时间有关的偶发失败"（推测），可能是新完成的检查没有算进未读，或者被其他并发写入抵消，但没有证据。git 历史里也没有后续修复。
- **经验：** "先排除和自己修改的关系，再单独重跑"只够判断失败不是自己造成的，不等于修好了。下次遇到，用本章"按任意时刻播种"的扫描去复现它。
- **范围：** imaging（系统 11 积压看板）。
- **来源：** wt-zealous-napier-1d0414 10-09 21:08–21:11。

### 5.12 周二的"编造诊断"是真的：预埋病人恰好因肺炎入院

- **现象：** 10-13（周二）重测 Gemini agent 时，全量后端 `807 passed, 1 failed`：`tests/test_documentation.py::test_template_only_without_ai_consent`
  第 196 行 `assert (False or False)`。测试让医生在"无 AI 同意、只有模板"的出院小结里加一条诊断 "Pneumonia"，引用的是另一条诊断的来源，
  期望校验器给出红色的 `untraceable_diagnosis` 或 `source_mismatch`，结果两种都没有。在未修改的 main a347f78 上同样失败，与 provider 改动无关
  （trusting-hypatia 的交接记录里也记成 a347f78 上的既有失败）。
- **怎么查的：**
  1. **先读判定逻辑，列出"什么输入会得到这个输出"。** `TraceabilityValidator.check_item` 对带类型的条目（诊断）先按 code、再按名字
     （`lexicon.same_name`：规范化后相等、词集包含、difflib ≥ 0.85，加同义词）在输入的 diagnosis / encounter 来源里找匹配：一个都没找到 → `untraceable_diagnosis`；
     找到了但不在本条引用的来源里 → `source_mismatch`；找到了而且就在引用里 → 没有问题。所以"两种都没有"只剩一种可能：
     **"Pneumonia" 和测试引用的那条诊断匹配上了**，也就是这位病人可能真的有肺炎。断言 `A or B` 漏掉了第三种结果"没有错误"。
  2. **假设**（任务说明给的线索，即本章）：预埋病例挑哪个病人取决于医院的星期几，10-13 是周二。读 `app/ehr/seed/documentation.py` 的 `generate()`：
     `hallucination` 病例从 Medicine A 在院、没被其他预埋用过、同意 AI 处理的住院里，按 `(是否带出院处方, 是否英语, id)` 挑一个，**不看诊断**。
     再看 `app/ehr/reference.py`：肺炎（233604007）是 Medicine A 最常见的入院诊断之一（"shortness of breath" 权重 0.4，"fever" 0.3）。
  3. 本机 Docker Desktop 没在运行（5433 拒绝连接），于是先做不依赖数据库的实验（思路同 18.6）。第一版脚本想在脱离数据库的 Store 上重放整个测试，
     卡在签核对单：`S.sign` → `bus.publish` → `RuntimeError: A detached store has no database connection`（服务层要写事件表）。改成只重放**确定性的那一半**：
     脱离数据库按指定时刻生成数据（`uv run --with time-machine` 移动整个时钟），按 `read_inputs` 的方式取这次住院的资源，`facts.build` 建来源，
     按服务的 `_sources_view` 排序取"第一个 diagnosis"，再用 `DischargeSummaryValidator` 校验测试编辑后的内容（探针 `probe_validator.py`，每次约 40 秒）。
  4. 周二 10:00：预埋住院是 stay-01066，`reasonCode ['Pneumonia']`，第一个诊断来源就是 `Condition/cond-005657 'Pneumonia' 233604007`。
     测试加的 "Pneumonia" 引用的正是它，匹配且在引用里，0 个问题，`TEST FAIL`。**假设成立。**
  5. **扫描一周**（10-12 到 10-18，6 个进程并行）：只有周二失败。周一、周日 Sepsis（stay-00931），周三慢阻肺急性加重，周四脑卒中，周五、周六 Sepsis（stay-00942），
     这些天都是 `red untraceable_diagnosis discharge_diagnoses[1] 'Pneumonia'`。顺带发现断言里的 `source_mismatch` 分支 7 天里一次都没出现过。
  6. **一直如此，还是后来改坏的？** 95c8eef（10-11，5.3）改过用药安全预埋病例挑的病人，而这些病人会被排除出文书预埋的候选池，可能把周二的病例换了人。
     用 `git archive a04ca7b apps/api` 取出引入这个测试的 WP7 提交，用当前 worktree 的 venv 跑同一个探针：周二同样是 stay-01066 肺炎、同样 `TEST FAIL`。
     先确认跑的真是旧代码：`import app.ehr.seed.documentation` 的 `__file__` 指向解出来的目录（`python -c` 会把当前目录放在 `sys.path` 最前面、遮住 `PYTHONPATH`，
     所以从 scratchpad 目录运行）。结论：测试从写下那天起就在周二失败；它是 10-10（周六，生成周五的数据）写的，10-13 是它遇到的**第一个周二**。
  7. 启动 Docker Desktop（compose 容器自动重启）后，在自己的库 `ioc_test_brave` 上，同一次 pytest 会话同时跑 HEAD 的原测试（临时拷成
     `tests/test_zz_head_documentation_tmp.py`，跑完删除）和修改后的测试：原测试 `assert (False or False)`，与报告一致；新测试通过。
- **根因：** **测试的假设错了，校验器是对的。** 测试把 "Pneumonia" 当成这次住院一定没有的诊断，但预埋病例挑病人时不看诊断，而肺炎是 Medicine A 的常见入院诊断；
  周二挑中的正好是一位因肺炎入院的病人，"编造"的诊断其实有据可查，也正确引用了来源，校验器不报错才是对的。
- **怎么修：** 只改测试。"编造"的诊断在运行时挑一个不属于这次住院的名字：依次试 "Pneumonia"、"Pulmonary embolism"，跳过和本次住院任何诊断或入院原因
  （含 `lexicon.synonyms` 的同义词）`same_name` 匹配的那个。断言收紧成确定的结果：第一条（有据可查的）诊断没有红色问题；第二条在 `discharge_diagnoses[1]`
  上有 `untraceable_diagnosis`，值就是挑中的名字（原来"或 `source_mismatch`"从没出现过，去掉）。种子不改：数据本身没错，改种子会让演示数据换人。
- **怎么证明：**
  - 同一个库、同一次会话：原测试失败、新测试通过（`1 failed, 1 passed`）。
  - 文书的两个测试文件（`test_documentation.py`、`test_documentation_validator.py`）在周二（真实时钟）和周五各 `30 passed`。周五用 scratchpad 里的 pytest 插件
    `-p travel`：模块导入时就用 time-machine 把时钟移到 `TRAVEL_TO`，早于 conftest 播种。`-q` 不显示插件的报告头，所以用 SQL 确认两个库确实是不同的医院日：
    `ioc_test_brave` 的预埋住院是 stay-01066 / 233604007，`ioc_test_brave_fri` 是 stay-00942 / 91302008。新测试的两条路（周二退到 "Pulmonary embolism"，
    周五仍用 "Pneumonia"）都跑到了。
  - **变异测试：** 插件 `-p mutate_validator` 把 `TraceabilityValidator.check_item` 换成丢掉 `untraceable_diagnosis` / `source_mismatch` 的版本，新测试失败
    （`AssertionError: []`），说明它不是空转。插件在 `pytest_collection_modifyitems` 里才 import app，保证 `tests/conftest.py` 先设好 `DATABASE_URL`，不会连到开发库。
  - 两个测试库用完已删除。没有跑全量后端（只改了一个测试）。
- **经验：**
  - 断言"两种错误至少有一种"（`A or B`）时，先问：有没有第三种结果"没有错误"？这里第三种结果恰恰是正确行为，失败报的是测试的假设，不是被测代码。
  - 测试里的反例（编造的诊断、不存在的药）要在运行时证明它确实不在数据里，不能凭印象挑一个"应该没有"的名字；挑得越常见，越容易撞上。
    这是 5.3、12.9 那个教训的另一面：那里是预埋病例的前提没写成挑选条件，这里是测试的前提没写成条件。
  - 读一遍判定逻辑就能把可能性从"三种"缩到"一种"，之后的实验只需确认这一种。
  - 数据库不可用时，把被测逻辑里确定性的那部分单独拿出来实验（生成器、来源、校验器都不需要数据库），一个星期几几十秒，比等环境快。
  - "从什么时候开始坏的"用旧提交跑同一个探针回答，先确认真的加载了旧代码。
- **范围：** hospital（WP7 文书测试，WP1 生成器的预埋病例）。
- **来源：** wt-brave-grothendieck-30fcc4 10-13 约 16:00–16:45；发现于同日 Gemini 重测的全量后端（807 passed、1 failed）。
- **代码：** `apps/api/tests/test_documentation.py`（`test_template_only_without_ai_consent`）；相关：`apps/api/app/modules/documentation/validator.py`（`check_item`）、
  `apps/api/app/ehr/seed/documentation.py`（`generate()` 的 `hallucination` 病例）；提交 a7a7d55。

> **怎么在另一天测试**（本机做法，见 memory 记录）：
> - 脱离数据库调用 `generate(Store(), seed, datetime(...))`，几十秒就能扫完一周（5.6）；或者写一个 pytest 插件只冻结播种时刻（`seedat`、`seedtime`：`-p seedtime` 加 `SEED_AT`），
>   每个星期几一个测试库并行跑，并先用 SQL 确认真的换了医院日（5.9）。
> - **只改种子日期不够**：影像相关测试会和真实时钟对不上。hopeful-ellis 只替换 `app.seed.datetime` 跑周四的数据，10 个失败：1 个是 5 秒计时测试，另外 9 个全是影像测试
>   （`test_phase2.py::test_contrast_status_and_threshold_recompute`、`test_phase3.py` 的 3 个、`test_phase4.py` 的 5 个，如 `test_reconciliation_finds_every_planted_discrepancy`、
>   `test_expired_lots_are_never_consumed` 的 `StopIteration`）；用 `uv run --with time-machine` 把整个时钟移到周四后，这 9 个全部通过（`9 passed in 108.29s`）。
> - time-machine 把**不带时区的** `datetime` 当作 UTC（本机是 UTC-4），要传带时区的时间。
> - 整个 pytest 会话换到另一天：scratchpad 里写一个插件，模块导入时就 `time_machine.travel(<带时区的时间>, tick=True).start()`，
>   用 `PYTHONPATH=<插件目录> uv run --with time-machine pytest -p travel` 运行；`-q` 不显示插件的报告头，用 SQL 确认库里是哪一天的数据（5.12）。
> - 浏览器端在 Vite 配置里注入一个 `Date` 子类来偏移时间；放在 scratchpad 里的 Vite 配置要用 `.mts` 扩展名（第六章）。改浏览器时钟前先确认前端不拿它比对令牌过期时间（5.2）。

---

## 六、开发环境（Windows 开发机）

环境问题的特点是报错和你的代码毫无关系，容易浪费大量时间。遇到"莫名其妙"的错误，先问：是不是环境变了？
前五个案例有完整的排查过程，后面两张速查表收的是一句话就能说清的坑。

### 6.1 播种"卡住"：Windows 上用 `localhost` 连数据库，每次新建连接要 8 秒

- **现象：** PostgreSQL 迁移时第一次 `uv run python -m app.seed`（迁移加生成演示数据）超过 300 秒没完成，被移到后台。
- **怎么查的：**
  1. 先看数据库在干什么：`pg_stat_activity` 里只有一个连接，状态 `idle in transaction`、等待 `Client:ClientRead`，停在 `INSERT INTO audit_events ...`，
     持续 14.9 秒，20 秒后变成 48 秒。说明是数据库在等客户端，不是数据库慢。
  2. 再看客户端在干什么：找到两个 seed 进程（venv 的启动器 CPU 0.05 秒，真正的解释器 CPU 21.3 秒），用 `uvx py-spy dump --pid` 打出主线程栈：
     停在 psycopg 的 `wait_conn` ← `connect` ← 连接池 `_create_connection` ← `engine.begin()` ← `_ensure_sequence (app/core/store.py:251)` ← `persist` ←
     `reset_store` ← `seed.py:528`。也就是第一个连接开着事务时，代码在**新建另一个连接**，卡在建连接上。
  3. 写小脚本对比建连接耗时（`connect_timeout=8`，各 3 次）：`127.0.0.1` 0.05 / 0.03 / 0.03 秒；`localhost` 8.39 / 8.02 / 8.04 秒。
- **根因：** 在这台 Windows 上 `localhost` 每次建连接都要多花约 8 秒（正好等于设的 `connect_timeout`）。会话的结论是 `localhost` 先尝试 IPv6 并挂起，
  然后才退回 IPv4（**推测**：转录里没有单独测 `::1`）。相关事实：compose 把 Postgres 端口只发布在 `127.0.0.1:5433`（IPv4 回环）。播种过程中会多次新建连接，
  每次 8 秒，看起来就像"卡住了"。
- **怎么修：** `app/core/config.py` 的默认 `DATABASE_URL` 从 `localhost:5433` 改成 `127.0.0.1:5433`。
- **怎么证明：** 重新播种 `real 0m30.330s` 完成；直接查表确认 19,473 个预约、2,003 个病人，PHI 列是 `k1:` 密文。
- **经验：** 程序"卡住"时，两头都看：数据库端看会话状态（`idle in transaction` = 数据库在等客户端），客户端用 py-spy 打栈看卡在哪一行。
  定位到"建连接慢"以后，用只改一个变量（主机名）的小脚本对比，几秒钟就能确认。同一类 IPv4/IPv6 的坑在 Vite 上也出现过（6.6 表）。
- **范围：** 环境（阶段 0 数据层迁移时）。
- **来源：** main__d95dd1c0 10-07 22:18–22:25。
- **代码：** `apps/api/app/core/config.py`；提交 9458db0。

### 6.2 `No module named 'websockets.client'` 反复出现：venv 里一个删不掉的旧 dist-info

- **现象：** `No module named 'websockets.client'`；或者 Playwright 的 webServer 用 `uv run` 启动 API 时报
  `failed to remove file ...\websockets/speedups.cp312-win_amd64.pyd: Access is denied. (os error 5)`，webServer 退出码 2。从 10-07 到 10-09 反复出现。
- **怎么查的：**
  1. 直接原因：pytest 或 API 正在用虚拟环境时，`uv run` 又去同步依赖，Windows 上 `.pyd` 文件被占用，同步失败，包被删了一半。
  2. 但为什么**每次** `uv run` 都要同步 websockets？每次 `uv sync` 的输出都是 `- websockets==17.2 / ~ websockets==16.1.1`。venv 里残留一个**没有 RECORD 文件的**
     `websockets-17.2.dist-info`，而锁文件要的是 16.1.1。uv 删不掉它（`warning: Failed to uninstall package at .venv\Lib\site-packages\websockets-17.2.dist-info due to missing RECORD file`），
     所以 venv 永远"不同步"，每次 `uv run` 都要重装 websockets；只要这时有进程占着 `.pyd`，就会 `Access is denied` 并留下半删的包。site-packages 里同时有
     `websockets-16.1.1.dist-info` 和 `websockets-17.2.dist-info`，`import websockets` 后连 `__version__` 都没有。17.2 从哪来，转录里看不到（10-09 `uv add temporalio`
     时的输出里也是 17.2 换成 16.1.1，但 10-07 的会话就已经是这样了）。
  3. 第一次（10-07 14:14）占用者是开发者早上起的 9001 端口后端。会话没去停它，而是用 `UV_PROJECT_ENVIRONMENT=<scratchpad>/venv uv run --frozen pytest`
     在一个临时 venv 里跑测试（146 过、1 失败，即 5.7）。10-09 那次占着 venv 的是一个没被 `kill` 掉的独立 worker（进程 14520，见 6.6 表）。
- **根因：** 一个缺 RECORD 的旧 dist-info 让 venv 永远处于"不同步"状态，每次 `uv run` 都去重装 websockets，遇上 Windows 的文件占用就把包删坏。
- **怎么修：** 停掉占用者，删掉过期的 `websockets-17.2.dist-info`，`uv sync --reinstall-package websockets`；有进程占用 venv 时用 `uv run --no-sync`
  （Playwright 运行设 `UV_NO_SYNC=1`），运行脚本里设置不在运行中同步依赖。
- **怎么证明：** 资料里只有修复步骤，没有记录修复后 `uv sync` 的输出。
- **经验：** 一个反复出现的环境问题，先找"为什么每次都会触发"（这里是 venv 永远不同步），不要每次都只绕过。不能停别人的进程时，可以用
  `UV_PROJECT_ENVIRONMENT` 指向一个临时 venv 来跑测试。
- **范围：** 环境。
- **来源：** main__b1ffba74 10-07 14:14；main__d95dd1c0 10-07 21:40、22:03；main__394ebe0a 10-07 23:22；main__4f2eefc4 10-08 20:05；main__bec5acdb 10-08 22:42；
  main__025b79fc 10-09 21:47、23:47–23:48。

### 6.3 Docker 数据盘写满（第一次，10-09）：从测试超时一路追到磁盘

- **现象：** ICU 校准会话并行跑了 4 个星期的测试库、再加 3 个旧生成器对照库之后，Playwright 9 个失败、13 个没跑，演示重置的 `beforeAll` 超过 60 秒；
  API 日志里重置返回 500：`psycopg.OperationalError: consuming input failed: server closed the connection unexpectedly`。
- **怎么查的：** `docker ps` 卡住（带 `timeout 60` 也没返回）；直接用 psycopg 连 5433 报 `connection timeout expired`。查盘：C: 只剩 3.5 GB（共 500 GB；WP5 会话复查时 4.6 GB），
  `docker_data.vhdx` 60.5 GB 在 C: 上；`wsl -d docker-desktop -- dmesg` 里反复出现 `I/O error, dev sdd, sector 87228360`。会话自己的判断：
  "My parallel test databases probably helped fill it"。
- **根因：** Docker Desktop 的数据盘 `docker_data.vhdx` 在 C: 盘上，并行播种多个测试库把 C: 写满。
- **怎么修：** 会话没有动 Docker，交给负责人。WP5 会话提醒：vhdx 在 Docker 里删数据库后并不会缩小，空间要从 C: 别处腾。负责人清出空间并重启
  （ICU 会话记录清出 25 GB；协调会话记录重启后 C: 有 42 GB）。
- **怎么证明：** 重启后容器先是 unhealthy（`FATAL: the database system is starting up`，崩溃恢复重放 WAL），随后可连，dmesg 不再有 I/O 错误。被中断写入的 `ioc_icu` 重新播种，
  评测和模拟器报告除了异常 id（被回滚事务推进的序列）外与之前完全一致，顺带证明了生成器是确定的。
- **后续：** 也正是这次重启之后，Windows 开始保留 TCP 8430–9329（6.6 表的端口一行）。之后的会话结束时会主动删掉自己的测试库（去标识化会话删了 6 个，理由是
  "Docker's disk on C: has been tight"；电话出生日期会话删了 2 个，当时 C: 93%），但删库不会让 C: 的空间回来：一个会话开始时 C: 剩 13.3 GB、结束时 8.9 GB。
  2026-10-12 C: 只剩 1.3 GB 时，`docker builder prune -f` 在 Docker 内释放了约 16 GB。
- **经验：** 现象链很长（测试超时 → 500 → 连接被断开 → `docker ps` 卡住），要一路追到最底层。数据库"莫名其妙"断开时，先看磁盘。并行开很多测试库之前先看 C: 剩余空间。
  生成器是确定的有个好处：重放后结果完全一样，可以用来确认恢复没丢东西。
- **范围：** 环境。
- **来源：** wt-gracious-cray-14c437 10-09 14:39–14:48、19:45–19:54；main__dec77d7e 10-09；main__7a422c19 10-09 15:04、21:13；wt-nifty-bose-77983d 10-12 02:06。

### 6.4 机器 CPU 被占满：一个孤儿 `find` 跑了两个小时

- **现象：** WP9 的 e2e run 13 第 5 步用了 540 秒（之前约 125 秒），连诊断命令都要 2 分钟以上。
- **怎么查的：** `pg_stat_activity`：ioc_wp9 12 个连接、0 个 active，Postgres 空闲；`docker stats`：各容器 CPU 都很低；`Get-Process | Sort-Object CPU`：除了几个 claude 进程，
  还有一个 `find` 累计 CPU 7718 秒（约两小时）。用 `Get-CimInstance Win32_Process` 看它：命令行 `D:\git\usr\bin\find.exe / -maxdepth 6 -type d -name side-session-notes`，
  2026/10/10 19:14:03 启动（在本次走查开始之前），父进程已不存在。
- **根因：** 某个会话在 Git Bash 里从根目录 `/` 找 side-session-notes 目录（深度 6，会遍历所有挂载的盘），父进程退出后它成了孤儿，一直占着 CPU。
- **怎么修：** 核对命令行确实是它之后 `Stop-Process -Id 14896`。
- **怎么证明：** 进程消失。不过之后 CPU 仍是 100%（主要是其他 Claude 进程），run 14 最终用 30.6 分钟通过。
- **经验：** 变慢时先看是谁在用 CPU（数据库、容器、进程逐层排除），再按命令行、创建时间和父进程判断它是不是自己的、还有没有人在等它。不要在 Git Bash 里 `find /`。
- **范围：** 环境。
- **来源：** wt-wp9-storyline-7d11cd 10-11 02:53–03:00。

### 6.5 路径太长：`DLL load failed … The filename or extension is too long`

- **现象：** WP10 计时的新人检查：把分支克隆到会话 scratchpad（`C:\Users\lenovo\AppData\Local\Temp\claude\...\scratchpad\newcomer`，约 150 个字符），`uv sync` 成功，
  `uv run python -m app.seed` 立刻失败：`ImportError: DLL load failed while importing _collections_cy: The filename or extension is too long.`
- **怎么查的：** 判断是 Windows 260 字符的路径上限（落在 `.venv\Lib\site-packages` 深处）。先试一个短 junction `C:\Users\lenovo\ioc-nc` 指向克隆目录，删掉 .venv 重新 sync，
  **仍然失败**，报错里还是长路径：uv 把 junction 解析回了真实路径。改为直接克隆到 `D:\ioc-nc`，sync、生成 PHI 密钥、seed（2 分 57 秒）都正常。
- **根因：** 项目路径加上虚拟环境里的深层路径超过 260 字符。
- **怎么修：** README 的 Windows 说明加上"克隆到短路径（如 `C:\src\ioc`）"，排错表加上这条报错。
- **怎么证明：** 短路径下整套演示（含 Temporal worker 和故事线重置）25 分钟跑起来，其中约 5 分钟花在这次绕路上。
- **经验：** 用 junction 或符号链接"缩短"路径不一定有效，工具可能解析回真实路径。从零克隆、只按 README 操作的新人检查，能发现开发者自己环境里永远不会出现的问题：
  同一次检查还发现 API 冷启动后第一个页面约 20 秒（缓存预热，写进 README 为 "10–30 seconds"），以及 2.1、2.2 的 worker 竞态。
- **范围：** 环境。
- **来源：** wt-wp10-roadmap-docs-6e66e7 10-11 08:26–08:39；PROGRESS WP10 "Newcomer check"。
- **代码：** `README.md`（Windows 说明和排错表）；提交 f531532。

### 6.6 速查表（一）：Windows、shell 和进程

| 现象 | 根因 | 解法 |
| --- | --- | --- |
| 放在 `%TEMP%` 的 Python 脚本一 import 就崩，栈是 sqlalchemy → `typing_extensions` → `inspect` → `import dis` → `C:\Users\lenovo\AppData\Local\Temp\dis.py` 第 3 行去打开一个 APK 解包路径（`FileNotFoundError`） | `%TEMP%` 里有一个别的任务留下的 `dis.py`，遮蔽了标准库（Python 会先从脚本所在目录找模块）。栈里出现既不属于项目、也不属于标准库的路径，就是"模块被遮蔽"的信号 | 临时脚本放在会话的 scratchpad 目录；运行不可信目录里的脚本时用 `python -I` |
| `No module named 'websockets.client'`；`.pyd` `Access is denied` | 见 6.2 | 有进程占用 venv 时用 `uv run --no-sync`；`uv sync --reinstall-package websockets` |
| 端口 9001、9017 等启动报 `Errno 13` | 重启后 Hyper-V 保留了 TCP 8430–9329。更早 8000 就被系统保留（`apps/web/vite.local.config.ts` 注释 "Windows reserves port 8000 on this machine"），所以 API 改用 9001；10-09 重启前 9001、9015、9017 都正常，重启后都落进保留范围 | `netsh interface ipv4 show excludedportrange protocol=tcp` 查保留范围，换端口（例如 9417、9461） |
| 输出重定向到文件时 `UnicodeEncodeError: 'gbk' codec can't encode character '₂'`（"SpO₂"），或打印法语人名时 `... character '\xc9'` | 中文区域的 Windows 上，Python 重定向输出默认用 GBK 编码 | 设置 `PYTHONUTF8=1` 或 `PYTHONIOENCODING=utf-8`（本仓库 `scripts/*.py` 已内置修复，见 6.8） |
| Windows PowerShell 5.1 里读 UTF-8 的 spec 文件，中文显示成乱码（"鎶ゅ＋浼氬湪…"） | `Get-Content` 不带 `-Encoding UTF8` 时按系统代码页读 | 加 `-Encoding UTF8` |
| 两个会话的 pytest 互相把对方搞挂 | `conftest.py` 开始时 `DROP DATABASE ioc_test WITH (FORCE)`，并行会话共用同一个测试库。起因可追到 10-08 WP3 和反馈修复会话共用一个 checkout；WP9 自己也忘设过一次，把共享的 `ioc_test` 删了重建 | 每个会话用自己的 `TEST_DATABASE_URL=.../ioc_test_<名字>`；出事后用 `pg_stat_activity` 确认谁还连着 |
| 停掉后台任务后还有进程在跑：第二次跑 Playwright 随机失败；停掉 bash 扫描后还剩 21 个 pytest 进程；停掉 `npx vite` 后端口 5211 仍被占 | 父进程停了，子进程还活着（孤儿 node 进程、pytest 子进程、真正监听端口的 `node ... vite.js`），两次运行共用了 API 和 Vite | 重跑前用 `Get-CimInstance Win32_Process` 按命令行特征（`*playwright*`、`-p seedat`、端口号）查残留，或按端口查 `OwningProcess` 再 `Stop-Process`。反过来，"有别人的进程"不等于"有冲突"，先查端口和数据库是否真的共用（2.8） |
| 脚本 `kill` 掉的 worker 进程还在，继续占着 venv | Windows 上 venv 的 `Scripts\python.exe` 是启动器，真正运行的是它的子进程（看到过子进程 7700 的父进程是启动器 13264）；`kill` 为什么没停掉 worker 14520 没有查明 | 按命令行找到启动器和子进程，两个都 `Stop-Process`；脚本里改用 PowerShell 按命令行匹配停止 |
| `git commit -q -F - @'...'@` 报 `... did not match any file(s) known to git` | Windows PowerShell 把 here-string 当成参数（pathspec）传给 git，而不是送进 stdin | 提交说明写到 scratchpad 文件，用 `git commit -F <文件>` |
| PowerShell 字符串 `"--- round $round: ..."` 报 `Variable reference is not valid. ':' was not followed by a valid variable name character` | 变量名后紧跟 `:` 会被当成驱动器限定的变量引用 | 写成 `${round}:` |
| `npx vite` 在错误的目录启动 | PowerShell 工具的工作目录在调用之间保留，前面执行过 `cd ..\api` | 启动前确认目录，或在命令里显式进入 `apps\web` |
| 基线扫描想从 10-08 00:00 起每小时播种，输出里却是 `Tue 2026-10-06 23:00` 等 | `date -d "2026-10-08 00:00 + $h hours"` 被 Git Bash 的 `date` 误解析（会话的结论是 "misparsed the offset"） | 不做日期运算，直接枚举字符串（`"2026-10-${d}T$h:00"`）；扫描脚本先打印它实际用的参数再读结果 |
| 判断机器负载时两个读数矛盾：`Win32_Processor.LoadPercentage` 100、87、91、77、100，而 `Get-Process` 算出 10 秒内单个进程的 CPU 增量最多 1.5 秒；忙时 `Get-CimInstance` 查询要四五十秒才返回 | 未查明哪个对（例如 Docker 虚拟机里的 PostgreSQL 不一定出现在 `Get-Process` 的前几名里） | 看一段时间内各进程的 CPU 增量，注意 Docker 里的进程；读数矛盾时先别下结论（16.2） |
| Vite 开发服务器只监听 `[::1]:5209`，`curl http://127.0.0.1:5209/` 得到 000；同时页面里 `fetch` 报 `Failed to fetch` | Vite 只绑定了 IPv6 的 localhost；`Failed to fetch` 其实是 API 还在迁移和播种（约 2 分钟后才健康） | 用 `http://[::1]:5209/`；等 API 健康再看页面 |
| 监控命令按 UTF-16 解码 Playwright 日志，什么也匹配不到 | 日志是 `Out-File -Encoding utf8` 写的，`file` 显示为 "UTF-8 (with BOM)" | 先用 `file <日志>` 确认编码再写过滤 |
| 管道里 `grep` 写文件，加载日志一直是空的 | `grep` 写文件时缓冲输出 | 换一个能直接看进度的地方（HAPI 计数，9.1），或关掉缓冲 |
| scratchpad 里的脚本 `import app...` 报 `ModuleNotFoundError: No module named 'app'` | 脚本不在 `apps/api` 下运行 | 在 `apps/api` 下运行并设 `PYTHONPATH=.`（或 `$PWD`） |
| 写 `docs/PROGRESS.md` 时 `OSError: [Errno 22] Invalid argument`，2 秒后重试成功 | 未查明；推测是 Windows 上短暂的文件占用 | 稍等重试；写完检查文件完整 |
| auto mode 拒绝 `git reset --hard`（"Irreversible Local Destruction"）、"DROP 再 CREATE 数据库"的组合命令（"Cloud Storage Mass Delete"）、复制 `apps/api/.env`（`[Credential Materialization]`）、`Stop-Process` 残留的 Temporal 测试服务器（`Interfere With Workloads`） | 自动模式的保护规则 | `git merge --ff-only` 或从目标分支新建分支；数据库换个新名字，事后留给人工删除；mock 模式用 `LLM_PROVIDER=mock` 加 `tests/conftest.py` 里公开的测试密钥；把 PID 报给负责人（9.4） |

### 6.7 速查表（二）：Docker、数据库、测试工具

| 现象 | 根因 | 解法 |
| --- | --- | --- |
| PostgreSQL 断开连接、`docker ps` 卡住，Docker 日志里有 `I/O error, dev sdd` | Docker Desktop 的数据盘 `docker_data.vhdx` 在 C: 盘上，并行播种多个测试库把 C: 写满（6.3）；删库不会让 vhdx 缩小 | 跑大任务前后都看 C: 剩余空间；用完删自己的测试库；`docker builder prune -f` |
| 开机后 `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine` | Docker Desktop 没起来 | `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"`，循环 `docker info` 直到成功（约 45 秒），再 `docker compose up -d postgres` 并等 `pg_isready` |
| `docker pull temporalio/temporal:1.9.1` 报 `unexpected status from HEAD request to https://docker.m.daocloud.io/... 500 Internal Server Error` | 本机的镜像源拉不到这个 tag | 固定用能拉下来的 1.8.3 |
| Playwright 报 `Timed out waiting 60000ms from config.webServer` | 机器负载高时 API 要 1–3 分钟才能启动（WP9 量到 65 秒和约 2.5 分钟）；全新数据库第一次启动要迁移加播种，实测 83 秒 | 先自己把 API 启动起来（数据库先播种好），Playwright 的 `reuseExistingServer` 会直接用；设置 `UV_NO_SYNC=1`；加 `PYTHONUNBUFFERED=1`，否则访问日志被缓冲，会误以为日志不全（11.2） |
| 评测跑完，运行记录被标为 "git dirty" | 评测在**结束时**才读 git 状态，运行期间改了代码就会被标脏 | 评测运行期间不要改被跟踪的文件，先把改动存成 patch，跑完再应用；反方向的问题（根本不标脏）见 14.8 |
| 在测试库上跑评测报 `cryptography.exceptions.InvalidTag` | PHI 字段加密存储；测试库由 conftest 用固定的测试密钥写入（`test-only-phi-encryption-key-32b`、`test-only-phi-blind-index-key-32`），评测进程读的是 `.env` 里的开发密钥。AEAD 解密报 `InvalidTag` 几乎总是密钥或关联数据不对，而不是数据坏了 | 数据库和密钥成对换：在测试库上跑时显式传入 conftest 里的测试密钥，或改在自己的开发库上先做演示重置再跑 |
| `CREATE TEMP TABLE ... (LIKE fhir_resources INCLUDING DEFAULTS)` 之后 COPY 报 `null value in column "seq" of relation "fr_plain" violates not-null constraint` | `INCLUDING DEFAULTS` 不复制 identity 列的生成规则 | 加上 `INCLUDING IDENTITY` |
| `pytest --keep-duplicates "$T" "$T" "$T"`（同一个节点 ID 写三次）结果只有 `1 passed` | 同一个节点 ID 重复写并不会跑三次（会话当时没有注意到，把它当成"这次通过了"） | 想重复运行，就用 shell 循环，每次输出写到单独的文件 |
| 用 time-machine 移动时钟后时间差了 4 小时 | time-machine 把不带时区的 `datetime` 当作 UTC（本机是 UTC-4） | 传带时区的时间 |
| 放在 scratchpad 里的 Vite 配置报 `"@tailwindcss/vite" resolved to an ESM file. ESM file cannot be loaded by require` | 项目外的 `.ts` 配置按 CommonJS 加载 | 改成 `.mts` 扩展名 |

- **经验：** 把每个环境坑记下来（这个项目记在 Claude 的 memory 里，下次会话自动读到）。同样的坑第二次遇到时，查记录只要一分钟。

### 6.8 脚本把输出重定向到文件时崩在 GBK 编码（SpO₂）

- **现象：** 2026-10-12 真实 key 跑 `scripts/storyline_report.py`，输出重定向到文件，第 2 步崩在 `UnicodeEncodeError: 'gbk' codec can't encode character '₂' in position 3: illegal multibyte sequence`（`₂` 是 “SpO₂” 的下标 2）。
- **怎么查的：**
  1. `python -c "import locale; print(locale.getpreferredencoding())"` → `cp936`（中文区域 Windows 的 ANSI 代码页，即 GBK）。
  2. 最小复现：`env -u PYTHONUTF8 -u PYTHONIOENCODING python -c "print('SpO₂')" > file` → 退出码 1，正是上面的报错；直接打到终端（TTY）不报错。确认是“stdout 被重定向到文件/管道时才按代码页编码”。
  3. 验证修复方向：脚本启动时把 stdout/stderr 重新配置成 UTF-8，同一条 `print` 就能写出 UTF-8 字节。
- **根因：** 中文区域 Windows 上，Python 的 stdout 一旦不是终端（重定向到文件或管道），就按本地 ANSI 代码页（GBK）编码，GBK 编不出 `₂`（以及法语人名里的 `É` 等）。原来的办法是手动设 `PYTHONUTF8=1`，容易忘。
- **怎么修：** 新增 `apps/api/scripts/_stdio.py` 的 `utf8_stdio()`，用 `reconfigure(encoding="utf-8", errors="replace")` 把 `sys.stdout`、`sys.stderr` 切成 UTF-8（取不到流就跳过）。31 个会 `print` 的脚本都在各自 `if __name__ == "__main__":` 里**第一件事**调用它——不在 import 时调用，因为评测 runner 会把 `control_tower_eval.py` 当模块在 API 进程里加载，改全局 stdout 会误伤宿主进程。于是不再依赖 `PYTHONUTF8=1`。
- **怎么证明：** 新测试 `tests/test_script_stdio.py`：一个 `PYTHONIOENCODING=gbk` 的子进程，不调用 helper 时打印 “SpO₂” 抛 `UnicodeEncodeError`，调用后写出的是 UTF-8；并检查每个会 print 的脚本都在 `__main__` 里调了 helper。开发机本地区域（cp936，不设 `PYTHONUTF8`）实测复现并通过。
- **经验：** 不要依赖“运行时记得设某个环境变量”来保证脚本能把输出写进文件；让脚本在入口处自己把 stdout 配好。配置位置选 `__main__` 入口，不要放在 import 时——有的脚本会被别的进程当模块导入，import 时改全局状态会误伤宿主。这条是本章“输出重定向 GBK”速查表行的落地修复。
- **范围：** 环境（开发机）/ 平台（脚本）。
- **代码与提交：** `apps/api/scripts/_stdio.py`（新）、31 个 `scripts/*.py`（各加 3 行）、`apps/api/tests/test_script_stdio.py`（新）。worktree 分支 `claude/dazzling-northcutt-41fb23`，提交 `6be8255`。

---

## 七、性能：先测量再优化

这一章的共同做法：先拆时间、找大头，每次只改一处，改完马上再量（瓶颈会转移）；在机器安静时量，或者交替跑几轮；
负载大到分不清假设时，换一种切法而不是继续堆实验。

### 7.1 重置写入：INSERT 换成 COPY，以及 JSONB `None` 的坑

- **现象：** 2.3 缩小锁范围后，单是写入仍要 14–21 秒，负载重时达 44 秒，等锁的活动仍可能超过 30 秒超时（6 次计时里有 3 次）。
- **怎么查的（测量）：**
  1. **测量工具先出了问题。** 第一版 measure.py 用 SQLAlchemy 的 `before_execute` 监听器统计每张表的 INSERT 时间，结果是 `"writes total": 0`：SQLAlchemy 2.0 里
     executemany 的参数传给监听器的形式不同。修好后重跑。磁盘上的代码已经是 COPY 版本，所以 "before" 模式是通过替换 `_copy_rows`、换回原来每批 2,000 行的 INSERT 来测的。
  2. 拆开看锁里的时间：TRUNCATE 64 张表 1.3 秒；写入 20.9 秒，其中 `fhir_resources`（64,653 行、94 MB，主要是加密后的资源）占 14.0 秒，`appointments` 占 3.0 秒。
     批量 INSERT 在服务器端仍是一行一条 INSERT。INSERT 的基线随负载变化很大：锁持有 28–54 秒，`fhir_resources` 14–33 秒，`appointments` 3–5 秒，TRUNCATE 7–9 秒。
- **怎么修：** 改用 PostgreSQL 的 COPY（psycopg 的 `cursor.copy`），在同一个连接和事务里写。**关键坑：** 每个值必须先经过该列的 SQLAlchemy bind processor，
  否则 JSONB 的 `None` 会变成 SQL NULL，而原来 INSERT 存的是 JSON `null`（`module_state.data` 是 NOT NULL）。另一种错误"Python 列表被当成 PostgreSQL 数组发送"
  是会话的推理，转录里没有对应的失败运行（**推测**）。
- **怎么证明：**
  - **公平比较：** 会话注意到 INSERT 和 COPY 分开跑时 build_store 的耗时差别很大（机器负载不同），于是改成两者交替跑三轮。轻负载下：写入 14.5–17.4 秒 → 6.6–6.7 秒，
    锁持有 15.8–19.2 秒 → 8.0–8.2 秒；重负载下：INSERT 写入 31.7 秒、锁 39.3 秒，COPY 写入 17.3 秒、锁 23.5 秒。写入开始时到达的活动：INSERT 等了 15.7–53.8 秒
    （6 次里 3 次超过 30 秒），COPY 7.9–23.3 秒（从未超过 30 秒）。TRUNCATE 的代码没变，耗时从轻负载的 1.0–1.4 秒到重负载的 8.8 秒，可见负载的影响。
  - **正确性：** 测试把生成的数据用两种方式写入，逐表逐行比较 `md5(t::text)`（这种文本形式能区分 SQL NULL 和 JSON `null`）；测试数据里专门放了 COPY 文本格式要转义的值
    （制表符、换行、反斜杠、字面量 `\N`、引号、非 ASCII 字符）和一张含全部 256 个字节值的图片。
  - **证明测试真能抓到 bug：** 把一个故意写错的 COPY 实现（`neg/copy_without_bind.py`，把 JSONB 的 `None` 写成 SQL NULL）放到 `PYTHONPATH` 上跑，测试失败：
    `psycopg.errors.NotNullViolation: null value in column "data" of relation "module_state" violates not-null constraint`。全套 779/779（14 min 53 s）。
- **经验：** 优化前先拆解时间，找到大头（这里一张表占了三分之二）；换实现时用**逐行比对**证明结果完全一致，而不是只看"没报错"。测量工具本身也要先验证
  （"writes total": 0 说明是工具坏了，不是没写）。负载不同的两次运行不能直接比，要交替跑。剩下的时间去哪了，见 7.7。
- **范围：** 平台（演示重置）。
- **来源：** wt-copy-reset-writes-0a4f34；PROGRESS "WP10 follow-up · the demo reset writes its rows with COPY"；engineering-notes.md note 8。
- **代码：** `apps/api/app/core/store.py`（`persist`、`_copy_rows`）；`apps/api/tests/test_persist.py`；提交 85e6fac。

### 7.2 计时测试在机器忙时失败

- **现象：** 一个"5 秒内出候补候选"的测试，其他测试套件占着 CPU 时 7.1 秒（失败），单独跑 3.4 秒（通过）。
- **同类的记录很多：**
  - 阶段 0 迁移途中（全套要 5 分 16 秒时）这个测试就失败过；7.3 的性能工作完成后它自己通过了（PROGRESS："The backfill timing test passed once the suite was no longer slow"）。
  - 10-09 一边跑 72 小时扫描和压力测试一边跑全量：`assert (10149.828 - 10101.781) < 5`，也就是 48 秒；和另外 6 个失败一起重跑时通过。另一个会话因此特意等扫描结束再跑全量，
    "so the 5-second backfill check isn't starved of CPU"。其他读数：15.6、10.4、9.3 秒（单独 2.4 秒）；WP8 的基线全量 1 failed、301 passed（566.86 秒）。
  - Control Tower 的 2 秒测试 `test_board_follows_the_simulator_within_two_seconds` 用了 4.6 秒（`assert (87324.14 - 87319.515) < 2.0`），当时后端全套 25 分钟、CPU 饱和；单独跑通过。
  - WP8 新写的 Playwright 用例：负载下电话 agent 对医疗问题的回复 5 秒内没出来（`expect(transcript).toContainText("not able to answer medical questions")`），
    改为让 `say()` 等到 agent 回复（最多 20 秒）再断言。
  - `business-compliance.spec.ts` 的 "inspection hub"：后台 intake 循环没在测试的 15 秒内发出新的 7 天提醒。先确认这个循环和自己的改动无关，再查到 PROGRESS WP9 已经把它列为负载失败，单独重跑 1/1 通过。
  - 负载的幅度（WP9 记录）：同一个故事线 Playwright，CPU 100% 时 30.6 分钟、较安静时 15.0 分钟；故事线重置 44、112、165、238 秒不等；全量后端 3708.88 秒（平时 15–20 分钟）。
    e2e run 12 失败是因为后端全量同时在跑：重置 238 秒，第 1 步的登记 3 分钟内没落库，API 日志最后一行停在登记的 `ADT^A01 ... outcome=received`，
    事件投递被重置的积压拖后了几分钟。处理方法是等后端套件跑完再跑 e2e，而不是继续加大超时。
- **两个提醒：**
  - "单独重跑一次"不总能排除负载：有一次单独跑也失败了，因为负载来自另一个会话（16.2）。
  - "失败都是负载"也不总对：5 秒候补测试的几次失败可能和 5.1 是同一个根因（推测，见 5.1）。
- **经验：** 计时类测试失败时，**先单独重跑一次**再下结论，并当场记下机器上还在跑什么。为了演示或调试临时放宽超时，只改本地的临时配置：给负责人用 UI 模式演示故事线时，
  电话那一步又因为 5 秒等待失败（当场测"身份核验"那一轮只要 0.77–1.28 秒，CPU 100%），会话只在临时的观看配置里放宽到 30 秒，提交的测试保持 5 秒不变。
- **范围：** 平台（影像和医院的计时测试）。
- **来源：** main__394ebe0a 10-07 23:22；main__56196a27 10-09 06:51；main__04f34599 10-09；wt-wp8-voice-services-addfd5 10-10 06:29、08:15；wt-cool-cartwright-7673d9 10-10 15:41–15:44；
  wt-wp9-storyline-7d11cd 10-11 02:14–03:32；wt-priceless-shamir-24594d 10-11 18:30–18:34；wt-nifty-bose-77983d 10-12 01:22–02:29；PROGRESS WP6、WP8、WP10b、WP10 Results。

### 7.3 迁移到 PostgreSQL 后变慢：测量驱动的五步优化（批量解码、快照、行缓存、拷贝、GC）

- **现象：** 模块代码保持字典式访问（`store.appointments.values()` 等），底层换成"每请求一个 unit of work"的仓储层后：147 个测试 8 个失败，整套 6 分钟，
  单个请求 8 到 26 秒。`docs/RATIONALE-db-temporal.md` 第 4 节事先就把"数据库调用变多，部分看板接口可能变慢，要量"列为风险。
- **怎么查的（每一步先量、再改、再量）：**
  1. 数整表扫描的调用点：`appointments.values()` 一类 grep 到 35 处，涉及 17 个文件。逐个改成 SQL 条件查询工作量大，所以先在存储层找办法。
  2. 量整表读取：19,473 个预约一次读出 0.81 秒（第二次 1.29 秒），病人 2,003 个 0.18 秒；一个慢测试（PHIPA 调查）40.8 秒。先加了"单行读取超过上限就整表读"（每表每 unit of work 16 次）。
  3. 只测 CPU 的基准（不碰数据库）：预约逐行 `model_validate` 解码 0.400 秒，病人加解密约 0.14 秒。试两种改法：一次 `TypeAdapter` 验证一批行（0.43 → 0.15 秒），
     flush 比对时直接读属性而不是 `model_dump()`（0.17 → 0.06 秒），并用 `same=True` 确认结果和原来一模一样。
  4. 接上后测 12 个看板接口：仍 0.9–1.7 秒。cProfile `/api/dose/overview`：时间主要花在**每个请求都整表解码、解密约 1.9 万个预约和病人**。改法：每个进程按
     `(row_key, xmin)` 缓存已解码的行，整表加载只取键和 `xmin`，只对变化过的行取数、解密、验证。热请求大约减半，但 dose overview 还是 1.27 秒。
  5. 再 profile：`clone`（从缓存拷一份给请求，原来用 `copy.deepcopy`）25,450 次累计 1.367 秒。基准比较几种拷贝：deepcopy 0.264 秒、按字段重新验证 0.103 秒
     （但对嵌套模型更慢：dose_records 0.122 vs 0.223）、自写的按类型拷贝 0.228 秒……最后按字段类型选拷贝方式，0.216 秒。
  6. 拷贝还是慢，怀疑不在拷贝本身：关掉 GC 再测，0.223 → 0.055 秒；`gc` 计数 (53911, 6, 8)，被追踪对象 560,338 个。原因是行缓存让堆变大，循环 GC 每次扫描都很贵。
     对比三种做法（不动、调高阈值 `gc.set_threshold(50_000, 50, 100)`、`gc.freeze()`），调阈值最好：scheduling dashboard 0.46 → 0.29 秒，dose 0.82 → 0.60 秒。选了在 API 启动时调高阈值。
- **根因：** 数据从"进程内的对象"变成"每个请求从数据库读、解密、构造"，而模块代码大量整表扫描，每个请求都在重复同样的解码；缓存住以后，大堆又让循环 GC 变贵。
- **怎么修：** 上面每一步都留下了：单行读取上限、批量解码、属性快照、`(row_key, xmin)` 行缓存（本进程写入时让对应缓存失效；标量共享，JSON 按字段类型复制）、
  按字段类型拷贝、启动时调高 GC 阈值。限制写明：每个进程一份缓存，内存约等于原来的内存 Store。
- **怎么证明：** 热请求 scheduling dashboard 0.85 → 0.3 秒、dose overview 1.5 → 0.6 秒、referrals 1.7 → 0.5 秒、contrast 1.2 → 0.4 秒、PHIPA 1.4 → 0.5 秒
  （PROGRESS 注明：第一个数已经包含批量解码和廉价 diff，第二个数再加行缓存和 GC 阈值；迁移后、优化前的时间没有记录）；后端全套 6 分钟（8 个失败）→ 2 分 33 秒（154 个全过）。
  缓存正确性用测试保证：外部写入后不会读到旧版本；同一事务写两次也不会；`test_objects_from_the_row_cache_are_independent` 检查从缓存拿出的对象互不共享可变数据。
  **并做了变异检查**：临时把 `clone` 改成浅拷贝，这个测试确实失败，再恢复代码。
- **经验：** 性能问题按"拆时间 → 找大头 → 换实现 → 再量"循环，每次只动一处。缓存会改变内存形态，带来新的成本（这里是 GC）。用 PostgreSQL 的 `xmin` 做缓存版本号，
  可以只在行真的变了时才付解密和校验的代价。给缓存写"不会给旧数据""拿出的对象不共享可变状态"两类测试，再故意把实现改坏一次，确认测试真能抓到。
- **范围：** imaging（阶段 0 数据层迁移）。
- **来源：** main__d95dd1c0 10-07 22:33–22:37；main__394ebe0a 10-07 23:15–23:39；PROGRESS 阶段 0 "Performance"、"Limits"。
- **代码：** `apps/api/app/core/db/repo.py`、`apps/api/app/main.py`（`gc.set_threshold`）、`apps/api/tests/test_db.py`；提交 9458db0。

### 7.4 医院生成器和模拟器的性能：先 profile，量到的大头和猜的不一样

- **现象：**
  - WP1：生成 65,204 个 FHIR 资源要 15.2 秒，再加编码 5.0 秒，每次重置和测试都要付这个钱。
  - WP3：全天快进 20 秒；全天测试第一次跑完整（包括最后的资源验证循环）要 65 秒；有一次 ICU 激增加全天快进用了 78 秒。
- **怎么查的：**
  1. WP1：先去掉生成时不必要的深拷贝和重复建索引：6.6 秒 / 编码 4.5 秒。再 cProfile：排第一的是 `datetime.astimezone`，204,804 次，1.78 秒（每个 FHIR 时间都单独查一次本地时区）。
     改成进程启动时取一次固定的本地 UTC 偏移：5.2 秒 / 编码 2.7 秒，合计约 8 秒。
  2. WP3 全天 20 秒：按累计时间排序看起来是约 4,200 次数据库往返。先缓存一个重复读取（入院体征基线）：还是 20.4 秒，"没变化，往返次数不是全部"。
     改按自身时间（tottime）排序：`select.select` 3,963 次 8.444 秒（Docker Desktop 上每次往返约 2.1 ms），其次是 FHIR store 的深拷贝。判断这在 Linux 服务器上会快很多，
     只记下本地数字，不为此重构。
  3. 78 秒那次：同时有全套后端测试在跑。单独重跑：22.4 秒（52 次推迟入院），确认是争用。
  4. 全天测试 65 秒：先怀疑是新加的"验证所有写入的资源"那段循环，单独计时：读 0.07 秒、验证 0.02 秒，排除。转而看事件投递：每个事件一个事务
     （会话估算 1,600 个事件约 16 秒；转录里没看到这个数的单独测量）。
- **根因：** WP1 是大量重复的时区换算；WP3 是每个事件一个事务的投递方式，加上 Windows 上 Docker 的数据库往返延迟。
- **怎么修：** WP1 用固定偏移；WP3 事件总线改为每个事务批量投递，某个处理函数失败时退回逐个投递（保留原子性、去重、顺序、"重试后搁置"语义），失败处理函数的测试改为检查记录的尝试次数。
- **怎么证明：** 全天测试 65 → 12 秒；报告里计划日 1,617 个事件 11.2 秒、ICU 激增 1,916 个事件 13.3 秒，顺序、一致性、验证都没有问题。
- **经验：** 缓存了一个读取却没变化，说明假设错了，要换个角度（按自身时间排序）再看。计时结果要在机器空闲时单独量，同时在跑的测试会让数字翻好几倍。
- **范围：** hospital（WP1 生成器、WP3 模拟器和事件总线）。
- **来源：** main__2427f64d 10-08 17:46–17:48；main__7a422c19 10-08 23:00–23:16。
- **代码：** `apps/api/app/ehr/events.py`；提交 e67bb5b（WP1）、5e9db1a（WP3）。

### 7.5 控制塔看板快照从 2 秒降到 0.3 秒：每改一步就重新剖析

- **现象：** 看板快照（急诊、床位、手术室三栏加 4 个预测模型）构建一次约 1.8 秒，cProfile 下是 `build ms 2031`。页面在模拟器运行时每秒刷新一次，所以必须在 1 秒内建完。
- **怎么查的：** 用 cProfile 跑一次 `snapshot.build`，按累计时间排序：
  - `flowmodels.predict` 4 次共 1.011 秒，其中 sklearn 的 `_raw_predict` 占 0.839 秒（每次预测的逐树开销）。
  - `_Reads.__init__` 0.967 秒，是 11 次网关搜索；每次搜索都单独写一条审计，`store.autonomous` 开了 22 个短事务（0.238 秒）。会话估计审计约占 0.5 秒。
  - 改完这两处后再剖析：890 毫秒，最大的一项变成 FHIR store 搜索里的 `copy.deepcopy`（63,949/937 次调用，0.310 秒）。
- **根因：** 三处开销：sklearn 每次预测的固定开销、每次读写一条审计、缓存资源每次返回都 deepcopy。
- **怎么修：**
  1. 预测改为用解释器已经算好的偏置加各特征贡献（bias + contributions）直接得分，不再调用 sklearn 的 predict。
  2. FhirGateway 加 `batch()`，看板的多次搜索合并成一条审计记录（提交说明："Ten searches audited as one batch"）。
  3. `fhirstore.search` 对第一次读到的资源保留解密后的 JSON 文本，解析两次：一份放缓存，一份返回（代码注释："parsing the JSON twice is several times cheaper than a deep copy"）。
- **怎么证明：** 每改一步都重跑剖析。前两项改完后 `build ms 890`；去掉 deepcopy 后连续三次构建是 547、327、296 毫秒。最终总结写的是一次构建约 0.25–0.45 秒，审计里只记一条。
- **经验：** 每次只动剖析结果里最大的一项，改完马上重新剖析，因为瓶颈会转移。"每次调用都审计、都深拷贝"这种看着无害的习惯，在每秒执行的路径上会叠加成主要开销（7.6 的轮询是同一个问题）。
- **范围：** hospital（WP5）。
- **来源：** main__dec77d7e 10-09 08:10–08:14。
- **代码：** `apps/api/app/modules/control_tower/snapshot.py`、`flowmodels.py`、`apps/api/app/ehr/gateway.py`（`batch`）、`apps/api/app/ehr/fhirstore.py`；提交 d9ffd9f。

### 7.6 故事线进程内走查太慢：一行测量推翻了两个"专业"假设

- **现象：** WP9 进程内走查（一个回滚事务里跑完 12 步）第 8、9、11 步的时钟快进各要 4–7 分钟（step 8 234.1 秒、step 9 311.6 秒），这样 10 分钟的现场演示也做不到。
  走查跑了 25 分钟没有任何输出（输出被缓冲）。
- **怎么查的：**
  1. 没输出时先看数据库在做什么：`pg_stat_activity` 显示一个连接 `idle in transaction` 已 18:46（走查的长事务），说明还在跑，没有卡死。
  2. 剖析：做一个只推进到 "night" 节拍的 cProfile 版本。347.5 秒里，7,654 条 SQL 共 317.6 秒（平均 41 ms/条），`select.select` 占 309.9 秒；430 次 `audit.record` 共 120.9 秒；
     13 次 `status()` 共 57.7 秒（每次 4.4 秒）。时间几乎都花在等数据库返回上。
  3. 假设 1：长事务里反复更新，MVCC 版本越堆越多，每次读要扫过更多版本（真实 API 每个请求都提交，不受影响）。考虑过改用脱离数据库的内存 Store。
  4. 假设 2：测试把每个 unit of work 包在 SAVEPOINT 里，成千上万个子事务溢出 PostgreSQL 的子事务缓存，拖慢可见性检查。
  5. "先测量再猜"：写了一个小基准（bench_tx.py）。新事务 38.6 ms/条；50 个带 savepoint 的审计每个 260.6 ms，之后读 6.6 ms/条；再 300 个每个 138.7 ms，之后读 6.5 ms/条；
     不带 savepoint 的审计 77.9 ms 一个，之后读 3.4 ms/条。读没有随写入增多而变慢，两个假设都不成立；但审计插入本身很贵。
  6. 看审计表：取链头的 `ORDER BY seq DESC LIMIT 1` 走索引，执行 0.185 ms，查询本身不慢。
  7. 量最简单的基线：裸 `SELECT 1` 要 34.48 ms（以前这台机器约 2 ms）。21 秒后再量：SQLAlchemy `select 1` 1.86 ms，裸 psycopg 1.69 ms。结论：慢在机器和共享 Postgres 的负载
     （记录归因于同时在跑的 replay 测试和其他会话），不在代码。
  8. 剖析同时暴露了真实的自身开销：每次 `status()` 4 秒，harness 每推进一步就调一次，页面也每 1–3 秒轮询一次；状态视图通过 FhirGateway 读病历，而每次读病历都写审计，
     所以每次轮询约写 15 条 read 审计事件。
- **根因：** 主因是环境负载（单次往返从约 2 ms 变成 20–40 ms），被一个长事务里的几千次往返放大；次因是状态视图每次轮询都重算并写审计。
- **和 PROGRESS 的说法不一致，证据怎么说：** PROGRESS 的 follow-up 把 "each audit event is its own savepoint in the test's transaction" 列为慢的原因之一。转录里的基准推翻的是
  "savepoint 让之后的**读**越来越慢"这两个假设（读一直是 3.4–6.6 ms/条）。带 savepoint 的审计插入（138.7–260.6 ms）确实比不带的（77.9 ms）贵，但这组数是在裸 `SELECT 1`
  要 34 ms 的负载下量的，之后没有在安静的机器上重量，所以 savepoint 本身的代价没有被单独证实。能确定的只有：负载下往返变慢 × 往返次数多；savepoint 是不是原因之一，
  现有证据既不能证实也不能排除。
- **怎么修：** harness 推进时改用便宜的 `storyline.beats()`，不再每步调 `status()`；状态视图的 EHR 事实加缓存（仿照 Control Tower 的快照缓存），键是故事线重置、
  store 变更计数、时钟、最后一个领域事件和最后一个审计决定；快进期间允许复用 8 秒内的事实。
- **怎么证明：** 第 8 步 234 秒 → 40 秒；1–7 步约 2.5 分钟；12 步共约 4.3 分钟（机器安静时）。PROGRESS 记录负载下仍要 15–25 分钟。
- **经验：** 解读基准之前先量一个最简单的基线（`SELECT 1`）。这次的基准本身就是在负载下跑的，"新事务 38.6 ms"已经是信号。两个听起来很专业的假设（MVCC、子事务缓存）
  被一行测量推翻。被审计的读会让"只读"的轮询变成写：审计、计费、限流这类"每次调用都有副作用"的层上面不要直接挂轮询，先给结果一个便宜的版本键，没变就不读。
  结论写进文档时，只写证据支持的部分。
- **范围：** hospital（WP9）。
- **来源：** wt-wp9-storyline-7d11cd 10-10 23:42–10-11 00:17；PROGRESS WP9 Results（In-process walk）、"Notes and deviations"、"Follow-ups (not WP9)"。
- **代码：** `apps/api/app/modules/storyline/service.py`（`beats`）；提交 8d3cd5e。

### 7.7 换成 COPY 后写入仍要 10 秒以上：瓶颈不在 PostgreSQL，而在 Docker Desktop 的端口转发

- **现象：** `persist()` 第一次改成 COPY 后测了一轮：`appointments` 从 3–5 秒降到约 0.6 秒，但 `fhir_resources`（64,653 行，COPY 负载约 94 MB）还要 9.8–12.3 秒，
  锁持有 14.1–18.6 秒。会话的判断是 "not clearly under target yet"。
- **怎么查的：**
  1. **先看表本身。** 查 `pg_indexes`：`fhir_resources` 有 8 个索引（主键加 7 个二级索引）；表总大小 113 MB，其中堆 69 MB；`data` 列是 extended 存储、默认压缩；主机和容器都是 4 核。
     假设：索引维护、WAL、TOAST 压缩拖慢了 COPY。
  2. **按假设逐项拿掉**（profile_fhir.py）：COPY 到无索引、无 WAL 的临时表；关掉压缩；只留主键；事后重建 7 个索引（1.6–1.8 秒）。结果噪声太大：同一项"COPY 到无索引临时表"
     两轮分别是 25.73 秒和 14.45 秒，"COPY 到 fhir_resources（8 个索引）"是 14.97 秒和 26.03 秒。在这种波动下，没法把时间归到索引或 WAL 上。
     （第一次运行还碰到一个小坑：`LIKE ... INCLUDING DEFAULTS` 不复制 identity，见 6.7 表。）
  3. **换个切法：把一次 COPY 拆成几段分别计时**（profile2.py，三轮）：psycopg 只格式化行、不做 I/O 0.98–1.15 秒；把预先格式化好的 94 MB 发进临时表 10.07–13.87 秒；
     纯服务器端 `INSERT INTO temp SELECT * FROM temp` 1.29–2.59 秒；纯服务器端 TRUNCATE 加 `INSERT ... SELECT` 写进 fhir_resources（8 个索引、WAL）4.48–6.95 秒；
     反向 `SELECT data` 取回 2.89–4.59 秒。大头在"把数据从 Windows 送进容器"：上行只有约 7–9 MB/s，取回约 20–28 MB/s。
  4. **新假设：Docker Desktop 的端口转发慢。** 对照实验：把同一份 COPY 负载写成文件（`fhir.copy`，93,745,538 字节），一次从 Windows 主机经 `127.0.0.1:5433` 发送，
     一次从 compose 网络里的容器直连 `postgres:5432`（复用本地已有的 API 镜像，不用下载），两种路径交替各跑两轮：
     - 主机经端口转发：临时表 20.21 / 23.45 / 39.35 / 21.32 秒；fhir_resources 20.58 / 22.08 / 25.22 / 33.69 秒
     - 容器走 compose 网络：临时表 4.82 / 4.71 / 4.20 / 4.78 秒；fhir_resources 10.74 / 10.81 / 10.51 / 12.89 秒
     假设证实：写临时表相差 4–9 倍，写 fhir_resources 约 2–2.6 倍。剩下的是负载下 PostgreSQL 维护 8 个索引的开销。
- **根因：** 开发机上 API 跑在 Windows 上，PostgreSQL 跑在 Docker Desktop 里，94 MB 的批量数据要经过 Docker Desktop 的端口转发，吞吐只有 7–9 MB/s。这是本机环境的特点，不是代码问题。
- **怎么修：** 没有再改代码，记为未完成项，留给负责人两个选项：一是调长取锁活动的 start-to-close 超时（会改变工作流时序，要负责人决定）；二是在拿锁之前先把数据 COPY 进临时表，
  锁里只做 `INSERT ... SELECT`（fhir_resources 4.5–7.0 秒）。
- **怎么证明：** 第 4 步的交替对照，只改网络路径，负载完全相同。会话起初在 PROGRESS 里写的是"2–4 倍"，对照数字出来后更正了。
- **经验：** 计时噪声大到分不清假设时，不要在同一层继续加实验。把一个操作拆成互相独立的几段（格式化、传输、服务器写入、读回）分别计时，再做一个只改一个条件的对照
  （同一份负载，换一条网络路径）。数据库跑在 Docker Desktop 里时，数据传输本身就可能是瓶颈。
- **范围：** 环境（开发机的 Docker Desktop），影响平台的演示重置。
- **来源：** wt-copy-reset-writes-0a4f34 10-11 22:57–23:20；engineering-notes.md note 8 "Where the rest goes"；PROGRESS "WP10 follow-up · the demo reset writes its rows with COPY"。

---

## 八、合成数据与种子校准

合成数据既是演示的道具，也是测试的输入。这一章的共同点：数据"不像真的"或"不满足故事需要"时，先把关键指标量出来，再动参数；
改生成器要预期所有依赖"现在这些病人"的测试都会动；生成器和用数据的组件之间的引用、id、时间约定，要有测试走完整条链。
和日期有关的种子问题（Westbrook、中文问卷、账单窗口）在第五章。

### 8.1 生成器第一次校准：医院被挤爆，用统计脚本按数字调

- **现象：** WP1 生成器第一版（132 张床）：ED 在院人数中位 54、最多 86，boarding 中位 47 小时（p90 70.3），按 CTAS 分级的等候时间 3 级为 203.6，
  789 台手术里 197 台取消。整个医院被挤爆。
- **怎么查的：** 写统计脚本 `simstats.py`，每次改参数都打印 ED 人数、各病区占用、等候时间、boarding、手术取消原因，按数字调：ED 每日到达 68 → 58 → 54 → 52；
  入院截距 -2.1 → -2.6 → -3.3 → -3.8（太空）→ -3.5；ALC 尾巴缩短；ICU 段延长；忙碌病人的择期手术改为重新预约（取消从 66 降到 13）。中途调低后 ICU 只有 2–3/12，
  63 台择期手术因为 "Patient admitted elsewhere" 取消（约 14%）。最后按播种日 +0…+3 天看 07:00 的人数，确认"早上安静、白天变忙"的模式。
- **根因：** 参数凭感觉定的初值和 132 张床的容量不匹配（会话的估算：每天约 23 个入院、平均住院近 7 天，超过床位数）。
- **怎么修：** 按上面的数字把参数调到"负载合理"。留下的问题是 ICU 偏空（8.2）。
- **经验：** 合成数据要像真实系统一样"有观测"：每次改参数都打印关键指标，用数字调，不凭感觉。动参数前先算一笔容量账（入院数 × 住院天数 对比床位数）。
- **范围：** hospital（WP1 生成器）。
- **来源：** main__2427f64d 10-08 17:39–17:43。
- **代码：** `apps/api/app/ehr/reference.py`、`apps/api/app/ehr/seed/simulate.py`；提交 e67bb5b。

### 8.2 ICU 太空：前后被看到三次才修，修时顺带挖出一个 ALC 的 bug

- **现象：** 生成的 12 床 ICU 在 60 天时间线上占用率 17%–58%（平均约 47%，工作日 07:00 中位数 5–6/12），各病房却是 85%–100%；真实 ICU 一般在 75%–85%。
  **同一个问题前后被看到三次：**
  1. WP1 调参后 ICU 只有 2–3/12（8.1）。
  2. WP3（10-08）：ICU 激增场景（固定 3 个危重病人）在播种日根本触发不了 boarding：07:00 ICU 2/12，空 10 张床。WP3 把激增默认人数改为"填满 ICU 再加 1"（至少 3 人），
     激增后 ICU 12/12、前 2.5 小时有 2 人在 ED 等床；把 ICU 偏空记为给 WP5 的发现，没去改 WP1 的生成器。
  3. WP5（10-09）：Control Tower 的占用率规则在病房 07:00 就触发，ICU 只有跑脚本 `icu_surge` 之后才触发；叙述评测走完脚本流程只收集到 16 个异常
     （`{'or_overrun': 5, 'preop_gap': 7, 'unit_occupancy': 4}`），达不到要求的 30 个、覆盖 4 条规则（`FAILED in 22.2 s`），急诊候床规则（超过 2 小时的候床者 ≥ 3 人）一次都没触发。
- **怎么查的：**
  - **WP5 先查规则为什么不触发：** 写 `walk.py` 打印每一步的看板：注入 ICU surge 把 ICU 填满之后，候床者 `boarders` 仍然是 0；再写 `bedreq.py` 直接看床位申请：`active bed requests []`；
    读模拟器的 `_on_admit`：找不到床的病人走 `_defer_stay`。结论：surge 的默认规模（填满 ICU 再多 1 人）只会留下 1 个候床者，规则的阈值永远达不到。WP5 内先调场景：
    surge 8 人只有 2 个候床超过 2 小时，改为 10 人后有 4 个，规则会触发；评测走的流程同样改大（30 个异常：`{'ed_boarding': 2, 'or_overrun': 10, 'preop_gap': 10, 'unit_occupancy': 8}`）。
    WP5 **不改**生成器：提高 ICU 比例会让之后每一次随机抽取都移位，所有住院、床位和计划事件都会变，而当时另一个会话正在修依赖这些 ICU 病人的测试（5.6）；记为限制，留给负责人单独开会话。
  - **校准会话：**
    1. **先测量**：写一个脱离数据库的草稿脚本（`people.build_*` + `simulate()`），对周一到周五每个医院日（数据只随星期变化），14 天预热后每 3 小时数一次各病区床位、
       急诊滞留和 ICU 普查。基线：周一到周五 07:00 的 ICU 人数 5/6/4/2/7，24 小时平均 47%。
    2. **拆开看**：按诊断统计 ICU 入住：46 天 915 次入院（19.8/天），ICU 只有 1.27/天（急诊入院的约 6% 加择期 CABG）；而 ICU 平均住 4 天左右，已经合理。
       结论：杠杆是**进 ICU 的比例**（`ADMIT_DX` 的 ICU share），不是住 ICU 的时长。
    3. 在草稿脚本里加 `--shares` 参数覆盖比例，网格搜索约 120 种组合（并行 3 个进程；PROGRESS 记的临床合理范围是心梗 0.4–0.6、脓毒症 0.5–0.7、卒中 0.2–0.3、
       肺炎/心衰/COPD 0.1–0.2，带和不带下面的 ALC 修复各搜一遍），按 5 个工作日 07:00 的普查、急诊滞留和长期指标打分。
    4. 换了比例后看到周五 07:00 的 ICU 里有 2 个病人被标成 ALC（等待转出的"替代级别照护"）。专门写脚本量"ALC 住院贡献了多少 ICU 床日"：周三 ALC 住院占了 1.11 张 ICU 床，
       周五有 2 个 ALC 病人在 07:00 还在 ICU。读 `simulate.admit()`：ICU 段长度是 `los_days * U(0.45, 0.65)`，而 ALC 住院的 `los_days` 已经**包含了** 5–16 天的 ALC 等待，
       于是 ALC 病人可能在 ICU 住 9–10 天并在 ICU 里被标成 ALC。
    5. 修了这个 bug 后 ICU 均值掉到 54–66%，原网格里没有一个组合五天都达标，于是把网格整体上移一档重新搜索。
    6. 比例一变，后面所有随机抽取都移位，所以"某个工作日 07:00 谁在 ICU"对比例是混沌的：结果是在合理范围里**挑出来的**最好的一组，不是拟合出来的最优解。
    7. 为什么不追求 75–85%：12 床单元的普查接近泊松，日间波动约 3 张床；平均 80% 时 ICU 约三分之一时间是满的（Erlang C），而生成器在 ICU 满时不会提前转出，ICU 病人会在
       急诊滞留数小时。更高的比例（脓毒症 0.7、肺炎 0.2）平均达到 71%，但周五一开始就有 3 个超过 2 小时的急诊滞留。
- **根因：** 生成器里各入院诊断进 ICU 的比例偏低；另有一个真 bug：ALC 住院的 ICU 段按"含 ALC 等待"的总住院时长算，应按预期的急性期时长算。
- **怎么修：** 一行修复：ALC 住院的 ICU 段取自预期急性期时长（`acute_days = expected if alc_from else los_days`，不多抽随机数）。比例：心梗 0.3→0.5、脓毒症 0.4→0.6、卒中 0.15→0.2、
  肺炎 0.08/0.05→0.15、心衰 0.05→0.15、COPD 0.08→0.12。控制栏 ICU surge 默认 10→6；流模型重训，控制塔评测和 WP3 报告重新生成。连带的模拟器边界问题和测试改动见 5.9。
- **怎么证明：** ICU 入住 1.27/天 → 2.2/天（入院的 11%），ICU 平均住 3.5–3.7 天，ICU 里的 ALC 病人 0；24 小时平均 47% → 64%，工作日 07:00 中位数 5–6 → 8，
  周一到周五 07:00 为 10/9/8/10/11；病房 79%、急诊滞留不变。提交前确认"写进代码的参数"能逐字复现草稿脚本里选中的那一组结果（`diff` 结果 IDENTICAL）。
- **经验：** 合成数据先拿真实世界的基准量一遍（占用率、比例），再在上面写规则和评测；规则只能靠注入场景才触发，说明底层数据不真实。调生成器先量、再拆，找到真正的杠杆再动参数；
  拆解时把"异常的个体"（ICU 里的 ALC 病人）单独量一遍，常能顺带挖出 bug。参数会打乱整个随机序列时，只能在合理范围里批量试、按多个指标打分，并在文档里写清结果是挑出来的。
  改数据之前先想清楚有谁依赖"现在这些病人"：这次牵出了 4.2、5.4、5.9 三个后续问题。同一个数据问题被看到两次都只是绕过，第三次才修：发现数据异常时最好当场记成待办。
- **范围：** hospital（WP1 数据，WP3、WP5 发现）。
- **来源：** main__2427f64d 10-08 17:39–17:43；main__7a422c19 10-08 23:05–23:07；main__dec77d7e 10-09 07:48–07:49、08:28–08:31、08:58、09:54；wt-gracious-cray-14c437 10-09 13:14–13:51；
  PROGRESS WP3 "Finding for WP5"、WP5 "ICU calibration: not changed"、"WP1 data change · ICU occupancy"；`docs/data-model.md` "Generator calibration"。
- **代码：** `apps/api/app/ehr/reference.py`（`ADMIT_DX`）、`apps/api/app/ehr/seed/simulate.py`、`apps/api/app/ehr/scenarios.py`；提交 1c36c9e（WP3 改激增默认值在 5e9db1a）。

### 8.3 术前检查 Task 指向错误的 id，模拟器永远关不掉它们

- **现象：** 手术开始时模拟器应该关掉的术前检查 Task 一直开着。
- **怎么查的：** PROGRESS 把它记在 WP5 开头的只读审计里；转录里能看到的查证发生在写完看板快照（含手术室的术前准备状态）之后，转录没写是什么让它去查这一点。
  模拟器在手术开始时用 `self.fhir.search("Task", focus=f"Appointment/{appt['id']}", status="requested")` 找开放的术前检查；再看生成器 `materialize.py`：Task 的 focus 写的是
  `Appointment/surg-NNNNN`（手术编号），而 Appointment 的真实 id 是 `appt-NNNNNN`（`b.appointment(...)` 的返回值）。两边的引用对不上，搜索永远是空的。
  已有测试 `test_ehr_store.py` 只检查"存在 requested 状态的术前 Task"；WP1 有"每个临床资源都有存在的 encounter"的完整性测试，但没覆盖 `Task.focus`。所以没发现。
- **根因：** 生成器和模拟器对同一个引用用了不同的 id，引用指向了一个不存在的资源。
- **怎么修：** `materialize.write_surgery` 保存 `b.appointment(...)` 的返回值，Task 的 focus 指向 `Appointment/{appointment['id']}`。只改这一个字段，不改随机数的抽取。旧数据库要重置后才生效。
- **怎么证明：** 修复后重新播种了自己的库。转录里没有为它单独加测试，也没有修复前后的对比。
- **经验：** 两个组件靠一个引用串起来时，测试要走完整条链（生成 → 模拟器按引用找到 → 状态改变），只断言"数据存在"不够。生成器里的引用最好有"每个引用都能解析"的完整性测试。
- **范围：** hospital（WP1 数据，WP5 发现）。
- **来源：** main__dec77d7e 10-09 08:09–08:10；PROGRESS WP5 只读审计、"WP1 data fix"。
- **代码：** `apps/api/app/ehr/seed/materialize.py`；提交 d9ffd9f（"pre-op checklist Tasks now point at their Appointment (the simulator never closed them)"）。

### 8.4 往返测试抓到"表示漂移"：`[]` 和缺省、`5` 和 `5.0`、`""` 和 `None`

- **现象：**
  - 25 个资源示例做"验证再导出"的往返，4 个失败。逐字段比较后：`dosageInstruction[0].route.coding None != []`、`doseQuantity.value 5 != 5.0`、
    `identifier[0].type.coding None != []` 等。
  - 影像检查 → FHIR → 影像检查的往返：`ImagingStudy.appointment_id` 原来是 `None`，回来变成 `""`。
- **怎么查的：** 写一个递归比较函数打印第一个不同的路径和值，原因一眼可见。适配器则先拿全部 19,468 个种子预约跑一遍往返，再写测试。
- **根因：** Pydantic 模型里列表字段默认 `[]`，而 FHIR 省略空列表；数量字段只声明了 `float`，整数被转成浮点；适配器把"必填但允许 None"的字段当成"必填文本"填了 `""`
  （FHIR 没有空字符串）。
- **怎么修：** 列表字段改为 `list[...] | None = None`，数量 `value: int | float | None`；适配器先判断注解里是否允许 None。
- **怎么证明：** 25 个示例全部往返一致；19,468 个预约往返无差异，测试里 20 个样本无损往返。
- **经验：** 凡是格式转换都写"往返相等"测试，并用全部真实形状的数据跑一遍。出错时打印第一个不同的路径，比读整个对象快得多。
- **范围：** hospital（WP2 FHIR 类型、影像检查 ↔ FHIR 适配器）。
- **来源：** main__2427f64d 10-08 18:20–18:21；main__4f2eefc4 10-08 19:34。
- **代码：** `apps/api/app/fhir/types/`、`apps/api/app/ehr/imaging.py`；提交 ae85709、8d6ba41。

### 8.5 周一的数据上，预置病例的 id 和生成器的 id 撞了

- **现象：** `test_the_preset_balances_surgery_on_a_monday` 失败：`ValueError: Condition/cond-000001 already exists`（`fhirstore.py:313`）。
- **怎么查的：**
  1. 周五的演示库上预置一直正常：量过骨科 24/24、外科 32/32、2 个可出院，预置根本不用补病人。
  2. 周一的数据（脱离数据库跑 `generate(store, 7, datetime(2026, 10, 5, 10, 0))`）有空床，预置要补住院：`_stay(... ready=True)` → `write_stay` → `b.condition(...)`。
  3. 看 `Builder.next_id`：每个 Builder 有自己的计数器，从 1 开始，所以第二个 Builder 发出的 id 和生成器已经写入的重复。
  4. 对照模拟器的 `SimBuilder`：它把 id 限定在自己的 scope（`<prefix>-sim-N`），早就解决过同一个问题。
- **根因：** 同一个 Store 里有两个各自从 1 计数的发号器。只有需要补病人的星期才会走到这段代码。
- **怎么修：** `_PresetBuilder(Builder)`，id 用自己的 scope `<prefix>-jpNNNN`（如 `cond-jp0001`），并写进 `docs/data-model.md`。
- **怎么证明：** 重跑 WP9 测试，周一测试通过。
- **经验：** 同一份数据里有多个发号器时，要么共用一个，要么各加前缀；先看看项目里有没有已经解决过同一个问题的代码（这里是 `SimBuilder`）。
  只在演示库那一天测过的代码，换个星期可能走到从没测过的分支（和第五章同一主题）。
- **范围：** hospital（WP9 故事线预置）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 00:23–00:24。
- **代码：** `apps/api/app/ehr/seed/journey.py`（`_PresetBuilder`）；提交 8d3cd5e。

### 8.6 拿统计模拟器演精确剧本：没床位、进了溢出床、固定时刻被推迟

- **现象（修好一个，下一个才露出来）：**
  1. WP9 故事线第 5 步"写入的记录"里没有她的床位，她的住院 Encounter 没有 location。
  2. 修好后，第 8 步报 403：`This patient is outside your units`（外科护士 U-NURS-13），因为她被收进了 Medicine B 的溢出床 `MEDB-12-A`。
  3. 再修好后，第 5 步超时：`timed out waiting for step bed done`。"admitted" 节拍到了，她却还没入院。
- **怎么查的：**
  1. 第 1 个：通过故事线的 gateway 读她的 Encounter，location 是空的；判断是一次重试入院的时间早于床位的计划开始时间，EHR 就不写床位。
  2. 第 3 个：给走查脚本加诊断输出（MARKS/PLAN/READY/BED），只跑到第 5 步。PLAN 列表显示：为腾出的床专门插入的入院本应在 10:01，却出现在 11:01；夜间生命体征 02:00 → 03:30；
     出院处方 17:00 → 18:30；出院 11:00 → 12:30。也就是每次入院失败重试，都把这段住院后面的事件整体推后 30 分钟。
- **根因：** 模拟器是为生成的病人设计的。没床时延期（`_defer_stay`）会把这段住院后面的所有事件一起推后；入院找床时按 `[unit, *OVERFLOW]`，可以落到溢出科室；
  重试入院早于床位计划开始时间时不写床位。这些"自动修正"和故事线的精确剧本冲突。
- **怎么修：** `_on_admit` 把第一张床的开始时间设为入院时间（对生成的住院没有影响）；计划条目可以指定可入院的科室（`bed_units ["ORTH","SURG"]`）；计划事件可以标 `fixed`，
  `_defer_stay` 不移动它们（`moved = [x for x in tail if (x.get("stay") == sid or x.get("order") in orders) and not x.get("fixed")]`）。故事线的出院、夜间生命体征、
  出院处方和插入的入院都标 `fixed=True`。
- **怎么证明：** 进程内走查第 5 步得到 "bed SURG-…"，之后 12 步全过。
- **经验：** 拿一个为"统计意义上的病人"设计的模拟器去演精确剧本时，它的自动修正（延期、溢出）会和剧本冲突，要把剧本的硬约束显式写出来（`fixed`、`bed_units`）。
  查时间类问题，打印事件计划表比看最终状态更快。
- **范围：** hospital（WP9 故事线，WP3 模拟器）。
- **来源：** wt-wp9-storyline-7d11cd 10-10 23:11–23:23。
- **代码：** `apps/api/app/ehr/simulator.py`（`_on_admit`、`_defer_stay`）、`apps/api/app/ehr/storyline.py`；提交 8d3cd5e。

### 8.7 规格里的故事数字，模型给不出来，规则也不触发

- **现象：** 规格要求手术预测 110 分钟（预约 90 分钟）。WP9 开工前的审计发现这个例子"低于阈值"：`or_overrun` 要求预测比预约多至少 30 分钟，+20 不会触发，演示里就不会出现提醒。
- **怎么查的：** 不硬编码故事数字，而是在已提交的流量模型上搜索输入组合，让模型自己算出这些数字。入院概率 0.82 有很多合理的生命体征组合；手术时长方面，110 分钟只在不合理的组合下出现
  （例如择期手术、特定的预约时间），最接近的合理病例是 Dr. Osei、ASA 2、urgent、全髋置换，预测 115 分钟。第一个搜索脚本跑超过 10 分钟，被手动停掉后缩小了搜索范围。
- **根因：** 规格作者写故事数字时没有对照模型的实际输出和规则的阈值。
- **怎么修：** 故事改用 115 分钟（记为偏差）；规则新增 block-overtime 条件：多出至少 15 分钟并且会超过 16:00 的手术块结束时间（`RuleConfig.or_block_overrun_minutes=15`），
  旅程的 proposeSchedule 共用同一个判断。
- **怎么证明：** 规则评测 20/20；narrator 评测 30 个异常，schema、溯源、守卫都是 100%（新的 block-overtime 措辞能通过守卫）。
- **经验：** 演示和测试里的"故事数字"要由真实组件算出来，再去对照规则阈值，否则演示时 AI 根本不会提出建议。
- **范围：** hospital（WP9，Control Tower 的 `or_overrun` 规则）。
- **来源：** wt-wp9-storyline-7d11cd 10-10 20:43（审计）、21:05–21:23（搜索）、21:39–21:44（改规则）；PROGRESS WP9 "Story numbers"。
- **代码：** `apps/api/app/modules/control_tower/rules.py`（`or_block_overrun_minutes`）；提交 8d3cd5e。

### 8.8 其他数据小记

| 问题 | 处理 | 来源 |
| --- | --- | --- |
| 生成器把每周一次的甲氨蝶呤写成 "once daily"（`app/ehr/codes.py`），患者说明书会照抄 | 发现，未改 | PROGRESS WP7 |
| 知识图谱的事实很嘈杂（高血压下列着腹痛、脓毒症下列着呕吐、阿莫西林下列着 COPD） | 只有通过筛选规则的事实才用来解释术语 | PROGRESS WP7 |
| 故事线讲解脚本的预算断言 `AssertionError: 605`（12 步讲解总时长不得超过 600 秒） | 把第 5、6 步压到各 65 秒。把"演示 10 分钟以内"写成断言，超了马上知道 | wt-wp9-storyline-7d11cd（压缩前摘要） |

---

## 九、外部服务与测试替身（HAPI、Temporal）

外部服务有自己的默认行为（缓存、版本差异），测试替身（时间跳跃测试服务器、共享连接的测试 Store）和真实系统的行为也不完全一样。
这一章的共同做法：先弄清外部服务和替身的默认值；"任务排好了但没人领"这类现象，就把怀疑集中到那个机制上，再**把它关掉**看问题是否消失；
替身模拟不了的路径，用真实环境单独补一个检查。

### 9.1 HAPI 的 60 秒搜索缓存：病人数"卡住"、双后端一致性测试偶发失败

- **现象：**
  - 第一次（WP1 全量加载 1,000 个病人进 HAPI 时）：`Patient?_summary=count` 在 18:17:56 返回 239，一分钟后还是 239。
  - 第二次（两小时后，WP2 全套后端测试）：264 过、1 失败，`test_both_backends_answer_the_same` 断言 `{'waiting': True} != {'waiting': False}`：HAPI 后端的床位板少了一个刚入院的病人。
    单独跑这个测试能过。
- **怎么查的：**
  1. 第一次：怀疑加载停了，或者 HAPI 缓存了搜索结果。直接按 id 读 `pat-0400`、`pat-0700`（还没加载到，404），再用 `_summary=count&_cache=no` 加 `Cache-Control: no-cache` 头重新计数：
     341。确认是缓存，加载没停。当时只是绕过，没有改代码。（同一时段还有一个小坑：加载日志一直是空的，因为管道里 `grep` 写文件时会缓冲输出，所以改用 HAPI 计数看进度。）
  2. 第二次：假设是同一个缓存（全套测试里之前有一次同样的搜索）。先尝试复现：先发一次床位看板查询"预热"，再跑这个测试，结果**通过了**，假设没被证实。
  3. 改成直接做实验：同一个 Encounter 搜索（status 为 arrived、triaged、in-progress、onleave）先查一次得 119 条；新建一个符合条件的 Encounter；几秒内再查，仍然 119 条，找不到新建的；
     带 `Cache-Control: no-cache` 再查：total 120，找到了。假设证实：HAPI 默认会把相同搜索的结果重用 60 秒。
- **根因：** HAPI FHIR 服务器默认缓存相同搜索 60 秒。本地后端每次都查最新数据，HAPI 后端会返回旧结果，比如床位看板漏掉刚入院的病人。
- **怎么修：** `HapiClient` 每次搜索都带 `Cache-Control: no-cache`，并加测试固定这个行为。
- **怎么证明：** 同一实验改后 before 119、after 120，新 Encounter 找得到；gateway 测试 20/20；全套后端 265 过；`gateway_demo.py` 对本地和 HAPI 后端给出同样的输出。
- **经验：** 同一个现象第一次出现时只绕过、没追根，两小时后变成了偶发失败。复现尝试没成功时不要马上放弃假设，换一个更直接、更可控的实验（"查 → 写 → 立刻再查"）。
  "写完马上读不到"时，除了事务和复制延迟，还要想到外部服务自己的缓存。两个后端做同一件事时，写一个"两边答案必须一样"的对照测试，这类差异会自己冒出来。
- **范围：** hospital（WP1 HAPI 加载、WP2 FhirGateway）。
- **来源：** main__2427f64d 10-08 18:16–18:19；main__4f2eefc4 10-08 20:04–20:07；PROGRESS WP2 "Notes and deviations"。
- **代码：** `apps/api/app/ehr/hapi.py`、`apps/api/tests/test_fhir_gateway.py`；提交 8d6ba41。

### 9.2 HAPI 冒烟测试的两个小坑：R4 没有 `physicalType` 搜索参数；`.sh` 被检出成 CRLF

- **现象：** 冒烟脚本 `smoke_fhir.sh` 在 Location 检查处抛 `json.decoder.JSONDecodeError: Expecting value: line 1 column 1`；提交时 git 警告 `.sh` 会被转成 CRLF。
- **根因：** 脚本用了 `Location?physicalType=bd`，按提交说明这是 R5 才有的搜索参数（"R4 has no physical-type search parameter"），HAPI（R4）返回 400，脚本拿到的不是 JSON，
  真正的错误被一个无关的 JSON 解析错误盖住了。另一个是 Windows 上 git 默认把文本文件检出成 CRLF，bash 脚本会因此出错。
- **怎么修：** 取 `Location?_count=500` 后在客户端按 `physicalType` 代码 `bd` 挑床位；9 秒后另一个提交给 `.gitattributes` 加了 `*.sh text eol=lf`。
- **怎么证明：** 其余检查全部 PASS（20 个病人的试加载只差病人数）；全量加载 1,000 个病人 9.6 分钟、0 个失败 bundle 后冒烟全过。
- **经验：** 脚本里解析 JSON 前先检查 HTTP 状态码，否则真正的错误（400）会被盖住。对接 FHIR 服务器先确认版本（R4/R5）支持哪些搜索参数。在 Windows 上维护 bash 脚本，
  用 `.gitattributes` 固定 LF。
- **范围：** hospital（WP1）。
- **来源：** main__2427f64d 10-08 17:59–18:00。
- **代码：** `apps/api/scripts/smoke_fhir.sh`、`.gitattributes`；提交 ef4b039、f4c601f。

### 9.3 重启 worker 的测试永远等不到：时间跳跃测试服务器不转交粘性队列上的任务

- **现象：** 验收测试 `test_a_restarted_worker_resumes_the_journey_without_running_completed_steps_again` 超时：
  `timed out waiting for journey-stay-00942 wardMonitoring in ('waiting',)`，工作流停在 `medRecAdmission`。同批的完整旅程和 MedRec 重试测试都通过。
- **怎么查的：**
  1. 第一个假设：代码改了版本（加了 `workflow.patched()` 的编码步骤），新 worker 重放历史时不确定（non-determinism）。写了一个脚本把录下的 v1 历史拿当前代码重放：`OK journey_v1.json`，排除。
  2. 第二个假设：粘性队列（sticky queue）。有工作流缓存时，Temporal 把下一个工作流任务发给上次跑它的那个 worker；那个 worker 死了，真实服务器会在 10 秒的 schedule-to-start
     超时后转交给别的 worker。于是在发信号后把测试服务器的时钟拨过这 10 秒，**仍然失败**。
  3. 让 harness 在超时时打印工作流最近的历史事件：两个签名信号都到了（`workflow_execution_signaled`），也有 `workflow_task_scheduled`，但没有任何 worker 领走。
  4. 查重启后的 worker 是否在轮询：事件里任务队列的种类（sticky / normal）没有填，无法下结论。
  5. 直接验证假设：让测试 worker 不用工作流缓存（`max_cached_workflows=0`），这样根本不会有粘性队列。重启测试通过。
- **根因：** Temporal 的时间跳跃测试服务器（time-skipping test server）不会把死掉的 worker 的粘性任务转交出去，而真实服务器会。
- **怎么修：** 测试 worker 一律不缓存工作流；副作用是每个工作流任务都重放完整历史，顺带检查了每一步的确定性（另一个副作用见 2.12）。粘性队列交接这条路径改由
  `scripts/workflow_demo.py` 在 docker 的 dev server 上**真杀一个 worker 进程再重启**来验证（用 TerminateProcess）。
- **怎么证明：** 重启测试通过（没有步骤被调度两次，只有一个 MedRec 文件）；真实服务器演示：MedRec 在第 3 次尝试成功、FHIR 里只有 1 份 MedRec 文档，杀掉 worker 后重启，
  旅程继续（会话里量到 23 秒，PROGRESS 写约 20 秒，评测报告记录 16.5 秒），已完成的步骤没有重跑；`evals/workflows/report.md` 33/33。
- **经验：** 测试替身（这里是测试服务器）和真实系统的行为不完全一样。排查时把"历史/状态"打印出来，看到"任务排好了但没人领"，就把怀疑集中到分发机制；
  验证假设最干脆的办法是**把那个机制关掉**看问题是否消失。测试替身模拟不了的路径，要用真实环境单独补一个检查。
- **范围：** hospital（WP4c）。
- **来源：** main__025b79fc 10-09 22:21–22:36、23:03–23:05；PROGRESS WP4c "Test workers run without a workflow cache"；`apps/api/app/workflows/README.md` "Known failure modes"。
- **代码：** `apps/api/tests/temporal_harness.py`、`apps/api/scripts/workflow_demo.py`；提交 da35c54。

### 9.4 残留的 Temporal 测试服务器：启动超时跳过了整个 teardown，还让 shell 循环卡住

- **现象：**
  - 第一次：为了确认 2.5 的 teardown 偶发错误在自己改动之前就有，会话开两个 CPU 空转进程，在旧代码上把故事线工作流测试跑 5 次。20 分钟没有任何输出，日志文件 0 字节。
  - 第二次：2.5 修完后的最后一次确认运行里，`test_stopping_the_worker_lets_an_activity_finish_the_query_it_is_in` 失败，报 `TimeoutError`（`concurrent.futures ... result(timeout)`，
    `timeout = 120`），而且又多出一个 `temporal-test-server-sdk-python-1.33.0.exe`（PID 28308）。
- **怎么查的：**
  1. **第一次卡住时。** 先查测试库的 `pg_stat_activity`，看有没有在等锁的会话：只有这条查询自己，说明不是卡在数据库里。再列进程，发现 15:02 启动的
     `temporal-test-server-sdk-python-1.33.0.exe`（PID 29832）还活着，父进程是会话自己的 pytest 循环。判断：第一次旧代码运行到 900 秒超时被结束，但测试服务器没退出；
     它继承了 `$(...)` 的输出管道，管道一直不关，整个 shell 循环就卡住了。会话尝试 `Stop-Process`，被 auto mode 拒绝（`Interfere With Workloads`）。于是停掉自己的后台循环，
     把被循环换掉的旧版 store.py/repo.py 恢复回来，改成每次运行把输出写到单独的文件，再跑 6 次。
  2. **第二次失败时。** 先看机器状态：`LoadPercentage 100`，4 个逻辑处理器，另一个会话的 pytest 正在跑。traceback 显示超时发生在 `start_worker`：它给 worker 120 秒，
     这段时间还包括在事件循环里构建 worker、在 Temporal 沙箱里校验每个工作流。用 `Get-CimInstance` 查新出现的测试服务器 28308 的父进程链，确认是自己这次运行留下的。
  3. **读旧的 `harness()`。** `start_worker` 在 `try` 之前，它一超时就跳过整个清理（`bridge.uninstall()` 和 `_shutdown`）；`stop_worker` 超时又会跳过 `_shutdown`。
     `_shutdown` 本身不会挂住：最多等 60 秒，异常都被吞掉。
- **根因：** harness 的清理只在部分代码路径上执行，负载下启动超时恰好走了不清理的那条路。测试服务器是独立进程，pytest 被杀掉或挂住时它也会继续活着。
- **怎么修：** `harness()` 改用 `contextlib.ExitStack`：每个启动步骤成功后立刻登记对应的撤销步骤，任何一步失败，已登记的撤销步骤都会执行。
- **怎么证明：** 写了一个临时 pytest 插件 `startfail.py`，让 `start_worker` 直接抛 `TimeoutError("scratch: the worker did not start in time")`。测试失败，但没有留下新的测试服务器，
  只剩之前的 29832 和 28308。之后 `test_workflows.py`、`test_db_threads.py`、`test_workflow_replay.py` 26/26 通过，后端全套 778/778（30 min 45 s），跑完也没有新的残留进程。
  900 秒的挂起本身没能复现；120 秒的启动上限在高负载下可能被碰到（"where the time went was not measured"），记为未完成项。两个残留进程会话无权结束，PID 报给了负责人。
- **经验：** teardown 要考虑"启动只做了一半就失败"的情况，用 `ExitStack`（或每一步各配一个 try/finally）比套一个大 `try` 可靠。子进程会继承输出管道，
  所以长循环里把每次运行的输出写到文件，不要用 `$(...)` 收集。用一个"让启动直接失败"的临时插件，可以确定地验证清理路径。
- **范围：** 平台（工作流测试 harness）。
- **来源：** wt-priceless-shamir-24594d 10-11 18:59–19:22；wt-heuristic-allen-f3088b 10-11 21:49–21:55；PROGRESS 两条 WP10 follow-up。
- **代码：** `apps/api/tests/temporal_harness.py`（`harness()`）；提交 4573b74。

### 9.5 在线工作流测试被"环境"骗了：继承了真实的 `TEMPORAL_ADDRESS`，以及 worker 计数不可信

- **现象：**
  - WP4c 的验收报告脚本 `workflow_eval.py --real-server`：32/33，`test_without_temporal_the_bridge_is_off_and_the_view_says_offline` 失败；真实服务器那一节
    "Exit code 1; MedRec documents after the injected failures: None"。
  - WP6 的在线 Playwright 第二次运行时，在线工作流用例全部跳过：它们的状态检查认为没有 worker。
- **怎么查的：**
  1. 离线测试失败：报告脚本在环境里设了 `TEMPORAL_ADDRESS` 去连 dev server，pytest 子进程原样继承，于是"没有 Temporal"的测试其实连上了 Temporal。
  2. 真实服务器演示单独直接运行是成功的（MedRec 第 3 次尝试成功，重启后继续）。**推测**报告里那次失败是因为 Temporal 还把几分钟前被杀掉的 Playwright worker 记成在轮询的 poller
     （Temporal 会把 poller 记几分钟）。资料里没有直接证据，修复后通过。
  3. WP6：手动按同样设置启动 API 看 `/api/workflows/status`，这次是 `"workers":1`。会话的判断是 worker 跑在 API 进程里（`TEMPORAL_WORKER_IN_API=1`，2 个活动线程）时，
     两个线程都忙，轮询就会断开，"在线 worker 数"掉到 0，和 WP4c 改用独立 worker 的原因一致。PROGRESS 把这句写成了事实，但转录里它是**推测**，没有进一步验证。
- **根因：** 测试的"模式开关"依赖进程环境和外部服务的状态，而这两者都会从别处漏进来（父进程的环境变量、服务器记住的旧 poller、忙碌的内置 worker）。
- **怎么修：** `conftest.py` 把 `TEMPORAL_ADDRESS` 固定为空；报告脚本不再把服务器地址传给 pytest，并捕获演示的 stderr；`workers()` 只数最近 30 秒内轮询过的 poller（`RECENT_POLL_S = 30`）；
  WP6 的在线 e2e 改用独立 worker 进程。
- **怎么证明：** 报告 33/33（包括离线用例和真实服务器演示：重启后 16.5 秒恢复、已完成步骤没有重跑）；WP6 用独立 worker 后在线 Playwright 29 过 1 跳过。
- **经验：** 测试要显式钉死所有"模式"相关的环境变量，不能依赖运行它的 shell（真实 key 的问题同理，见 10.6）。从外部服务读来的"在线状态"要带时间窗口，过期的心跳不算。
- **范围：** hospital（WP4c、WP6）。
- **来源：** main__025b79fc 10-10 00:35–00:40；wt-wp6-med-safety-570354 10-10 02:05–02:20；PROGRESS WP6 合并一节。
- **代码：** `apps/api/tests/conftest.py`、`apps/api/scripts/workflow_eval.py`、`apps/api/app/workflows/client.py`（`RECENT_POLL_S`）；提交 da35c54。

### 9.6 Temporal 探针工作流起不来：`RuntimeError: Failed decoding arguments`

- **现象：** WP8 把旅程里"文员批准后预约随访"的特权步骤换掉了，WP4c 原来借它测试"特权活动只跑一次、失败后等人重试或跳过"的规则，于是写了一个只用于测试的探针工作流
  `tests/workflow_probe.py`。测试超时：`AssertionError: timed out waiting for the failed call's Task`，worker 日志里反复出现 `RuntimeError: Failed decoding arguments`。
  这一轮（2 个测试，1 失败）跑了 374.53 秒。
- **怎么查的：**
  1. 假设：模块开头的 `from __future__ import annotations` 让类型注解变成字符串，而 Temporal 要读 `run` 方法参数的类型来解码输入。去掉这一行重跑。
  2. 换成新错误：`NameError: name 'Timers' is not defined`，仍然 `Failed decoding arguments`。输入类型是 `app/workflows/model.py` 里带
     `timers: Timers = field(default_factory=Timers)` 字段的 dataclass 一类。
  3. 不再追类型解析，改为让探针的 `run` 接收普通 `dict`（探针不需要定时器），恢复 `from __future__ import annotations`。
- **根因：** Temporal 根据 `@workflow.run` 方法的类型注解解码输入；探针模块里这个注解（及其引用的 `Timers`）在 Temporal 解析时无法解析。转录只记录了两个报错和最终修法，
  没有进一步分析解析失败的具体机制。
- **怎么修：** `async def run(self, inp: dict) -> dict:  # a plain dict input: the probe needs no timers`。
- **怎么证明：** `-k "privileged or replay or patch"` 10 个测试全过（含旧历史的重放和 patch 标记测试）。
- **经验：** Temporal 的工作流输入、活动参数是按类型注解序列化的，测试用的小工作流用最简单的输入类型（dict）最省事。一个超时型测试失败要 6 分钟时，先看 worker 日志里的
  第一条异常，而不是等测试超时。
- **范围：** hospital（WP8 改动 WP4c 的工作流测试）。
- **来源：** wt-wp8-voice-services-addfd5 10-10 07:22–07:35。
- **代码：** `apps/api/tests/workflow_probe.py`；提交 e7a2319。

### 9.7 测试里失败的 unit of work 不会回滚，和生产行为不一致（未修）

- **现象：** 不是哪个测试失败暴露的，是修 2.5 时读 `unit_of_work()` 和 harness 代码发现的。
- **怎么查的：** 转录里只有结论，看不到具体的阅读步骤（会话分析 teardown 时读过 `app/core/unit_of_work.py` 和 `store.py`）。
- **根因：** 测试里所有 unit of work 共用测试的那一个连接（ambient store）。ambient 分支出错时只是跳过 `save()`，失败活动留下的待写改动会被下一个 unit of work 的 `save()` 写进去，
  SQL 留在外层事务里。生产中这个活动的事务会被回滚。
- **怎么修：** 没有修，记为未完成项。建议每个 ambient unit of work 开一个 savepoint。
- **怎么证明：** 无，没有写复现测试。
- **经验：** 为了隔离和方便回滚而共享连接、共享事务的测试替身，可能悄悄改变"出错时"的语义。要专门核对出错路径在测试里和生产里是不是一样。
- **范围：** 平台（测试 harness）。
- **来源：** wt-heuristic-allen-f3088b 10-11 22:33；engineering-notes.md "Open optimizations"；PROGRESS WP10 follow-up。

### 9.8 其他小记

| 现象 | 根因 | 解法 | 来源 |
| --- | --- | --- | --- |
| Temporal 检查脚本报 `RuntimeError: Failed validating workflow Sleepy` | 工作流沙箱会重新 import 工作流所在的模块；草稿脚本没有 `if __name__ == "__main__":`，import 时又执行了 `asyncio.run(main())` | 加 `__main__` 保护 | main__025b79fc 10-09 21:52 |
| 消息类 mock 配置的延迟从来没生效过（立即返回），只有 OHIP、保险和外院影像库会等待 | — | 发现，未改 | PROGRESS WP2 |

---

## 十、端到端测试（Playwright）

端到端测试把前后端、后台循环和机器负载都串在一起，所以失败的原因最多样。这一章的共同做法：先看失败现场（快照 `error-context.md`、trace、服务器日志），
分清是"慢"还是"错"；断言等的是"真正表示完成的信号"（一个响应、一个可见的结果），而不是从点击开始倒计时；测试环境要默认安全（mock 模型、自己的端口和库）。
前端代码本身的 bug 在第十一章，负载下的计时失败在 7.2。

### 10.1 点完登录马上跳转：token 还没存好，页面又回到登录页

- **现象：** 出现过两次：
  1. 10-08 PostgreSQL 迁移后（`storyline.spec.ts`）：修好 11.1 后，storyline 在候补步骤失败：`getByTestId('candidate-PT-DEMO1')` 找不到；失败快照显示的是**登录页**。
  2. 10-10 WP10b 新写的 `ai-ops.spec.ts`：4 个用例里 3 个失败，例如 `locator.click: Test timeout of 60000ms exceeded`，在等 `getByRole('tab', { name: 'Eval Center' })`；
     另一个在等 `getByTestId('compare-head-a')`。
- **怎么查的：**
  1. 第一次：快照是登录页，说明跳转时还没登录。看 spec：`click("login-operations_manager")` 之后紧跟 `page.goto("/scheduling?tab=backfill")`。再用 grep 找所有"点登录后紧跟 goto"的地方：当时只有这一处。
  2. 第二次：读失败时的页面快照（`error-context.md`）：是 "Sign in as" 的登录页，而不是 AI Ops 页面。测试代码是 `await page.getByTestId("login-admin").click();`
     紧接着 `await page.goto("/ai-ops?tab=compare&...")`。会话的结论只有一句 "Race between login and goto"。
- **根因：** 测试里的竞态。点击登录后 token 要等登录请求返回才写进 localStorage；`goto` 是整页跳转，抢在前面，新页面没有 token，被送回登录页。
  内存后端登录很快，掩盖了这个竞态；数据库后端登录稍慢，就暴露了。
- **怎么修：** 点登录后先等一个"登录完成"的可见标志再跳转：storyline 等 `await expect(page.getByTestId("current-user")).toHaveText("Jordan Lee")`
  （注释 "token stored before navigating"）；ai-ops 等导航里的 "AI Ops" 链接出现。
- **怎么证明：** 第一次修复后全套 Playwright 19/19（3.8 分钟）；第二次 ai-ops 4/4（1.4 分钟），带截图的重跑也 4/4。
- **经验：** 后端变慢常常不是新 bug，而是把测试里早就存在的竞态暴露出来。点击之后要等一个能证明"操作已完成"的可见信号，再做下一步。测试卡在"找不到某个元素"时，
  先看快照里到底是哪个页面。同一个坑两天后在新 spec 里又出现了一次，可以考虑把正确写法做成共用的登录辅助函数（建议，资料里没有这样做）。
- **范围：** imaging（`storyline.spec.ts`）、平台（WP10b `ai-ops.spec.ts`）。
- **来源：** main__394ebe0a 10-08 00:02–00:07；wt-wp10b-ai-ops-060651 10-10 08:44–08:51；PROGRESS 阶段 0 "Phase 0 completion"。
- **代码：** `apps/web/e2e/storyline.spec.ts`、`apps/web/e2e/ai-ops.spec.ts`；提交 9458db0、03be4e4。

### 10.2 计时断言：等待窗口里算进了被测操作本身，后台循环在负载下超过 1 秒

- **现象：**
  1. 第一次跑控制塔规格："+30 min 后看板在 2 秒内显示新时间"这一步失败，看板一直是 `"Fri, Oct 9 07:00"`。
  2. 全量 Playwright 回归（机器忙）时，"运行中时钟每 2 秒内要变化"这一步失败，时钟停在 `"Fri, Oct 9 07:42"`。
- **怎么查的：** 第 1 次：2 秒是从点击开始算的，包含了 advance 请求本身的耗时，而这个请求在 CPU 紧张时就要好几秒。第 2 次：后台医院循环是"做完一轮再 sleep 1 秒"，
  负载高时一个周期超过 1 秒，于是界面在 2 秒内可能根本不变。（同一次运行里重置的 500 是另一个问题，见 2.10。）
- **根因：** 断言测的不是规格要求的东西。规格要的是"模拟器提交之后，看板在 2 秒内跟上"，测试却把请求耗时和循环漂移也算了进去。
- **怎么修：**
  1. 等 advance 的响应（`page.waitForResponse(... /simulator/advance ...)`）回来以后，再开始计 2 秒。
  2. 循环改为只睡剩下的时间：`await asyncio.sleep(max(0.1, 1 - (loop.time() - started)))`，负载下也大约每秒 tick 一次。
  3. 运行中的检查改为：从 `GET /api/hospital/simulator` 读模拟器自己的时间，用 `expect.poll` 等看板时间（换算成分钟数 `minutesOf`）在 2 秒内追上它。
- **怎么证明：** 第一处改完后，控制塔规格 3/3 通过。第二处改完后连跑 3 次，2 次全过，1 次失败，查出是另一个原因（2.11 的 Pause 409）；时钟这一步不再失败。
- **经验：** 写计时断言前先想清楚：计时从哪一刻开始，测的是哪一段。"做完再睡固定时长"的循环在负载下周期会变长；要固定周期，就减去这一轮已经花掉的时间。
- **范围：** hospital（WP5）。
- **来源：** main__dec77d7e 10-09 08:51、09:19–09:20；PROGRESS WP5 Simulator 一项。
- **代码：** `apps/web/e2e/control-tower.spec.ts`、`apps/api/app/main.py`（医院循环）；提交 d9ffd9f。

### 10.3 随访电话结束后 5 秒内看不到转写（只在全量 Playwright 里失败）：等响应，不要等固定时间

- **现象：** `feat/hospital-platform` 最终验证（95c8eef）的全量 Playwright 运行里，`voice-services.spec.ts` 的 "follow-up: the Chinese call escalates heavy wound drainage…" 失败：
  点"没有了"之后 5 秒内找不到 `followup-transcript`。单独跑能通过。
- **怎么查的：**
  1. 读 `FollowUpPage.tsx`、`api.ts` 和 `followup.py` 里结束通话的路径：最后一个回答在同一个请求里结束通话，服务器先跑 AI 分类器（一次模型调用），再写观察记录和通话记录，
     最后才回复；页面收到回复后重新拉取通话详情。
  2. 写脚本（time_followup.py，mock 模型）直接调 API 计时：sweep 17.16 秒，开始通话 0.28 秒，前两个回答 0.23 秒和 0.28 秒，最后一个回答"没有了" 1.84 秒（`ended=True`）。
     最后一步明显最慢，当时会话自己的星期扫描也在抢 CPU。
  3. 假设一：LLM 网关把调用串行化了，分类器排在重置后的后台 AI 工作后面。grep `Lock\(|Semaphore|BoundedSemaphore|rate_limit|max_concurrent|concurrency`，没有匹配，排除。
  4. 假设二：真有竞态。如果事务在响应发出之后才提交，页面重新拉取时可能读到仍是 `in_progress` 的通话，之后也不会再拉取。读 `app/core/unit_of_work.py` 的中间件：
     响应开始时就提交（状态码低于 500 时），也就是在响应体发出之前，所以重新拉取一定读到已结束的通话。排除竞态。
- **根因：** 页面没有 bug。测试把整段往返（分类器、工具调用、回复、重新拉取）都放进一个 5 秒的 expect 里，全量运行时机器负载高，往返超过了 5 秒。
- **怎么修：** spec 在点击前先用 `page.waitForResponse(...)` 等那次 `/answer` POST 的响应，断言 `ended` 为 true，然后再对转写做 5 秒检查（spec 只加了 4 行）。
- **怎么证明：** 全量 Playwright（周日数据）43 passed、2 skipped、1 failed，`voice-services.spec.ts` 和 `storyline.spec.ts` 都通过。唯一的失败是已知的负载敏感用例 inspection hub，
  单独重跑 1/1 通过。
- **经验：** 下"只是时序问题"的结论之前，先排除真正的竞态，弄清楚提交和响应谁先谁后。测试应该等真正表示完成的事件（这里是那次 POST 的响应），而不是用一个固定超时覆盖整段往返。
- **范围：** hospital（WP8 的 spec）。
- **来源：** wt-nifty-bose-77983d 10-12 00:17–00:55；PROGRESS 最后一节 "voice-services.spec.ts"。
- **代码：** `apps/web/e2e/voice-services.spec.ts`；提交 3c610c2。

### 10.4 `beforeAll` 超时：机器满载，加上生成器里 110 次全表扫描

- **现象：** WP8 的全量 Playwright：11 个通过（含 3 个 WP8 用例），9 个失败、17 个跳过，失败都是 `"beforeAll" hook timeout of 60000ms exceeded.`，而 `beforeAll` 里做的是演示数据重置。
- **怎么查的：**
  1. 先怀疑自己：WP8 往生成器加了 voice 一段，可能让重置变慢。写 time_seed.py 分段计时：整个 `build_store` 69.22 秒，其中 medsafety 1.11、documentation 3.34、voice 4.8 秒。
     voice 只占一小部分，但整体已经超过 60 秒。
  2. 看机器：`Win32_Processor.LoadPercentage` = 100；进程列表里有多个 Chrome、另一个会话的 Python 进程。
  3. 仍然先把自己那段优化掉：cProfile 显示 voice 段 7.4 秒里，`fhirstore.search` 被调用 110 次、累计 5.5 秒，其中 `_search_mem` 自身 3.5 秒：每个候选病人做一次 Consent 搜索，
     每次都全表扫描内存 store。
  4. 修后再计时：同样的挑选结果（逐个病人 id 对比），voice 段 4.8 → 3.2 秒。剩下的主要是 WP7 知识图谱加载（每进程一次，`graph.load` 1.445 秒）和按住院的搜索，判断"够了"。
     两次整体计时之间 `build_store` 从 69.22 秒变成 46.08 秒，而 voice 段只从 4.8 变到 4.44 秒，可见整体波动主要是负载。
  5. 通过 API 实测两次重置：55.96 秒、57.98 秒，CPU 100%，紧贴 60 秒上限。10 秒内按进程统计 CPU：pid 8156（兄弟会话 WP10b 的 API）占 6 秒，约 60% 个核。那是别的会话的进程，没有去动它。
  6. 把机器负载和真失败分开：先 `npx playwright test --last-failed` 重跑失败和跳过的用例，还有 2 个重置超时；再只对那 3 个文件用 `--timeout=180000`（命令行参数，不改文件）重跑，
     只剩 storyline 一个失败（就是 5.1）。
- **根因：** 两件事叠加：机器被其他会话占满，重置本来就要约 57 秒；WP8 新加的种子代码对每个候选病人做一次全表扫描的 Consent 搜索，又多加了几秒。
- **怎么修：** Consent 在每次生成时索引一次，不再按病人搜索。超时本身没有改，靠重跑区分负载。
- **怎么证明：** 同样的挑选结果，voice 段 4.8 → 3.2 秒；加长超时后除 storyline 和已知的周六失败（5.2）外全部通过。
- **经验：** 超时先拆时间（分段计时 + cProfile），再看机器负载。即使主要原因是负载，也顺手把自己引入的 O(n²) 消掉；用"重跑失败项"和"加长超时"两步，把"机器太忙"和"真的坏了"分开。
  不过要记得 5.1 的教训：剩下的那一个失败不一定也是负载。
- **范围：** hospital（WP8 种子数据）、平台（Playwright 全量运行）。
- **来源：** wt-wp8-voice-services-addfd5 10-10 09:11–09:45。
- **代码：** `apps/api/app/ehr/seed/voice.py`；提交 e7a2319。

### 10.5 未查明：长时间运行、反复重置的 API 上，全套 Playwright 越跑越慢，每次失败的 spec 都不一样

- **现象：**
  - WP10b 第一次全套 Playwright（19.4 分钟）：14 过、9 失败、15 没跑，多数失败是 `"beforeAll" hook timeout of 60000ms exceeded`（每个 spec 的 `beforeAll` 都调用 `/api/demo/reset`）。
    只重跑失败的 spec：21 过、3 失败。重启 API 后再跑一次全套：32 过、4 失败，而且**失败的 spec 和上一次不一样**（break-glass、inventory、med-safety 联署，上一轮重跑时这几个是通过的；
    另一个是周六的 radiology-ops）。
  - 另一个会话的全套（API 由 Playwright 自己启动，`--timeout=180000`）：先是 control-tower 的病人卡片 5 秒内没出来，接着 `"beforeAll" hook timeout of 180000ms exceeded`，
    然后 documentation spec 连 240 秒的钩子超时都超了。
- **怎么查的：**
  1. WP10b 先按"机器忙"处理：后台 pytest 在跑，演示重置超过 60 秒，所以把 ai-ops spec 的 `beforeAll` 加到 240 秒，失败的 spec 等机器空了再跑。
  2. 重跑后 `agent-runtime.spec.ts` 仍然失败：点 "Triage with AI" 后 5 秒内没有结果卡片，页面快照显示按钮仍是 disabled（请求还在路上）。**单独**重跑这个 spec，在同一个 API 上还是失败（1 分 28 秒）。
  3. 绕过浏览器直接 curl 同一个接口：第一次 3.9 秒，再两次 1.7 秒、3.7 秒，都在 5 秒以内但很接近。
  4. 怀疑自己新加的"发布状态叠加层"让注册表变慢，就去测量：`registry.agent()` 每次 0.064 ms，`registry.agents()` 每次 1.86 ms；AI Ops 总览聚合冷启动 0.69 秒、热 0.04 秒；
     `registry.agents()` 只在两个路由里用，不在热路径上。排除。
  5. 杀掉这个已经跑了约 50 分钟、经历过多轮演示重置的 API，用 `PYTHONUNBUFFERED=1` 重新启动，同一个 spec 2/2 通过。在刚重启的 API 上跑最后一轮全套，跑到后半程又出现 3 个"新"失败
     （break-glass 的重置钩子超过 60 秒、inventory 的 "completed" 5 秒内没出现、med-safety 联署状态 5 秒内没变）。再重启一次 API，只跑这 3 个 spec：12/12 通过。
     也就是说，一个新 API 在一整套运行的前半程没问题，后半程开始出错。
  6. 另一个会话在钩子超过 180 秒时查进程：CPU 负载 100%，API 的 python 进程（PID 21716，uvicorn 9441）工作集 **2,428 MB**、CPU 时间 317.9 秒；10 分钟前第三个会话用 `tasklist`
     看到同一个 PID 是 1,208,072 K，十分钟里涨了一倍。它的假设：Playwright 钩子超时只是放弃等待，服务器端的重置请求还在跑；下一个 spec 又发一个重置，排在前一个后面，越积越多。
     这个假设**没有被验证**。它停掉整套运行，换一个新 API 重跑：40 过、2 跳过（Temporal 没开）、0 失败，13.2 分钟。
- **根因：** **未查明。** 能确定的只有：API 进程内存在一次全套运行里增长到 2.4 GB；重置越来越慢；换新进程后同样的 spec 通过。注意两轮之间不止一个变量变了：第二个会话第一轮的 API
  也是 Playwright 新启动的，第二轮开始时另一个会话的后端全套已经结束，机器负载也降了；WP10b 最后确认时只重跑了 3 个 spec，前面没有几十次重置。所以"进程跑久了、重置多了"
  和"机器负载"两个因素没有分开。
- **怎么修：** 没有代码修复，只有操作上的做法：跑全套前重启 API（或让 Playwright 自己启一个新的）；重置钩子给足超时（ai-ops spec 里 `test.setTimeout(240_000)`，命令行
  `--timeout=180000`）；全套失败的 spec 在新 API 上单独重跑来确认。
- **怎么证明：** 只有"新 API 上通过"这一类证据：agent-runtime 2/2、三个 spec 12/12、另一个会话全套 40/40（除跳过）。
- **经验：** 失败在不同的 spec 之间"轮流"出现，说明问题在它们**共享的东西**（同一个服务器进程、同一台机器），而不在某个 spec 本身。重启服务器是一个便宜的对照实验，
  但它一次改了好几个变量；想找到真正原因，要记下进程内存、重置耗时这些数字，并一次只改一个条件。
- **范围：** 平台（所有 Playwright spec）。
- **来源：** wt-wp10b-ai-ops-060651 10-10 09:13–10:07；wt-cool-cartwright-7673d9 10-10 16:07–16:42；wt-adoring-raman-2c26f4 10-10 16:12、16:25；PROGRESS WP10b 结果、WP10 Results。

### 10.6 测试和本地容器悄悄用了真实的 Gemini key

- **现象：**
  - 本地 `docker compose up --build` 验证时，`/api/health` 返回 `"llm_mode":"gemini"`：容器通过 `env_file` 读了开发用的 `apps/api/.env`（`LLM_PROVIDER=gemini`）。
  - WP2 的 Playwright 回归：日志里出现 `POST https://aiplatform.googleapis.com/...gemini-3.5-flash:generateContent "HTTP/1.1 504 Gateway Timeout"`，`storyline.spec.ts` 的
    `"beforeAll" hook timeout of 60000ms exceeded`，19 个里 1 个通过、11 个没跑，其余失败，大多数 spec 超时。
- **怎么查的：** 第一次：立刻停掉 api 和 web 容器（防止后台 worker 继续调用），查 `llm_calls`：`mock|14`，没有产生真实调用。第二次：`grep` 日志只看到 1 次 504
  （因为命令用了 `tail`，日志只剩最后 30 行）；想查 `llm_calls` 统计实际调用次数，结果是 0，因为测试会重置演示数据，把调用记录也清掉了，**无法确认实际花了多少次调用**。
- **根因：** Playwright 的 `webServer` 用 `uv run uvicorn` 起 API，API 读 `apps/api/.env`；开发者的 `.env` 选的是 Gemini 和真实 key。测试默认期望 mock 模型，但没有任何地方强制 mock。
- **怎么修：** 重跑时显式加 `LLM_PROVIDER=mock`，并在日志里检查 `aiplatform` 出现次数为 0；之后的会话简报和 PROGRESS 的环境说明都写明要设这个变量。
  **没有结构性修复**：`apps/web/playwright.config.ts` 仍然只读 `PW_API_PORT` 等，不会强制 mock。
- **怎么证明：** mock 重跑 `gemini calls: 0`，18/19 通过（剩下 1 个是 5.5 的中文问卷问题，当时一度被误以为是这次真实调用留下的状态）。
- **经验：** 测试环境要"默认安全"：真实 key 只有显式要求时才用，比如测试启动的服务器强制 `LLM_PROVIDER=mock`，需要真实模型时再显式打开；否则测试会变慢、变得不确定，还会花钱（3.7）。
  要统计花了多少次调用，别依赖会被测试重置清空的表，保留完整日志。
- **范围：** 平台（影响全部 spec）。
- **来源：** main__394ebe0a 10-08 00:09–00:10；main__4f2eefc4 10-08 20:19–20:29；PROGRESS "Environment notes"。

### 10.7 负载下写一个 75 分钟的长 e2e：一串坑

- **现象与原因（逐条）：** WP9 的 `hospital-storyline.spec.ts` 要在一个测试里走完 12 步故事线，离线跑了十几轮才稳定：
  - **5 秒默认超时太短**：负载下状态接口慢，`story-step-5` 在 5 秒内还没渲染（快照是 "status: Loading…"）；第 6 步 "Ask the prescriber to change it" 按钮 5 秒后还在 loading。
    改为先等步骤列表；全局 `expect.configure({ timeout: 30_000 })`，后来再到 60 秒；整个测试 40 分钟 → 75 分钟。
  - **`isVisible()` 不会等待**：第 9 步 "Draft with AI" 按钮还没渲染，`if (await draft.isVisible())` 直接跳过，后面找不到 `summary-review`。改为等"草稿按钮或审阅区"其中之一出现。
  - **页面只解析一次关联记录**：第 6 步 MedRec 面板这次没出现（run 8 却过了），因为页面加载时入院核对记录还没生成，工作台只解析一次。改为先通过 API 等记录存在、并等它的 AI 步骤完成
    （和后端走查一样）。另外工作台要 `?encounter=…&document=…` 才会打开 MedRec 记录。
  - **会消失的输入框**：第 11 步随访电话说完最后一句"没有了"后通话结束，回答框消失，循环里的 `fill` 一直等下去。改为等"回答框或已结束通话的转写"其中之一。
  - **否定断言空过**：角色对比（文员队列里没有分诊卡）在队列加载之前就"通过"了。改为先等队列加载。
  - **测试自己的假设错了**：期望 "You are" 显示当前步骤的角色，但第 6 步最后联署的是外科医生，改为检查它和页眉的登录人一致；Temporal 模式下第 8 步的卡片是旅程自己的 Task，
    没有 AI 徽章，spec 改为接受旅程的 Task。
  - **误以为有另一个 Playwright 在跑**：快照显示已登录为第 5 步的 Jordan Lee，又看到 `story-05-approved.png`，以为"我的运行都没到第 5 步"。查 node 进程：没有别的；看截图时间戳：
    20:39:04、20:39:15、20:40:57，都属于本次运行。原来步骤号只在一步完成后才打印，第 5 步其实已经批准，是在第二次推进时失败的。
- **经验：** `isVisible()` 是瞬时检查，不能当等待用；"A 或 B"两种结果都要能等到。做否定断言前先确认页面加载完了。怀疑有外部进程干扰之前，先看进程列表和文件时间戳；
  先弄清自己的日志在什么时刻打印。负载下的超时要放宽，但每次失败先看快照，分清是"慢"还是"错"：这一串里一半是测试或页面的真问题（另见 11.2、12.8、12.9）。
- **范围：** hospital（WP9）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 00:41–02:03、04:07。
- **代码：** `apps/web/e2e/hospital-storyline.spec.ts`；提交 8d3cd5e。

### 10.8 Playwright 小坑

| 现象 | 根因 | 解法 | 来源 |
| --- | --- | --- | --- |
| `strict mode violation: ... getByRole('button', { name: 'Justified' }) resolved to 2 elements` | `getByRole` 的 `name` 默认是不区分大小写的子串匹配，"Justified" 也匹配 "Not justified" | 加 `exact: true` | main__40bdfa74 10-09 00:20–00:22 |
| 点按钮一直重试：`<div class="fixed inset-0 z-30 ... bg-black/50 ...">… subtree intercepts pointer events` | 床位网格的弹层还开着，就去切换角色，点击被弹层挡住 | 切换角色之前先关掉床位网格。"intercepts pointer events" 说明上面还盖着一层 | main__dec77d7e 10-09 08:50–08:52 |
| `npx playwright test e2e/storyline.spec.ts` 把 `hospital-storyline.spec.ts` 也跑了 | Playwright 的文件过滤是子串（正则）匹配 | 用 `'(?<!hospital-)storyline\.spec'` 这样的正则 | wt-nifty-bose-77983d 10-12 01:47、02:14 |
| Temporal 测试 `timed out waiting for the critical-value card` | 卡片的 kind 写成了 `critical_value`，实际是 `critical` | 改正 kind | wt-wp9-storyline-7d11cd 10-11 00:40 |
| Control Tower 用例期待 "Task task-"，却看到 "queued for the workflow" | 开了 Temporal 后，批准由容量工作流异步执行 | 测试给出执行的时间（2.8） | main__025b79fc 10-09 23:45 |
| Playwright 失败后查库，看到的状态和失败时不一样 | 失败后 Playwright 会重启 worker 并重跑 `beforeAll`（演示重置） | 现场以快照、trace 为准（5.1） | wt-wp8-voice-services-addfd5 10-10 |

---

## 十一、前端与多语言界面

前端的 bug 常常以"端到端测试等不到结果"的样子出现，看起来像后端慢。先确认响应有没有到（trace 的网络记录），再看页面有没有报错（`pageerror`、console），
最后才去后端找原因。

### 11.1 电话 Agent 第一句回复后组件崩溃：`useEffect` 把 `scrollIntoView` 的返回值当成清理函数

- **现象：** 后端换成 PostgreSQL 后跑 Playwright，19 个里 18 个过。`storyline.spec.ts` 在电话步骤失败：
  `expect(getByTestId('transcript')).toContainText(/you're verified/)` 5 秒超时，页面上的通话记录停在 "…Agent is responding…"。看起来像是"数据库让电话回合变慢了"。
- **怎么查的（逐层排除，每一步只换一个条件）：**
  1. 假设"Postgres 让回合变慢"。写脚本直接调 `/api/frontdesk/calls` 和 `/turn`：回合 0.87 秒 / 0.03 秒，再跑一次 0.26 秒 / 0.02 秒。不慢。
  2. 假设"跑测试时后台 worker 争用"。单独重跑这个 spec：仍然失败，能稳定复现。
  3. 自己在 9001 端口起 API（后台 worker 开着）再跑 spec，同时循环采样 `pg_stat_activity`：没有锁等待；API 日志里 `/turn` 返回 `200 OK`。
  4. 给 API 套一个计时包装，记录服务器端每个请求的耗时：`/turn` 1.07 秒。再查有没有阻塞事件循环的 `async def` 端点：只有一个上传接口，排除。
  5. 用 httpx 客户端调同样的请求：0.3 秒。问题只在浏览器路径上。
  6. 录 Playwright trace（`--trace on`），从 trace.zip 的 `*.network` 里取出这次请求：`200`，1,424 ms，响应体完整且正确（"Thank you, Robert, you're verified…"）。
     结论：**响应到了，但 React 没渲染**。
  7. 假设"浏览器真实的语音合成在渲染前出了问题"。写临时 spec，分别把 `window.speechSynthesis` stub 掉和不 stub，超时放到 30 秒：两种都失败，排除。再读前端的 `post` 封装
     （`src/lib/api.ts`），没有异常。
  8. 临时 spec 监听 `console` 和 `pageerror`：出现 React 警告 "useEffect must not return anything besides a function, which is used for clean-up"，接着
     `PAGEERROR destroy is not a function`、"The above error occurred in the <PhoneAgent> component"。
- **根因：** `PhoneAgent.tsx` 第 47 行
  `useEffect(() => bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), [session?.transcript.length]);`
  是表达式体箭头函数，返回值就是 `scrollIntoView(...)` 的返回值。本机较新的 Chromium 在这里返回 Promise，React 把它当清理函数调用，报 `destroy is not a function`，组件崩溃，
  这次 mutation 永远不会结束。和数据库无关（PROGRESS 也特别写明"不是数据库引起的"），只是这次回归第一次用这个 Chromium 跑。
- **怎么修：** 改成块体 `useEffect(() => { bottom.current?.scrollIntoView(...); }, [...])`，注释写明原因。用正则 `useEffect\(\(\) => [^{(]` 搜同类写法：另外 3 处（ContrastPage、
  BacklogPage、DosePage）返回的是 `setState(...)`，永远是 `undefined`，不用改。
- **怎么证明：** `npx tsc -b` 通过；重跑全套 Playwright，电话步骤通过（暴露出下一个问题，见 10.1）；修完 10.1 后 6 个 spec 的 19 个测试全部通过（本地 Windows，API 9001，mock）。
- **经验：** 前端"等不到结果"时，先确认响应有没有到（trace 里的网络记录），再看页面有没有报错（`pageerror`、console）。这次花时间最多的是在后端一层层排除，其实第 8 步的监听应该更早做。
  React 的 `useEffect` 只能返回清理函数或什么都不返回；用 `() => expr` 写 effect 会把 `expr` 的结果泄漏出去，今天是 `undefined`，浏览器升级后可能就不是了。
- **范围：** imaging（系统 4 前台电话）。
- **来源：** main__394ebe0a 10-07 23:46–23:58；PROGRESS 阶段 0 "Phase 0 completion"。
- **代码：** `apps/web/src/features/frontdesk/PhoneAgent.tsx`；提交 9458db0。

### 11.2 快速切换页面时请求被中断，前端把登录态清掉了

- **现象：** WP9 离线 e2e 第 1 步通过，第 2 步打开 /storyline 时 `getByTestId('storyline')` 找不到；失败快照显示的是登录页（"Sign in as"）。也就是一键切换角色（快速跳页）后，用户被登出。
- **怎么查的：**
  1. 先怀疑后端回了 401（前端 `api()` 收到 401 会清 token）。查 API 访问日志：所有 `/api/auth/me` 都是 200，没有 401、403 或 500。一度以为 stdout 被缓冲、日志不全
     （不加 `PYTHONUNBUFFERED=1` 时访问日志确实会被缓冲），确认日志完整（74 行）后排除了后端。
  2. 转向前端：路由守卫 `RequireAuth`、"Switch role" 按钮里的 `logout()`、所有 `login(` 调用。
  3. 看 `AuthProvider`：`api("/api/auth/me").then(setUser).catch(() => setToken(null))`。任何失败都会清 token，包括被页面跳转中断（abort）的请求。spec 切换角色后立刻跳转，
     `/api/auth/me` 还没返回就被取消了。
- **根因：** 错误处理太宽，把"请求被中断"当成了"会话无效"。真正的 401 已经由 `api()` 处理，provider 里多出来的那次清除只多覆盖了 abort 这种不该清的情况。
- **怎么修：** `.catch(() => undefined)`，只有 `api()` 里的真 401 才丢 token。同时让 spec 的角色对比检查先等队列加载完，否则否定断言会"空过"（10.7）。
- **怎么证明：** 重跑后 1–4 步通过 UI；最终离线 e2e 12 步全过，`hospital-storyline.spec.ts` 每一步切换角色并检查 "You are" 和登录的人一致。
- **经验：** 在 catch 里做破坏性动作（登出、删数据）之前，先判断错误类型：401（凭证无效）、网络中止、5xx 应该有不同的后果。真实用户快速点击也会触发这个 bug，自动化测试只是把它放大了。
  服务端日志干净时，就该转向客户端找原因。和 10.1 的区别：10.1 是测试没等登录完成；这里是登录已经完成，前端自己把它清掉了。
- **范围：** 平台（Web 登录）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 00:30–00:33；PROGRESS WP9 "Web fix"。
- **代码：** `apps/web/src/lib/auth.tsx`；提交 8d3cd5e（"the web dropped the session when /api/auth/me was aborted"）。

### 11.3 刚提交的同行评审从列表里消失

- **现象：** 提交说明写的是修复后的行为："A reviewer's list shows open reviews first, then completed ones newest first, so a just-submitted review stays visible."
  也就是修复前，评审人刚提交的评审会从自己的列表里消失。
- **怎么查的：** 资料没有记排查过程。从 diff 看机制：原来按 `(status == "completed", assigned_at)` 排序，已完成的评审按分配时间**从旧到新**排在后面，再截取 `mine[:60]`；
  种子里有两周的评审历史，最新完成的那条排在最后，被截掉了。
- **根因：** 排序方向加上截断。
- **怎么修：** 未完成的按分配时间从旧到新，已完成的按完成时间从新到旧。同一提交还把重新分配建议的上限从 40 提到 60（"covers a full off-shift queue"），
  并给没有站点的上传检查加了 "Uploaded (no site)" 标签。
- **怎么证明：** 资料里没有针对这个排序的专门测试；阶段 3 的 Playwright `radiology-ops.spec.ts` 覆盖盲审流程（提交 02db3c8）。
- **经验：** 列表"排序 + 截前 N 条"时，要检查用户刚操作的那一条会不会被截掉；种子数据量大时这种问题才出现。
- **范围：** imaging（系统 13 同行评审，阶段 3）。
- **来源：** 提交 7392bdc 的说明和 diff（与 2.7 同一个提交）。
- **代码：** `apps/api/app/modules/peer_review/router.py`；提交 7392bdc。

### 11.4 中文患者指导里混入英文诊断名

- **现象：** 两件事。一是第一次评测虽然过了门槛，但报告里中文文档"每个术语首次出现都有解释"只有 37%；二是单元测试
  `test_instructions_copy_the_facts_from_the_summary_and_render_three_languages_alike` 失败在 `assert renderings["zh-Hans"]["readability"]["no_abbreviations"]`。
- **怎么查的：** 直接渲染那份指导并打印可读性检查：`{'abbreviations': ['Hip', 'fracture'], 'no_abbreviations': False, ..., 'unexplained_terms': ['condition:hip fracture']}`，
  正文第一句是 `您因Hip fracture住院。`。
- **根因：** 没有编码、只有名字的诊断查不到中文术语，就原样输出英文名；另外很多病情、操作和药物类别没有审核过的通俗解释。
- **怎么修：** 对没有编码的术语先按名字查编码再取译名和解释；补齐病情、操作、药物类别的通俗解释。
- **怎么证明：** 测试 18/18 通过；评测报告：中文无缩写 100%、每个术语首次出现有解释 100%（其中来自知识图谱的 17%），患者指导可读性代理分 4.37 → 5.0。
- **经验：** 双语渲染要有"没有译文时"的显式检查（这里是"中文里不该出现拉丁字母缩写"），否则缺译文会静默退化成英文。监控指标（不是门槛）也值得看一眼，37% 这种数字说明功能虽然
  "过了"，用户体验却不行。
- **范围：** hospital（WP7 双语患者指导）。
- **来源：** wt-wp7-documentation-c03e03 10-10 04:05–04:10。
- **代码：** `apps/api/app/modules/documentation/`；提交 a04ca7b。

---

## 十二、业务逻辑与设计缺口

这一章的问题都不会报错：代码按写的那样运行，只是写的东西和业务要求不一致。它们多半是在"把故事从头到尾走一遍"时发现的：写演示脚本、
WP9 开工前的只读审计、读端到端截图、核对 README 里的一句话。

### 12.1 MedRec 可以绕过药师工作台直接签成终稿

- **现象：** WP6 的工作台要求药师对每一条差异做决定（接受 / 修改 / 拒绝并给理由）再签名、医生再联署。但病人病历页的通用 Sign 按钮
  （`POST /api/hospital/documents/{id}/sign`）、直接调用签字服务、AI 代理被批准的 `signDocumentFinal` 都能把 MedRec 文档签成终稿，这样签不会记录逐条决定。
- **怎么查的：** 把 WP6 接进旅程时搜通用签名入口发现的：`grep "sign_document\|/sign\""` 找出所有签名入口（后端 `platform_router.py:240`，前端 `PatientChartPage.tsx:166`）。
  WP6 会话当时只把旅程的在线 e2e 改成走工作台 API，并把这个缺口写进 PROGRESS 的已知限制和交接说明，由单独的会话修。
- **根因：** WP4 的签名服务只校验"谁能签"（角色、联署顺序），不校验"在哪个流程里签"。对 MedRec 来说，工作流程本身（逐条决定）就是安全要求的一部分。
- **怎么修：** 签名服务新增 `signing_flow(doc)`：类型为 `medrec` 或属于 `medication_reconciliation` 模块的文档，只有调用方传 `flow=MEDREC_WORKBENCH`（只有 WP6 的 `service.sign` 会传）
  才能签。病历页的签名接口、直接调用 `sign_document`、AI 代理被批准的 `signDocumentFinal` 请求一律 403，并写一条结果为 denied 的 `sign` 审计。病历页对 MedRec 不再显示 Sign，
  改为 "Sign in Medication safety" 链接，直接打开工作台里对应的核对。
- **怎么证明：** 新测试 `test_a_medrec_record_is_signed_only_in_the_workbench`：即使每条差异都处理完，三条旁路都被拒，工作台仍能签和联署，终稿带着每条决定；
  后端 561 通过，Playwright 29 过 2 跳过（需要 Temporal 的在线用例），Playwright 也检查病历页给的是链接。
- **经验：** 做"必须经过某个流程"的安全要求时，要把所有能达到同一终态的入口都列出来（grep 签名、状态变更的接口），在最底层的服务里拦，而不是只在 UI 上不给按钮。
  "谁能签"之外还要问"从哪条路径签"。加守卫后要提醒并行分支：任何直接签 MedRec 的测试或脚本都会被拒（交接说明里专门提醒了 WP7）。
- **范围：** hospital（WP4 签名服务 + WP6）。
- **来源：** wt-wp6-med-safety-570354 10-10 01:10（发现）；wt-youthful-mclean-6b21df 10-10 02:41–03:24（修复）；PROGRESS WP6 "Follow-up, MedRec records signed only in the workbench"。
- **代码：** `apps/api/app/ehr/signing.py`（`signing_flow`）、`apps/api/app/ehr/platform_router.py`、`apps/web/src/features/hospital/PatientChartPage.tsx`；提交 0d6814f。

### 12.2 一条超出护士 72 小时权限的记录，让整个随访队列打不开

- **现象：** 没有测试失败。WP8 会话在写 README、核对一句说明时意识到：队列通过**护士本人**的 FhirGateway 读病人，而 WP4 给护士的病区范围在出院 72 小时后结束。
  那么出院超过 72 小时的随访条目会怎样？
- **怎么查的：** 写探针脚本 `old_queue.py`：在会回滚的事务里把一条随访的出院时间改到 72 小时以前，再以护士身份取队列。结果整个列表抛出
  `BreakGlassRequired This patient is outside your units; emergency access (break-glass) is required`。
- **根因：** 列表接口逐条用用户自己的网关读病人详情，任何一条被拒绝（`BreakGlassRequired` 是 `FhirAccessDenied` 的子类），异常就冒到整个列表，一条记录拖垮整页。
  随访电话在出院后 48 小时打，加上等待和升级，条目很容易活过 72 小时窗口。
- **怎么修：** 超出范围的条目仍留在队列里，保留优先级等字段，但不带病人详情（`restricted: True`，`mrn`、`patient_name` 为空，`_restricted()`），打开它需要 break-glass；界面对这类条目显示原因。
- **怎么证明：** 新回归测试（`test_an_item_outside_the_nurses_72_hour_scope_s...`）；受影响的三个测试文件 55 个测试全过。
- **经验：** 列表接口里"逐条做权限检查"时，要决定一条失败怎么办：跳过、降级显示，还是整页报错。通常不应该整页报错（和 3.9"单条失败不影响整体"同一个道理）。
  时间窗口类的权限（72 小时）要和业务流程的时长（48 小时后打电话、升级、等护士处理）放在一起想，看会不会有条目跨过窗口。用一个会回滚的探针脚本"把时间拨过去"，是验证这种猜想的便宜办法。
- **范围：** hospital（WP8 护士随访队列，涉及 WP4 的访问范围）。
- **来源：** wt-wp8-voice-services-addfd5 10-10 08:45–08:54；PROGRESS WP8。
- **代码：** `apps/api/app/modules/voice_services/queue.py`（`_restricted`）；提交 e7a2319（"items outside the nurse's 72-hour scope stay listed without patient details"）。

### 12.3 呼叫中心把来电者的亲属关系存反了

- **现象：** 来电者说 "my mother"，`caller_relation` 存成 `mother`，通话摘要写成 "Call from the patient's mother"，而实际来电的是病人的孩子。呼叫中心页面显示
  "Caller: the patient's {caller_relation}"，同样读反。
- **怎么查的：** WP9 开工前对 12 个步骤做只读审计（三个并行的审计子任务），把每一步映射到现有代码并列出缺口，这条在第 1 步的缺口里。WP8 原有的测试断言
  `call["caller_relation"] == "mother"`，也就是把错误行为写成了期望值，所以测试一直是绿的。
- **根因：** 正则抓到的是"病人是来电者的谁"，字段的语义却是"来电者是病人的谁"，方向反了。
- **怎么修：** 加 `CALLER_IS` 映射（mother/father → child，husband/wife → spouse 等）；"I'm her daughter"、"我是她女儿"这类自述按字面记录（`SELF_RELATION`、`SELF_ZH`）。
  `tests/test_voice_services.py` 改为期望 `child`。
- **怎么证明：** 测试改为按需求写的期望值后通过；修这个 bug 时又引入了 15.1 的退格符问题（英文自述匹配不上），也一并修好。
- **经验：** 关系类字段要写清方向（A 是 B 的什么）。测试的期望值如果是照着当时的输出抄的，就会把 bug 固化下来，期望值要从需求推出来。修 bug 的代码同样需要每种语言的测试。
- **范围：** hospital（WP8 呼叫中心，WP9 修）。
- **来源：** wt-wp9-storyline-7d11cd 10-10 20:43–21:03（审计）、22:05（修）；PROGRESS WP9 审计表和 "WP8 fix"。
- **代码：** `apps/api/app/modules/voice_services/identity.py`（`CALLER_IS`）、`apps/api/tests/test_voice_services.py`；提交 8d3cd5e。

### 12.4 "The second one" 订成了第 1 个时段：关键字规则的顺序

- **现象：** WP8 写演示脚本 `scripts/voice_demo.py` 时，来电者改约说 "The second one"，系统确认的却是选项 1。单元测试
  `test_reschedule_proposes_a_new_time_and_the_clerk_confirms_it_in_one_click` 一直是通过的。
- **怎么查的：** 看 `intents.ordinal()`：它按 `ORDINALS` 列表顺序返回**第一个匹配**的序号，而选项 0 的正则里有 `one`、`number one`……"The second one" 里的 "one" 先命中了选项 0。
  单元测试虽然也说了 "The second one"，但只断言回复里列出了选项，**没有断言最后确认的是哪个时段**，所以没抓到。
- **根因：** 多个规则可能同时命中时，按"谁先列出"取第一个；裸数字词（one/two/three）比序数词（first/second/third）更泛，却排在前面。
- **怎么修：** 先匹配序数词（first/1st/第一/头一个……），再匹配裸数字（one/number one/一号/`^\s*1\s*$`……）。单元测试加上"确认的是哪个时段"的断言。
- **怎么证明：** 一组输入逐个打印 `ordinal()`：`The second one 1`、`the first one 0`、`one 0`、`2 1`、`option three 2`、`第二个 1`、`我要第一个 0`、`the third one please 2`、`two 1`，
  全部正确；演示脚本重跑通过。（修这个 bug 的过程中又踩了 15.1 的坑：第一次改完所有英文输入都返回 `None`。）
- **经验：** 规则表"取第一个命中"时，要把更具体的规则排在更泛的前面，并为"同时命中两条规则"的句子写测试。测试要断言**结果**（订了哪个时段），而不只是断言中间回复里出现了某些字。
  端到端跑一遍演示脚本能抓到单元测试漏掉的东西。同类问题：写 40 条呼叫中心评测脚本时也发现过几处规则顺序问题（"改期"要先于"取消"判断、预约词要先于 FAQ），以及中间夹词的
  "nosebleed that won't stop" 没被红旗规则抓到，都在写评测用例时改掉；同批还修了中文回复用空格连接句子的问题。
- **范围：** hospital（WP8 呼叫中心）。
- **来源：** wt-wp8-voice-services-addfd5 10-10 07:45–07:46、08:21–08:22；PROGRESS WP8 "Found while writing the demo and fixed"。
- **代码：** `apps/api/app/modules/voice_services/intents.py`（`ordinal`）、`apps/api/scripts/voice_demo.py`；提交 e7a2319。

### 12.5 "截至入院时刻"的医嘱永远是 0 条：截止时间选错了

- **现象：** 检查端到端截图时发现，工作流时间线上医嘱审核步骤的占位文字写着 "0 admission medication orders"。
- **怎么查的：**
  1. 怀疑"截至入院时刻的医嘱"这个筛选把医嘱都排除了。直接查一个住院（stay-01086）：入院时间 2026-10-05 23:39，5 条用药医嘱的 `authoredOn` 在次日 00:47 到 03:35 之间。
  2. 扩大到 40 个住院统计：40 个都有医嘱，但**没有一个**有在入院时刻或之前开的医嘱（`Counter({(True, False): 40})`）。
- **根因：** 生成器（和现实一样）在入院后 1–4 小时才写医嘱，用"入院时刻"做截止时间，筛出来永远是空的。
- **怎么修：** 截止时间改为取自工作流自己的历史（确定、可重放）：医嘱审核用旅程加载上下文时的医院时间（`as_of`），入院用药核对用药师签署事件的时间。
- **怎么证明：** 相关工作流和回放测试 17 个通过（重放不检查活动输入），之后用最终代码重新录制了全部历史。
- **经验：** 时间截止点要对着真实的业务时序验证一次（"医嘱是什么时候写的"），而不是凭直觉选"入院时刻"。全量数据上一条都筛不出来，是最强的信号；截图里一个"0"就值得追。
- **范围：** hospital（WP4c 旅程 + WP6 接口）。
- **来源：** main__025b79fc 10-10 00:03–00:05。
- **代码：** 提交 da35c54。

### 12.6 指南检索：病人背景里的词压过了医嘱本身的词

- **现象：** 首次端到端冒烟（intake → AI 点评 → 核对 → 签名）时，"萘普生 + 华法林"的相互作用审核检索到的第一条指南是 `cardiovascular#rate-control-continuity`（心率控制），
  而不是 `anticoagulation#nsaids-antiplatelets`。
- **怎么查的：** 单独调用 `knowledge.search()` 对比：只用医嘱相关的词（naproxen、nsaid、interaction、warfarin）检索，第一条就是正确的抗凝/NSAID 段落；把病人背景（其他用药和诊断，
  如 amlodipine、"Atrial fibr…"、"Fracture of neck of femur"）混进同一个词表后，背景词把心血管、骨折镇痛段落顶到了前面。
- **根因：** 检索时医嘱本身的词和病人背景的词放在一起、权重相同，背景词多，就主导了排序。
- **怎么修：** `search(terms, *, context=...)` 把两者分开，`terms`（医嘱的药物、类别、规则发现）的权重是 `context`（病人的其他用药和诊断）的 3 倍。
- **怎么证明：** 四组查询（相互作用、过敏、肾功能剂量、重复检验）的第一条分别是 `anticoagulation#nsaids-antiplatelets`、`drug_allergy#beta-lactam`、`renal_dosing#nsaids-ckd`、
  `lab_stewardship#repeat-tests`。
- **经验：** 给 AI 检索参考资料时，"问题本身"和"背景"要分开加权；检索结果要看一眼排第一的是什么，而不只是看有没有返回。
- **范围：** hospital（WP6）。
- **来源：** wt-wp6-med-safety-570354 10-09 22:50–22:51。
- **代码：** `apps/api/app/modules/med_safety/knowledge.py`（`search`）；提交 3d23650。

### 12.7 髋部骨折的检查申请被配成头颅 CT 或胸片协议

- **现象：** WP9 故事线要让急诊的髋部骨折检查走影像中心的流水线。开工前的审计发现：协议库里没有髋部协议，髋部申请拿到的是头颅 CT 或胸片协议；mock 分诊给髋部骨折 P3。
- **怎么查的：** WP9 的只读审计逐步对照"故事每一步需要什么、模块现在有什么"。关键词检索在没有合适协议时仍然会返回"最接近"的那个。
- **根因：** 协议库只覆盖影像中心常见检查（README：库里 22 个协议，库外的检查会得到最接近的一个，需要放射科医生改）。
- **怎么修：** 加两个医院协议 `XR-HIP-2V`、`CT-HIP-FX`；代码注释说明它们的名字和索引词刻意避开 "x-ray"、"contrast" 这类通用词，"so that the imaging centre's own requests rank as before"。
  故事线第 3 步之后是分诊 P2 加协议建议（P3 → P2 具体怎么改的，资料里没写）。
- **怎么证明：** 故事线 12/12 步；`hospital-storyline.spec.ts` 通过。
- **经验：** 检索式推荐在"没有正确答案"时也会给答案，要么有覆盖检查，要么让人确认。往关键词检索的库里加条目时，要防止新条目抢走别的查询的排名（避开通用词）。
- **范围：** imaging（系统 5、6 申请单与协议，在 WP9 发现）。
- **来源：** PROGRESS WP9 审计表（step 3）和 Tasks（Step 3）；`apps/api/app/modules/README.md` "Known failure modes"。
- **代码：** `apps/api/app/modules/protocols/library.py`；提交 8d3cd5e。

### 12.8 故事线第 5 步的审计列是空的：审批记在 Task 上，没关联病人

- **现象：** WP9 进程内整条走查 12 步都过了，但第 5 步的审计检查失败；e2e 里 `story-audit-5` 下没有任何 `li`。
- **怎么查的：**
  1. 状态视图按病人 MRN hash 查审计。先猜审批记在 FlowException 上，于是加了按资源 id（`EXC-...`）查。e2e 里仍是空的：这次修改是基于猜测，没生效。
  2. 直接查 ioc_wp9 的 `audit_events`：没有任何 FlowException 行；模拟器自己的 EHR 写入也不审计。
  3. 按用户查 `U-OPS`：`approve | accept | Task | task-01333 | control_tower`，没有关联病人。
- **根因：** 床位经理的批准审计记在异常的审阅 Task 上，不带病人；按病人过滤的审计查询看不到它。
- **怎么修：** 第 5 步的审计指向这个审阅 Task 和批准产生的 Task，并显示批准人姓名而不是 id。
- **怎么证明：** 第 5 步在 UI 上通过，审计列有内容。
- **经验：** 先去数据库看实际写了什么，再改查询；第一次凭猜测改查询就白改了。"按病人检索审计"只覆盖带病人的事件，而运营类决定常常不带病人。
- **范围：** hospital（WP9 状态视图 + Control Tower 审计）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 00:40–00:57。
- **代码：** 提交 8d3cd5e。

### 12.9 故事线第 5 步取了"最新的骨科异常"，结果第 11 步永远等不到

- **现象：** e2e run 10：1–10 步通过，第 11 步 `Expected: "scheduled"  Received: ""`，4 分钟内随访电话没有被安排。
- **怎么查的：**
  1. 先怀疑后台 sweep 的触发条件（快进之后时钟冻结）：读 `sweep_step`（时钟走了 5 分钟或数据重建后才扫）和 `candidates`。
  2. 然后直接探测 API：时钟 `now 2026-10-12T11:01`（出院时刻），`current 5`（当前步骤竟然是 5），随访节拍 2026-10-14T11:06 一直没推进到；`FU-stay-j0001` 返回 404。
- **根因：** 第 5 步的状态取的是"最新的骨科异常"（`orth[-1]`）。她入院后骨科又开了一个新的、未决定的异常，第 5 步就重新显示为未完成，页面上"推进到下一个节拍"一直停在第 5 步，
  第 11 步的 48 小时快进从未触发。
- **怎么修：** 改为取带 early rounds 批准的那个异常（决定里有 `overflow_discharge_rounds` 动作）。
- **怎么证明：** run 11 的随访电话被安排并完成（GET 200，`completed`，`escalated`，`urgent`）。
- **经验：** 和 5.3 是同一个教训："最新的一个"在还在变化的数据里不是稳定标识，要用能唯一确定对象的条件。读代码猜触发条件不如直接问系统"现在在第几步"。
- **范围：** hospital（WP9 状态视图）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 01:34–01:36。
- **代码：** 提交 8d3cd5e。

### 12.10 架构守卫测试（grep 规则）：一半是真问题，一半是误报

- **现象：**
  - WP7、WP8 的全量后端测试都在 `tests/test_fhir_gateway.py::test_modules_go_through_the_gateway` 失败：`AssertionError: assert ['app/modules...ion/tools.py'] == []`
    （WP7：`documentation/evals.py`、`documentation/tools.py`；WP8：先是 `voice_services/tools.py`，后来是 `voice_services/evals.py`）。
  - WP9 全量后端里 `test_access.py::test_registry_entries_are_valid` 失败：`AssertionError: coding_assist`（documentation 层的模块必须能写 `DocumentReference preliminary`
    或 `Communication completed`）。
- **怎么查的：**
  1. 网关测试用正则扫 `app/modules` 下所有文件：`\.fhir\.(search|read|create|update|delete|count|ids)\b|store\.fhir\b|fhirstore|...`，规则是"模块不直接碰 FHIR store 或后端"。逐个看命中行：
     - `evals.py` 里的 `store.fhir.read("Patient", ...)`、`store.fhir.read("DocumentReference", ...)`：确实绕过了网关（不经访问策略、不留审计），**真问题**。
     - `tools.py` 里的 `ctx.fhir.read(...)`：`ToolContext.fhir` 的类型就是 `FhirGateway | None`（`app/agents/gateway.py`），已经是 Tool Gateway 为这次调用打开的网关，**是误报**。
  2. 注册表规则：新的 8.1 桩模块只写建议卡片，原规则没考虑这种模式。
- **根因：** 评测代码图方便直接读 store；正则只看字面，分不清 `ctx.fhir`（网关）和 `store.fhir`（原始 store）；注册表规则没想到"只出卡片"的模块。
- **怎么修：** 评测里的读取改走网关（WP8 `evals.py` 新增 `_patient()`）；`tools.py` 只是改写法让正则不再命中（WP7：`gateway = ctx.fhir` 后再 `gateway.read(...)`；
  WP8：`_fhir(ctx)` 辅助函数，文档注明 "The FhirGateway the Tool Gateway opened for this call"）。注册表规则明确允许"只有卡片类工具（`suggestEhrAction`、`submitForReview`、
  `createFlag`）"的模块，其余仍要求能写草稿文档或通话记录。WP9 在动手前就先查到了网关规则，把预置移到 `app/ehr/seed/journey.py`、EHR 剧本移到 `app/ehr/storyline.py`，
  模块只通过 FhirGateway 读。
- **怎么证明：** 相关测试重跑通过（WP7 52 个，WP8 83 个，之后全量通过；WP9 重跑后后端全绿）。
- **经验：** 用 grep 写的架构测试很便宜，而且确实能抓到真绕过（这里两次都抓到评测代码直接读 store）。但它有误报，满足它时要分清：真问题就改走正规路径；误报只改写法时，
  等于在测试上开了个口子，最好在代码里写清楚为什么这里是安全的，或者把规则改得更准。新模式和旧规则冲突时，把新模式写成明确的例外，而不是整体放宽。
- **范围：** hospital（WP7、WP8、WP9）。
- **来源：** wt-wp7-documentation-c03e03 10-10 04:43–04:46；wt-wp8-voice-services-addfd5 10-10 07:39–07:42、08:47–08:50；wt-wp9-storyline-7d11cd 10-10 22:00、10-11 02:36–02:43；
  PROGRESS WP8 Results。
- **代码：** `apps/api/tests/test_fhir_gateway.py`、`apps/api/tests/test_access.py`；提交 a04ca7b、e7a2319、8d3cd5e。

### 12.11 其他设计小记

| 现象 | 根因 | 解法 | 来源 |
| --- | --- | --- | --- |
| `adjustOrBooking.yaml` 被注册表拒绝：`app.agents.registry.RegistryError: adjustOrBooking.yaml: 1 validation error for ToolSpec`（"privileged tools take the approval record"） | 特权工具必须把 `approval_task_id` 列为必填 | 加上必填字段 | wt-wp9-storyline-7d11cd 10-10 21:53 |
| `ValidationError: 3 validation errors for DomainEvent attrs.codes.str Input should be a valid string [type=string_type, input_value=['wound_drainage'], input_type=list]` | 平台事件属性必须是标量，红旗代码是列表 | 拼成字符串 | wt-wp8-voice-services-addfd5 10-10 07:12 |

---

## 十三、Python 与框架的坑

这些问题的根因都是语言或框架的一条规则：名字绑定、导入顺序、参数默认值、类的身份。报错往往离根因很远，看到报错先想"这里涉及哪条语言规则"。

### 13.1 新函数名覆盖了模块级的锁：`def _lock(...)` 与 `_lock = threading.Lock()`

- **现象：** 跑文书模块的 Playwright 用例时，WebServer 输出里有 `{"level":"ERROR","logger":"app","msg":"documentation intake failed"}`，traceback 停在 `service.py` 第 710 行
  `intake()` 的 `with ...` 一行（转写里异常原文被截断）。后台 intake 每一轮都失败。
- **怎么查的：** 转写里没有展开排查过程。从修法看，会话发现模块里有两个同名的 `_lock`：模块开头的 `_lock = threading.Lock()`（进程内互斥，用在 `with _lock:`），
  和刚为"起草串行化"写的事务级 advisory lock 辅助函数 `def _lock(store, key)`（第 694 行）。
- **根因：** Python 模块里后面的 `def _lock` 把前面的同名变量重新绑定了，`with _lock:` 拿到的是一个函数而不是锁对象，于是后台 intake 一进来就报错。
  合并后的提交里可以看到：`_lock = threading.Lock()`（57 行）、`with _lock:`（710/732/739 行）、改名后的 `def _xact_lock`（694 行）。
- **怎么修：** advisory lock 辅助函数改名为 `_xact_lock`，调用处一并改。
- **怎么证明：** 转写里改名之后没有再出现这条报错，文书用例随后 3/3 通过（中间的失败是 2.6 的竞态，与此无关）。注意之后几次运行过滤掉了 WebServer 输出，所以"没再出现"只是没看到，没有专门的验证。
- **经验：** 模块级名字只有一个命名空间，后定义的同名函数会静默覆盖前面的锁、常量或单例。给辅助函数起能说明"是什么锁"的名字；ruff 的 F811（重复定义）这类检查能在提交前抓到
  （建议，不是会话里做过的）。后台循环里的异常只进日志，要在跑端到端时顺便看 WebServer 日志。
- **范围：** hospital（WP7 文书模块）。
- **来源：** wt-wp7-documentation-c03e03 10-10 04:12–04:14。
- **代码：** `apps/api/app/modules/documentation/service.py`（`_xact_lock`）；提交 a04ca7b。

### 13.2 循环导入只在某一个入口出现

- **现象：** 基线评测（`python -m app.aiops.evalrun --stamp`）跑完了，链在后面的 `python -m app.aiops.promptgate` 却报
  `ImportError: cannot import name 'registered' from partially initialized module 'app.agents.prompts' (most likely due to a circular import)`。
- **怎么查的：** `app/llm/prompts.py` 第 18 行写着 `from app.agents.prompts import registered  # noqa: E402  (after Prompt: no import cycle)`，作者以为把这行放在 `Prompt` 定义之后就没有环了。
  这只在**先导入 `app.llm.prompts`** 时成立；prompt gate 先导入 `app.agents.prompts`，它在顶部 `from app.llm.prompts import Prompt`，后者执行到第 18 行时又去拿还没初始化完的
  `app.agents.prompts`，于是失败。evalrun 和 pytest 的导入顺序不同，所以没撞上。
- **根因：** 两个模块互相在顶层导入，靠语句位置"化解"的环取决于谁先被导入。
- **怎么修：** 把 `RegisteredPrompt` 挪进 `app/llm/prompts.py`，它需要的注册表和加载函数改为在方法里延迟导入（`_current()` 里 import）。
- **怎么证明：** `promptgate` 能运行，先报 22 个版本里 4 个没有评测记录（这是门禁在正常工作），盖章后 0 个失败；`test_prompt_gate.py` 通过。
- **经验：** 解决循环导入不要靠"这行放后面一点"。每个命令行入口（`python -m ...`）都至少跑一次，因为它们的导入顺序各不相同。
- **范围：** 平台（WP10b 提示词注册表）。
- **来源：** wt-wp10b-ai-ops-060651 10-10 08:32–08:34。
- **代码：** `apps/api/app/llm/prompts.py`（`RegisteredPrompt`）、`apps/api/app/agents/prompts.py`；提交 03be4e4。

### 13.3 新代码绕开了测试的注入点：`test_gemini` 期待 `gemini-x`，拿到 `gemini-3.5-flash`

- **现象：** `tests/test_gemini.py::test_provider_selection` 失败：`assert gw.mode == "gemini" and gw.model_for("fast") == "gemini-x"`，实际是 `'gemini-3.5-flash'`。
- **怎么查的：** 看测试怎么换配置：`monkeypatch.setattr(gateway_module, "settings", config.Settings(...))`，只替换了**网关模块里**的 `settings` 这个名字。新写的模型注册表
  （`app/agents/models.py` 的 `model_id`）在函数里 `from app.core.config import settings`，读的是原始配置对象，所以看不到测试设的 `gemini_model_fast="gemini-x"`。
- **根因：** monkeypatch 改的是"某个模块里的名字绑定"，不是全局对象；新代码换了一条读配置的路，绕过了测试原来的注入点。
- **怎么修：** 模型 id 通过网关所用的 settings 来读。
- **怎么证明：** `test_gemini.py` 31 个测试全过。
- **经验：** 重构"从哪里读配置"时，先看现有测试是在哪一层注入的；最好让配置只有一个入口。
- **范围：** 平台（WP10b 模型注册表）。
- **来源：** wt-wp10b-ai-ops-060651 10-10 07:43–07:46。
- **代码：** `apps/api/app/agents/models.py`（`model_id`）、`apps/api/tests/test_gemini.py`；提交 03be4e4。

### 13.4 容器启动即退出：`os.getenv` 的默认值总会先被计算

- **现象：** `docker compose up --build` 后 `container ioc-test-api-1 is unhealthy`，web 因依赖失败没起来。
- **怎么查的：** `docker logs` 看到导入链 `app/main.py:21` → `app/modules/evals/router.py:10` → `app/modules/evals/paths.py` 第 5 行 `EVALS_DIR = Pat…`（报错原文在转录里被截断）。
  镜像里已经设置了 `EVALS_DIR`，按理不该用默认值。
- **根因：** `Path(os.getenv("EVALS_DIR", Path(__file__).resolve().parents[5] / "evals"))` 中，默认值作为参数**总会先被计算**，不管环境变量有没有设。容器里文件路径是
  `/app/app/modules/evals/paths.py`，层级太浅，`parents[5]` 不存在。
- **怎么修：** 改成 `Path(os.getenv("EVALS_DIR") or Path(__file__).resolve().parents[5] / "evals")`，和 `graph.py` 的写法一致。
- **怎么证明：** 重建后容器 `Healthy`；通过 nginx 冒烟：SPA 路由、`/api/health`、登录、知识图谱（3,668 个疾病）、评测结果都正常。
- **经验：** 函数参数里的默认值是急切求值的。默认值本身可能出错或很贵时，用 `or` 或显式判断。代码在本机能跑，不代表在不同目录结构的镜像里能跑，要实际起容器验证。
- **范围：** 平台（Docker 部署）。
- **来源：** main__b1ffba74 10-07 14:10–14:12。
- **代码：** `apps/api/app/modules/evals/paths.py`；提交 9458db0。

### 13.5 在一个进程里加载旧版本模块做对照：`feedback_surveys holds Survey, not Survey`

- **现象：** 为了证明新回归测试在旧代码上会失败，两个会话都用 `git show HEAD:...service.py` 把旧版种子存成文件，再用 importlib 加载到当前进程。一运行就报
  `TypeError: feedback_surveys holds Survey, not Survey`（账单会话里是 `TypeError: claims holds Claim, not Claim`）。
- **怎么查的：** 报错来自 `app/core/db/repo.py` 的 `__setitem__`：`raise TypeError(f"{self.name} holds {self.model.__name__}, not {type(obj).__name__}")`。同一个模块文件第二次加载时，
  会重新执行类定义，生成同名但不同的类对象；store 按已注册的类检查类型，所以拒收。
- **根因：** Python 的类是对象，类型检查按"是不是同一个对象"判断，不按名字。
- **怎么修：** 加载旧模块后把它的模型类换成已注册的类：`old.Survey, old.FeedbackResponse, old.Alert = new.Survey, new.FeedbackResponse, new.Alert`
  （账单会话：`old.Claim, old.WorkItem = billing.Claim, billing.WorkItem  # the store checks the model class`）。
- **怎么证明：** 之后对照能跑通：反馈的回归测试在旧种子上 24 个时刻里漏 4 个、新种子 0 个；账单的回归测试在旧种子上漏掉 AP-11380 的预埋拒付（16:00 播种，一天后对账）。
- **经验：** "在旧代码上跑新测试"是证明测试有效的好办法，但只替换一个模块时，要让它和其他代码共用同一份类型定义；或者干脆在一个检出了旧提交的 worktree 里跑（14.5）。
- **范围：** imaging（反馈系统 19、账单系统 18 的种子修复）。
- **来源：** main__56196a27 10-09 06:30–06:32；wt-lucid-mestorf-0d5ad0 10-09 20:46–20:50。

### 13.6 其他小记

| 现象 | 根因 | 解法 | 来源 |
| --- | --- | --- | --- |
| `config/safety.yaml` 报 `yaml.scanner.ScannerError: mapping values are not allowed here` | 一个未加引号的值里含 `: `（`evidence: evals/injection (an attack call executed: 0)`） | 加引号，再用 grep 扫一遍其他同类行 | wt-wp10-roadmap-docs-6e66e7 10-11 07:48 |
| Temporal 沙箱校验草稿工作流时失败 | 沙箱会重新 import 工作流所在的模块，没有 `__main__` 保护的脚本会再跑一次 | 见 9.8 | main__025b79fc 10-09 21:52 |

---

## 十四、测试与评测的可信度

测试、评测、裁判（judge）、对照实验本身也是代码，也会错，也会"空转通过"。这一章收的是"怎么知道你的证据可信"：只走一条路径的功能、自己出题自己答的评测、
对照组本身不确定、在旧代码上跑不起来的回归测试、被悄悄改写的结果文件。满分要怀疑的另一个例子是 4.3。

### 14.1 电话 agent 的模型路径从来没跑过：第一次有评测集，一次暴露六个问题

- **现象：** 影像前台的两个 agent（`voice_agent`、`call_summary`）一直被统一评测器报成 "no eval set"。mock provider 也没有工具调用型的 agent，所以**没有 API key 时，模型 agent 这条路径
  （每一轮的去标识化、Tool Gateway、trace）根本不会执行**，演示里的电话一直走确定性的兜底 `ScriptedAgent`。
- **怎么查的：**
  1. 按 SPEC 写 20 通脚本电话（英 11、中 7、法 2）。harness 让每通电话走电话线自己的路径（`service.take_turn` / `end_call`，和 API 共用），每个用例在会回滚的事务里给自己的病人订预约，
     所以哪天跑都行；写一个关键词策略的 mock（`mock_agent.py`，`MOCK_TOOL_FIXTURES`），网关把任务交给 provider 的 `tool_turn`，没有 key 也能跑模型 agent 的工具循环；
     兜底 agent 在同样 20 通电话上跑一遍，作为额外指标。
  2. 第一次冒烟运行（10-10 15:36 UTC）：两个 agent 都 20/20，但额外指标显示 `"calls_with_raw_identifiers_to_model": 7`、`"fallback_task_success": 0.7`
     （评测运行 run-2026-10-10T114545 同样是两个 agent 20/20、兜底 14/20）。
  3. 打印几通电话的完整转写来读，而不只看分数：`fd-01` 改约成功了（工具调用里有 `reschedule`），结果却记为 `abandoned`。逐条看兜底的失败。
- **暴露的问题和各自的修法：**
  1. **模型 agent 没有确认预约的工具**，只有兜底能确认。新增工具 `confirmAppointment`（voice_agent 1.1.0），兜底也改走它。（7a4ca1e）
  2. **口述的出生日期原样发给模型**：20 通里 7 通，凡是中文或法文报生日的都漏了。会话没有在评测集的提交里顺手修，而是提成单独的后续任务，就是 4.1。（4fb2f6d）
  3. **模型 agent 完成的电话全部记为 `abandoned`**：只有 `ScriptedAgent` 在 `session.outcome == "in_progress"` 时把它改成 `resolved`（agent.py 第 164–165 行）。修法是把状态设在
     "真正做事的地方"：`reschedule`、`cancel`、`confirm_appointment`（真的确认时）、`get_prep_instructions`、`get_site_info` 五个工具调用 `_resolved`，转人工仍然优先。修后模型路径
     12 通 resolved、5 通 transferred（原来没转人工的 15 通全是 abandoned）；fd-03（不改约）、fd-09（确认被拦）、fd-20（没有预约）仍是 abandoned，因为什么都没做，而模型没有"通话结束"信号。
     裁判新增：做完的请求没有记为 resolved 就判失败。（9780dcf）
  4. **兜底只完成 14/20**（失败 fd-02、fd-06、fd-08、fd-12、fd-14、fd-16）：不懂法语（`parse_dob` 只认英文月份）；中文听不懂"第二个"、"确认"和医学问题，所以**中文的医学问题不会转给人工**；
     问任何站点都回答 Lakeshore 的地址（传了 `site=""`）。修法：兜底和 mock 共用一张三语关键词表 `intents.py`（"第二个"、"la deuxième"，重音可省略、弯引号 ’ 也认）；
     医学规则偏向"宁可匹配"，因为匹配的结果只是转人工；`tools.find_named_site` 回答来电者说的那个站点。顺手修了一个崩溃：已核验、但没有预约的来电者说"确认"会让兜底崩掉
     （之前只检查了取消、改约和准备说明）。14/20 → 20/20（run-2026-10-10T141652）。会话自己指出这个 20/20 偏乐观，因为规则是对着这些脚本扩的，所以另写 4 通集外电话验证，全部通过。（9780dcf）
  5. **兜底对法语和中文来电者用英文回答**（读出的时间、准备说明、营业时间也是英文）。修法：每轮判断语言（有汉字就是中文，否则比较法语和英语关键词数），存进 `session.state["lang"]`；
     所有回复来自手写的三语短语表 `phrases.py`；读转写时还发现法语省音错误（应为 "à l'Eastview"）并修正。（80bfa28）
  6. **电话页面用 `en-CA` 读所有回复**（`u.lang`、`r.lang` 写死）。修法：按每条回复的语言选 `en-CA`、`fr-CA`、`zh-CN`，识别也用通话的语言。因为无头 Chromium 没有真实语音，新 spec
     `frontdesk-languages.spec.ts` 记录页面让浏览器"说了什么、用什么语言"来断言。（99bf5fe）
- **根因：** 同一功能有两条实现路径（模型 agent 和兜底），测试和演示只走其中一条；另一条路径上的缺口（缺工具、去标识化漏洞、状态没设）长期没人看见。两套 agent 的意图规则各写各的；
  "resolved"只在脚本 agent 的流程里设置，不在真正完成请求的工具里设置。
- **怎么证明：** 每一项都有对应的评测指标或门禁：`calls_with_raw_identifiers_to_model` 7 → 0 并成为门禁；`fallback_task_success` 14/20 → 20/20；`run-2026-10-10T150508` 加了语言检查后
  兜底仍 20/20，若用英语回答，9 通法语和中文电话会判失败（14.2）；每轮服务端平均 91 ms（p95 327 ms）。三个会话的后端全套分别是 658/659、684（另有 1 个后加的测试单独跑通过）、
  701/702，唯一的失败都是 5 秒候补计时测试，单独跑通过。连带现象：4.1 修好后，fd-06 和 fd-12 能通过核验了，接着就卡在法语请求上：修了一层才看到下一层。
- **经验：** 只有拿到真实 key 才会执行的代码路径，等于没测过。给它一个 mock 和一个评测集，并且把"副指标"（兜底成功率、发给模型的原始标识数）也打出来；再读几通完整转写，
  分数 20/20 的时候问题也可能藏在转写里。裁判要检查系统状态（通话记录），不能只看回复文字。规则和脚本由同一个人一起扩充时，20/20 是偏乐观的，要用集外用例再验一次。
- **范围：** imaging（系统 4 前台电话）。
- **来源：** wt-adoring-raman-2c26f4 10-10 15:21–15:51；wt-dazzling-borg-298d62；wt-goofy-hermann-1dcfd7；PROGRESS "System 4 eval set · voice_agent and call_summary"。
- **代码：** `apps/api/app/modules/frontdesk/`（`evals.py`、`mock_agent.py`、`intents.py`、`phrases.py`、`tools.py`）、`apps/web/src/features/frontdesk/PhoneAgent.tsx`、
  `apps/web/e2e/frontdesk-languages.spec.ts`；提交 7a4ca1e、9780dcf、80bfa28、99bf5fe。

### 14.2 评测裁判本身也要测：一个"因为错误原因而失败"的负面测试，以及几种证明裁判不是摆设的做法

- **现象：** 新写的负面测试 `test_a_completed_call_that_the_call_log_does_not_record_as_resolved_fails` 失败：它用 `monkeypatch.setattr(tools, "_resolved", lambda session: None)`
  让工具不再设 resolved，期望模型路径和兜底路径的 fd-07 **都**判失败，但兜底那一行仍然 `'outcome': 'resolved'`，`assert not row["passed"]` 不成立。
- **怎么查的：** 兜底除了工具，还会在来电者说再见时把通话标成 resolved（`ScriptedAgent` 自己的逻辑）。所以关掉 `_resolved` 并不能让兜底"做完却没记 resolved"：测试的前提错了，不是裁判错了。
- **根因：** 负面测试只关掉了产生某个结果的**一条**途径，而被测对象还有另一条途径。
- **怎么修：** 这个测试只检查模型 agent 那一行。
- **同一批会话里其他"测裁判"的做法：**
  - 写了 8 个新测试，其中几个是负面用例：模型回答了医学问题、订错了选项、核验身份之前就读出预约，裁判都要判失败（会话的说法是先 "verify the judge isn't vacuous"）。
  - 加了"兜底用错语言就失败"的检查后，写脚本把兜底强制改回英文再跑 20 通：恰好 9 通法语/中文失败（`answered in English, not Chinese (turn 1)`），11 通英文通过。
    这证明了 README 里"旧版英文回复会让 9 通全部失败"的说法。
  - 把容量工作流测试从"至少 3 个异常"改成 2 个的同时，加了一条"批准后确实有要执行的动作"的断言，免得测试空转通过；提交前又把回归测试的检查直接跑在**基线提交的**
    `medsafety.py` 上，全部失败，证明它确实能抓住旧 bug（5.3）。
- **经验：** 裁判和断言也是代码，也会写错。每加一条检查，至少准备一个"应该失败"的输入，在旧代码或故意弄坏的输入上看它真的失败；负面测试要确认被关掉的是**唯一**的途径。
  放宽一个断言时，同时加一个"不会空转"的断言。
- **范围：** imaging（系统 4）、hospital（WP6、WP4c 测试）。
- **来源：** wt-dazzling-borg-298d62 10-10 18:13–18:16；wt-adoring-raman-2c26f4 10-10 15:37–15:44；wt-goofy-hermann-1dcfd7 10-10 19:40–19:41；wt-hopeful-ellis-36d8fc 10-11 07:49、09:53。
- **代码：** `apps/api/tests/test_frontdesk_eval.py`；提交 9780dcf、80bfa28、95c8eef。

### 14.3 正则 `\b` 遇到中文：裁判误判"没给地址"

- **现象：** 兜底改成中文回答后，`test_the_calls_meet_the_bar_with_the_mock_model_and_the_scripted_fallback` 失败：`('fd-14', ['the address was not given'])`。
  但打印出来的中文回复里明明有地址（"……地址是482……"）。
- **怎么查的：** 裁判用 `re.search(rf"\b{number.group()}\b", r)` 找门牌号（例如 482）。中文回复里门牌号紧跟在汉字后面。Python 3 的 `str` 正则默认按 Unicode 处理，汉字和数字都算 `\w`，
  两者之间没有"单词边界"，所以 `\b482` 匹配不上。
- **根因：** `\b` 的定义是 `\w` 和 `\W` 的交界；中文不用空格分词，汉字后面直接接数字时根本没有边界。
- **怎么修：** 改成数字边界（前后不是数字，如 `(?<!\d)…(?!\d)`），而不是单词边界。同时补了裁判的另一个漏洞：核验身份前的回复也检查法语和中文写法的预约日期。
- **怎么证明：** 20 通兜底全部通过；`run-2026-10-10T150508` 两个 agent 20/20，兜底 20/20；提交说明写明裁判 "finds the address number after a Chinese character"。
- **经验：** 评测失败时也要怀疑裁判。对多语言文本用 `\b` 要小心，真正想表达的往往是"前后不是数字"或"前后不是字母"，就直接用前后查找写出来。
- **范围：** imaging（系统 4 评测裁判）。
- **来源：** wt-goofy-hermann-1dcfd7 10-10 18:59；PROGRESS "System 4 eval set"（"Two judge fixes on the way"）。
- **代码：** `apps/api/app/modules/frontdesk/evals.py`；提交 80bfa28。

### 14.4 可溯源校验器漏看句末的数字

- **现象：** 新单元测试 `test_subclasses_share_the_base_class` 失败：交班单里写了 `"Heart rate 99."`（来源里没有 99），期望校验器报 `number_not_in_source`，实际什么也没报。
- **怎么查的：** 看抽数字的正则：`NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")`。后面的否定前瞻 `(?![\w.])` 不允许数字后面紧跟句点，本意是不把 `1.2.3` 这类拆开，
  结果句末的 "99." 整个不被当成数字。
- **根因：** 边界条件写得太宽：把"后面是句点"一律排除，而句点也可能只是句子结束。和 4.3 去标识化评测里漏掉句末日期是同一个写法。
- **怎么修：** `(?<![\w.])\d+(?:\.\d+)?(?!\w|\.\d)`（注释 "a number may end a sentence"）：只有句点后面还跟数字时才排除。
- **怎么证明：** 校验器测试 18/18 通过。
- **经验：** 校验器的漏报比误报更危险（这里意味着编造的数字能通过"可溯源"检查）。写抽取正则时，用"句末""括号里""带单位"这些位置各测一遍；为"应该报错"的情况写测试，
  这次就是这样抓到的。同一个错误写法在仓库里出现了两次，修一处时可以顺手 grep 一下别处。
- **范围：** hospital（WP7 文书校验器）。
- **来源：** wt-wp7-documentation-c03e03 10-10 04:07–04:09。
- **代码：** `apps/api/app/modules/documentation/validator.py`；提交 a04ca7b。

### 14.5 是不是我弄坏的？在基线提交上跑同样的测试做对照

- **现象：** 周日（10-11）WP9 重跑 `test_workflows.py` 和 `test_med_safety.py`：27 passed、5 failed，这些测试前一天全量都过了。全量 Playwright：35 passed、2 skipped、7 failed
  （5 个 business-compliance、2 个 med-safety）。
- **怎么查的：**
  1. 后端：怀疑日期依赖（测试库按当天生成）。用 `git worktree add --detach` 在临时目录检出基线 a454d12，复制 .env，借用 WP9 的 venv，用一次性库 `ioc_test_wp9base` 跑同样 5 个测试：
     同样 5 个失败（`5 failed, 1 passed in 512.86s`）。结论：既有问题、依赖日期，不是 WP9 引入的（后来的 5.3）。删掉临时库和 worktree。
  2. Playwright：先确认 WP9 没改这两个 spec 和 web 依赖；在基线 worktree 里起基线 API（同一个库、同一个端口），只跑这两个 spec：business-compliance 全过，med-safety 只挂药师流程。
     再用 WP9 代码只跑这两个 spec：10 passed，挂的也是同一个药师测试，与基线完全一致。结论：全量里多出来的失败是负载和顺序效应。
  3. 清理：基线 worktree 的 `node_modules` 是指向 WP9 的 junction。先只删 junction 本身（`[System.IO.Directory]::Delete($j, $false)`；`cmd /c rmdir` 被工具的保护规则拦下），
     再 `git worktree remove --force`，最后确认 WP9 的 node_modules 完好。
- **同一个方法在别处：** Westbrook 失败时检出 dd98b56 单独跑（5.5）；账单失败时 `git stash` 去掉自己的改动再跑（5.7）；ICU 校准时把旧版 `reference.py`、`simulate.py` 临时放回去
  在同一份数据上重跑（5.9）；修 2.5 时新旧代码交替跑。
- **根因：** 5 个后端测试和药师 e2e 依赖播种日（5.3，提交 95c8eef）；其余是负载。
- **怎么修：** 不在 WP9 里修，记为 follow-up，并提议开单独的会话处理。
- **经验：** 同一天、同一台机器、同样的测试，只换代码版本（基线 vs 新代码），是回答"是不是我弄坏的"最直接的实验。临时 worktree 借用现有的 venv 和 node_modules（junction）可以省掉安装时间，
  但删除前要先拆 junction，否则可能删掉真实目录。注意它的边界：同一天 WP10 把"单独重跑能过"当成"只是负载"，后来证明是真 bug（5.1）。基线对照能告诉你"不是我"，不能告诉你"没有 bug"。
- **范围：** hospital（WP9 收尾）、imaging（business-compliance 等）。
- **来源：** wt-wp9-storyline-7d11cd 10-11 04:46–04:59（后端）、06:10–06:50（Playwright）。

### 14.6 对照基线本身不确定：先比较"旧代码对旧代码"

- **现象：** 出现过两次，都是"新旧对比"里冒出了不该有的差异：
  1. 账单修复（5.4）：比较新旧种子在 48 个播种时刻生成的全部数据，想证明修复只影响账单，结果 `critical_cases`、`feedback_surveys`、`outbox` 也有差异。
  2. 重置锁修复（2.4）：验证生成能不能并行，两个线程同时生成和单独生成相比，38 个集合里 3 个不同（`module:critical_cases`、`module:feedback_surveys`、`outbox`），看起来像线程 bug。
- **怎么查的：** 账单那次，`critical_cases` 在账单之前就播种好了，账单的修改不可能影响它。于是假设"这是生成过程本身的不确定性"，用同一份旧代码、同一个时刻跑两次来对比：
  `old vs old, 15:00: ['modules.critical_cases', 'modules.feedback_surveys', 'outbox'] claims equal True planted equal True`。也就是说，同样的代码跑两次也会有这三处不同。
  重置锁那次做了同样的对照：连续两次单线程生成，差异的字段一模一样（critical_cases 的 `ack_due_at`、`created_at`、`events`、`next_action_at`，以及随机的问卷 token）。
- **根因：** 这几个字段用了随机 token 和真实时钟的时间戳；比较工具把"每次运行都会变"的字段当成了修改带来的差异。
- **怎么修：** 比较时排除这三个字段（脚本里注明 "differ between two runs of the same code"）。
- **怎么证明：** 排除后，账单那次 48 个时刻里有 33 个移动了预埋记录（一共 61 条），其余数据完全相同；重置锁那次确认同一进程两次并发生成的数据和单次生成一致，只在这三处不同。
- **经验：** 用"新旧对比"证明修改范围之前，先做一次"旧对旧"，确认基线是确定的。不然差异里会混进噪声，可能让你误判修复有副作用（或者掩盖真的副作用）。
- **范围：** imaging（系统 18 账单的种子）、平台（演示重置）。
- **来源：** wt-lucid-mestorf-0d5ad0 10-09 20:58–20:59；wt-priceless-shamir-24594d 10-11；PROGRESS "WP10 follow-up · the demo reset holds its lock only while it writes"。

### 14.7 从内存 Store 换成数据库后，测试的"对象身份"假设失效

- **现象：** PostgreSQL 迁移后的失败里有一组和业务无关：
  - `test_dashboard_version_changes_after_booking`：预约后看板版本号没变。
  - 4 个测试：`assert appt.status == AppointmentStatus.COMPLETED` 得到 `confirmed`；库存 `assert (13 == (13 - 1))`；收货 `28 == 28 + 250`；反馈 `resp.ai_status` 是 `None`。
  - `test_audit_chain_detects_tampering`：`AuditLog()` 现在需要一个 store 参数。
- **怎么查的：** 看断言里的值：数据其实已经被请求正确修改，只是测试手里的对象还是请求之前取出来的那个。内存 Store 里测试和请求共享同一个对象，请求改了它，测试自然看得到；
  换成数据库后，每个请求有自己的 unit of work 和对象副本，测试共享的 store 在请求后丢掉已加载对象，好让后续读取真的走数据库。版本号的问题同理：测试路径只 flush 不 commit，
  而版本号只在 commit 时增加。
- **根因：** 测试隐含依赖了"内存里只有一份对象"这个实现细节。
- **怎么修：** 新增 `Store.save()`（flush 并增加版本号），`commit()` 和测试路径都走它；4 个测试改成请求之后重新读取对象再断言（检查本身没有放松）；审计链测试用脱离数据库的 `Store().audit`。
- **怎么证明：** 这一组全部通过；整套 154/154（2.5 分钟）。新增 `tests/test_db.py`：跨 unit of work 的修改可见、PHI 列在 SQL 里是密文且盲索引匹配、审计表拒绝 UPDATE/DELETE、
  SKIP LOCKED 的认领、`alembic check` 无差异、行缓存不会给旧版本、行缓存复制出的对象不共享可变状态。Playwright 19/19。
- **经验：** 换存储层时，测试里"拿着对象引用等它变化"的写法会失效。断言应该在动作之后重新读取被测状态，而不是依赖对象身份。同一批失败里还有"种子变老"（5.7）和计时测试（7.2），
  要逐条按类型分开处理。
- **范围：** imaging（阶段 0 数据层迁移）。
- **来源：** main__394ebe0a 10-07 23:16–23:24；main__d95dd1c0 10-07 22:27；PROGRESS 阶段 0 "Finishing work"（"Failing tests fixed"）。
- **代码：** `apps/api/app/core/store.py`（`save`）、`apps/api/tests/test_db.py`；提交 9458db0。

### 14.8 结果文件要能说明自己：被 mock 覆盖、被缺环境的重跑抹掉、版本号没标脏

- **现象（三件事）：**
  1. **mock 覆盖了真实结果。** 合并 WP8 和 WP10b 后快进合并时 git 拒绝：`Your local changes to the following files would be overwritten by merge: evals/results/clinical_kg.json`。
     这个文件里是开发者还没提交的真实 Gemini 评测结果，而 WP10b 的提交里是一次 mock 基线重跑。合并后用 mock 跑一遍全部评测：23 个 agent 都达标，但评测脚本**把 `clinical_kg.json`
     改写成了 mock 基线**，另外 7 个旧结果文件也被改写。
  2. **缺环境的重跑抹掉了旧结果。** WP8 重新生成工作流评测报告时，WP4c 的真实 Temporal 服务器那一节被覆盖成 `{'ran': False, 'why': '--real-server not given'}`。
  3. **版本号没标脏。** WP9 的离线故事线报告写着 `git a454d12`，但它跑的是未提交的 WP9 工作树。
- **怎么查的：** 1：比较 WP10b 改动和本地改动（`mode: mock` vs `mode: gemini`）。合并前已把开发者的文件备份到 scratchpad，stash、快进、还原；评测跑完后从备份还原 Gemini 结果；
  对另外 7 个文件只统计 `run_at`、`seconds` 以外的改动行数，都是 0，于是还原。2、3：重新生成后读一遍报告发现的。
- **根因：** 评测脚本的 mock 基线和真实运行写同一个结果文件；报告脚本把"这次没跑"也写成结果；报告只记 HEAD，不看工作树。
- **怎么修：** 1：从备份还原；2：从上一版报告恢复该节并标注"沿用"；3：脚本检测工作树有没有改动，有就标 `-dirty`（之后的 Temporal 报告就是 `a454d12-dirty`），离线报告手工更正了这一字段。
- **一个没追查的疑点：** 合并后为 WP8 的两个 agent 加 `--stamp` 重跑时，输出是 `call_center: met task_success None (0/0), threshold 0.8`，也就是 0 个用例也算"达标"。转录里没有追查这一点，
  值得核对门禁是否应该拒绝 0/0（门禁合并时的另一个问题见 16.3）。
- **经验：** 会改写被跟踪文件的脚本（评测、格式化、报告）运行前，先备份本地未提交的结果；重新生成报告前后 diff 一下，别让缺少环境的那部分结果被静默抹掉。结果文件里的版本号要能说明
  到底跑的是什么代码：只记 HEAD 会误导；和第六章"评测结束时才读 git 状态"互为补充，一个是根本不标脏，一个是标脏的时机不对。"0/0 达标"和满分一样是警报。
- **范围：** 平台（评测与报告脚本）。
- **来源：** main__7a422c19 10-10 10:23–10:40；wt-wp8-voice-services-addfd5 10-10 10:05；wt-wp9-storyline-7d11cd 10-11 03:44。
- **代码：** `apps/api/scripts/storyline_report.py`；提交 0485ffb、8d3cd5e。

### 14.9 其他测试与评测小记

| 现象 | 根因 | 解法 | 来源 |
| --- | --- | --- | --- |
| 评测汇总 `dict(sorted(calls.items()))` 报 `TypeError: '<' not supported between instances of 'NoneType' and 'int'` | 有的病例没记到模型调用次数（键为 None） | 正确读取每份文档的模型调用数 | wt-wp7-documentation-c03e03 10-10 04:03 |
| `tests/test_voice_eval.py` 只跑评测子集时 `ZeroDivisionError` | 子集里没有红旗（或没有正常）脚本 | `max(len(...), 1)`；评测汇总函数要能处理空子集和缺失值 | wt-wp8-voice-services-addfd5 10-10 08:47；PROGRESS WP8 Results |
| 校验栏写 "2 untraceable items"，其实只有 1 项 | metoprolol 同时触发 `untraceable_medication` 和 `untraceable_drug` 两条问题 | 按条目路径去重计数 | wt-wp7-documentation-c03e03 10-10 04:27 |
| WP8 注入评测报告把语音 agent 自己的工具调用算成了模型请求 | 计数口径错了 | PROGRESS 只记了现象 | PROGRESS WP8 |
| WP10b 低置信度工作流测试还在读旧的 per-agent `report.json` | 测试没跟上新的结果文件 | PROGRESS 只记了现象 | PROGRESS WP10b Results |
| 工作流时间线测试 `steps[1]["status"]` 期望 `running`，实际 `pending` | 测试按下标取步骤，急诊阶段在前面插了两个步骤，下标 1 变成了 `imagingOrders` | 按步骤 key 取，并核对 `next_step` 仍是 `medRecAdmission`；有序列表按 key 访问 | wt-wp9-storyline-7d11cd 10-11 02:36–02:43 |
| `test_day_simulator.py` 断言出院后没有任何 MedicationRequest 是 active | 出院处方按设计会保持 active，测试的假设错了 | 只检查住院医嘱已完成 | PROGRESS WP6 |
| 在线 e2e 跑完后仓库里的 `evals/patient_message_triage/cases.jsonl` 多了一条 | 低置信度回路会把人工纠正写成评测用例，没设 `AGENT_EVALS_DIR` 时写进仓库 | e2e 运行时把 `AGENT_EVALS_DIR` 指到 scratchpad；已写入的用例还原 | main__025b79fc 10-09 23:02；wt-wp6-med-safety-570354 10-10 02:22 |
| 走查脚本自己的 bug：requests 接口把列表包在对象里；异常列表视图不带 menu，要打开详情；MedRec 条目名取错字段（`no amlodipine omission: [None, None]`）；看板的键是 `or_cases`（`KeyError: 'or'`） | 走查脚本对接口形状的假设和实际不符 | 逐个改正 | wt-wp9-storyline-7d11cd 10-10 22:29–23:11 |

---

## 十五、用脚本改代码的坑

开发会话常用"Python 脚本 + heredoc"批量改代码。这里有两层甚至三层转义（Bash heredoc → Python 字符串 → 目标代码里的正则），每一层都可能吃掉或改写字符。
这一章的统一解法：改代码用编辑工具，或把修改脚本写成文件再运行；改完看 diff，并用 `cat -A` 或控制字符扫描看不可见字符。

### 15.1 正则里的 `\b` 变成了退格符（三个会话踩了四次）

- **现象：**
  1. 去标识化会话（4.2）用补丁脚本把测试里的断言改成 `re.search(rf"\b{family}\b", sent)`。`git diff` 里显示成 `rf"{family}"`，看起来像 `\b` 丢了。
  2. WP8 第一次：用 `python - <<'PYEOF' ... PYEOF` 改 `identity.py` 的正则后，下一次替换找不到原文；同时打印出来的中文是乱码（`�ҽ�`）。
  3. WP8 第二次：修 12.4 时又这样写了一个替换脚本，改完后 "The second one"、"one"、"two" 等英文输入全部返回 `None`，只有中文和 `^\s*2\s*$` 这种还能匹配；
     脚本运行时还打印了 `SyntaxWarning: invalid escape sequence '\s'`。
  4. WP9：后端测试 `test_the_callers_relation_is_who_they_are_to_the_patient` 失败：`"I'm her son, she is Mary Smith"` 的 relation 是 `None`。中文"我是她女儿"却正常，
     所以故事线第 1 步（中文来电）一直是通过的。
- **怎么查的：**
  - 1：觉得 diff 不对，用 `cat -A` 一看，那一行是 `re.search(rf"^H{family}^H", ...)`：`\b` 变成了退格字符（0x08）。
  - 2：打印 `repr()` 才看到文件里是 `r"\x08(my name is|...)\x08|..."`；在写回前加 `assert '\x08' not in s`（断言失败，没有写坏文件）。
  - 3：`git diff ... | cat -A`，新行里是 `^H(first|1st)^H`，`chr(8)` 计数为 12。
  - 4：直接调用 `identity.parse` 复现，不跑整套测试（另一个 pytest 会话会把测试库删掉重建）；看 `git diff`，再单独测 `SELF_RELATION.search(...)` → `None`；打印
    `repr(I.SELF_RELATION.pattern)`：开头是 `"\x08(?:i'?m|i am) ..."`；`grep -rlP "\x08"` 找到 identity.py 第 42、43 行两个退格符，`od -c` 确认。第一次用 heredoc 里的 python 片段替换后，
    `od -c` 仍显示控制字符；改用写成文件的脚本再替换才生效（记录没解释第一次为什么没生效）。最后用 `grep -rlP "[\x00-\x08\x0b\x0c\x0e-\x1f]"` 扫一遍整个改动集。
- **根因：** 替换文本写在 Python 的**普通字符串**里（`'...'`、`'''...'''`），不是 raw 字符串。heredoc 用了带引号的 `'PYEOF'`，bash 不动反斜杠，但承载代码的那层 Python 字符串会把
  `\b` 解释成退格转义，写进源文件的是一个控制字符而不是两个字符 `\b`；目标代码虽然写成 `r"\b..."`，也挡不住外面那一层。`\s` 不是合法转义，原样保留（只给一个 SyntaxWarning），
  所以 `\s` 正常、`\b` 坏掉：英文单词边界的规则全部失效，中文规则照常工作，很容易误以为是规则逻辑的问题。
- **怎么修：** 把文件里的 `chr(8)` 全部替换回 `\b`（`s.replace(chr(8), chr(92)+'b')`），或用 Edit 工具改回（去标识化会话还把行尾恢复成和其他文件一致的 CRLF）。之后改含正则或中文的代码时，
  把修改脚本写成 UTF-8 文件再运行（"via a UTF-8 script to keep the Chinese intact"），或者直接用编辑工具；用 `grep -c $'\x08' app/modules/voice_services/*.py` 检查控制字符。
- **怎么证明：** `cat -A` 显示 `\b`；`ordinal()` 那组输入全部返回正确序号；`grep -c` 全为 0；WP9 的四个英文自述句子（"I'm her son"、"I am her son"、"i'm her daughter" 等）都能匹配，
  控制字符扫描为空，测试通过。heredoc 转义这类坑早在 WP4 就写进了 memory（local-windows-env-quirks），之后的 WP8、WP9 会话仍然踩到。
- **经验：** 用脚本生成或修改代码时有两层字符串转义（脚本里的字符串、目标代码里的字符串），含反斜杠的文本要用 raw 字符串（`r'...'`），或者直接用编辑工具；改完用 `git diff | cat -A`
  看一眼不可见字符，或扫一遍控制字符，成本很低。症状"只有某一类输入失效"（英文坏、中文好）往往是转义或编码问题，而不是业务逻辑问题。多语言功能每种语言都要有测试，只走中文路径
  会掩盖英文路径的 bug。光把坑记下来不够，改代码时直接用编辑工具更可靠。
- **范围：** 环境（工具用法），出错的代码在 hospital 的 WP4 测试、WP8、WP9。
- **来源：** wt-zealous-napier-1d0414 10-09 20:42；wt-wp8-voice-services-addfd5 10-10 07:06–07:07、08:21–08:22；wt-wp9-storyline-7d11cd 10-10 22:06（引入）、10-11 00:29–00:36（发现和修复）。

### 15.2 heredoc 和多层转义的其他症状

- **现象：**
  - 长的带引号 heredoc（`python - <<'PYEOF'`、`cat <<'EOF'`、`bash -c` 加 heredoc）报 `/usr/bin/bash: -c: line 116: unexpected EOF while looking for matching `''`，整个命令没有执行：
    WP4、WP4b、WP5 三个会话一共 5 次，WP10b 和系统 4 的两个修复会话也都遇到过；WP9、WP10 还有 `Exit code 2`、Python `SyntaxError: unterminated string literal`。另有几次转义纠缠
    （"Escaping got tangled in the heredoc"、"Shell escaping again"、"fighting sed quoting"）。
  - 用 Python 脚本往 `app/agents/prompts.py` 插入代码后，导入报 `SyntaxError: unterminated f-string literal (detected at line 75)`。`cat -A` 一看，`f"## system\n{system}..."` 里的 `\n`
    变成了真的换行。
  - 写进文件的正则里，`一-鿿` 这样的范围变成了字面上的汉字字符（`cat -A` 显示成多字节序列）。
  - heredoc 里的 Python 脚本出现 `SyntaxWarning: invalid escape sequence '\w'`、`'\['`（正则写在了非 raw 字符串里）。
  - 录制 v1 历史的脚本找不到要临时还原的代码片段：用 Python 补丁改 `journey.py` 时写入了 CRLF，脚本按 LF 文本匹配。
- **怎么查的：** 都是用 `cat -A` 看真实字节找到的。`unexpected EOF` 的根因转录里没有查，WP4 会话记进 memory 的说法是"正文里引号很多或有非 ASCII 字符时容易出现"。
- **根因：** 要插入的代码先被 shell、再被 Python 字符串字面量各解释一次，`\n`、`\u`、`\w` 和引号在其中一层就被"吃掉"或改写了；补丁脚本写入的换行符和仓库文件不一致。
- **怎么修：** 长补丁先用 Write 工具写成独立的 `.py` 文件再运行，或者直接用 Edit 工具；写完用 `python -c "import ast; ast.parse(...)"` 或直接导入检查语法；对正则这类内容，
  沿用仓库已有的写法（WP8 的 `voice_services/intents.py` 用字面的 `[一-鿿]`）；按文本匹配前统一换行符。
- **怎么证明：** 改用脚本文件或 Edit 后都成功。
- **经验：** 生成代码的代码，要检查生成结果本身（语法检查、`cat -A` 看不可见字符），不要只看"脚本没报错"。
- **范围：** 环境（工具用法）。
- **来源：** main__40bdfa74 10-08 23:46、10-09 00:24、00:26；main__21cd9296 10-09 02:53、03:24、03:27；main__dec77d7e 10-09 08:22、09:01；main__04f34599 10-09 08:00；
  main__025b79fc 10-10 00:16；wt-wp10b-ai-ops-060651 10-10 07:42、07:48、08:12；wt-dazzling-borg-298d62 10-10 18:09；wt-goofy-hermann-1dcfd7 10-10 18:55、19:07；
  wt-wp9-storyline-7d11cd 10-10 22:08、10-11 00:21；wt-wp10-roadmap-docs-6e66e7 10-11 08:00。

### 15.3 写给别人跑的命令，先在同样的环境里跑一遍

- **现象：** 用 Python 脚本往 `docs/DEPLOY.md` 写"按 IP 汇总 Caddy 访问日志"的命令。在 `ubuntu:24.04` 容器里拿一份模拟日志实际跑：
  `awk: line 1: runaway string constant "%s	%s	%s	% ...`；写文件时还有 `SyntaxWarning: invalid escape sequence '\('`。修好后再测，`TZ=Asia/Shanghai` 和 UTC 输出的时间完全一样。
- **根因：** Python 普通字符串把 awk `printf` 里的 `\t`、`\n` 变成了真正的制表符和换行，写进文档的命令已经坏了（和 15.1 同一个机制）。时区一样是因为容器里没装 `tzdata`，`TZ` 不起作用；
  装上后同一时间戳 UTC 13:33:20、上海 21:33:20。
- **怎么修：** 换成一条 `jq -rs 'group_by(.request.client_ip) | ...strflocaltime(...)'` 命令，去掉 awk；文档写明看北京时间要加 `TZ=Asia/Shanghai`。
- **怎么证明：** 在容器里用模拟日志跑出正确的按 IP 汇总和登录记录。
- **经验：** 交给别人执行的命令，自己先在相同环境里跑一遍。用一种语言生成另一种语言的代码时，转义最容易出错，优先用原始字符串或单独的文件。
- **范围：** 平台（部署文档）。
- **来源：** main__b1ffba74 10-07 14:46–14:49。
- **代码：** `docs/DEPLOY.md`。

---

## 十六、多会话并行开发

医院扩展的工作包常常由几个会话在同一台机器上并行开发。代码、数据库、端口、CPU 和磁盘都是共享资源，任何一个没隔开，就会出现"别人的改动让我的测试失败"
或"我的对照实验其实没控制住变量"。隔离的清单：代码用 worktree、测试库用自己的 `TEST_DATABASE_URL`、API 和 Vite 用自己的端口，并时刻记得 CPU 和磁盘是大家共用的。

### 16.1 并行会话共用一个工作区：测试会跑到别人没写完的代码

- **现象：** 修"夜里播种"测试的会话（5.6）准备在最终代码上做 27 个点的扫描，先跑了一次 `git status`。结果里有几个它没动过的文件：`app/ehr/gateway.py`、`config/agents/control_tower.yaml`，
  新文件 `config/tools/createFlowTask.yaml`、`explainException.yaml`。`docs/PROGRESS.md` 里也混着别人的段落。
- **怎么查的：**
  1. `ls --time-style=full-iso` 看这些文件的修改时间：都在 03:58（-0400），也就是 WP5 会话刚开始写代码的时候。WP5 会话也在同一个目录（主 checkout，不是 worktree）里工作。
  2. 判断风险：扫描和全量测试会加载这些没写完的代码，结果不能证明自己的修复；提交时也可能把别人的改动带进去。（WP5 那边 08:10 的 `git diff --stat` 里同样混着这个会话改的 4 个测试文件。）
- **根因：** 几个会话直接在同一个工作区里并行改代码，只给测试库分了名字（`ioc_test_hospital_night`、`ioc_test_wp5`），代码没有隔开。
- **怎么修：**
  1. 停掉正在跑的扫描（TaskStop）。停掉后还剩 21 个进程（bash 循环、uv、pytest、python），按命令行里的 `-p seedat` 和 `sweep.sh` 找出来，用 `Get-CimInstance Win32_Process` 加 `Stop-Process` 清掉。
  2. `git worktree add --detach <scratchpad>/wt HEAD`，只拷进自己的 6 个文件，用主目录 venv 的 python 在 worktree 里跑扫描。
  3. 每次运行把 `app.seed.__file__` 打进日志（`code=...\scratchpad\wt\apps\api\app\seed.py`），证明加载的确实是 worktree 里的代码。
  4. 在 worktree 里重跑全量测试，提交时只暂存自己的 6 个文件和 PROGRESS.md 里自己的两条，事后确认 WP5 未提交的改动还在。
- **怎么证明：** worktree 里 27/27 个播种点通过，全量 395 passed；WP5 会话 08:56 看到"对方的文件不再显示为已修改"，它的 PROGRESS diff 只剩 WP5 一节。当天晚些时候的 ICU 校准、去标识化、
  账单三个会话都在各自的 worktree 里工作。
- **经验：** 并行开发时，数据库要分开，代码也要分开（worktree）。验证前先 `git status` 看工作区里有没有不属于自己的改动，并在测试输出里打印实际加载的代码路径。
  停掉后台任务后，要检查子进程是不是真的都退出了。
- **范围：** 环境（多会话开发流程）。
- **来源：** main__04f34599 10-09 07:58–08:26；main__dec77d7e 10-09 07:55、08:10、08:56。

### 16.2 共享机器上的对照实验："在同样负载下"基线通过，其实负载已经没了

- **现象：** 一个会话的后端全套里，`test_cancellation_produces_ranked_backfill_within_5_seconds` 失败：`assert (89865.14 - 89854.765) < 5`（约 10.4 秒）。单独重跑，**还是失败**。
- **怎么查的：**
  1. 单独跑也失败，所以会话不再直接说"是负载"，而是要区分"是负载还是我的改动"。
  2. 查负载：CPU 100%，最忙的是 PID 21716，查命令行发现是另一个会话的 uvicorn，端口 9441。
  3. 设计对照：用 `git worktree add --detach` 在 scratchpad 里检出基线提交 0485ffb，"在同样负载下"跑同一个测试：通过（67.6 秒）。会话据此说"不能只怪负载"；接着在自己的分支上再跑：
     也通过（50.1 秒）。结论：失败是负载造成的。
  4. 但对照两个会话的时间线：另一个会话在 16:26:15–16:27:21 之间正好杀掉了 PID 21716；基线测试大约从 16:27:30 才开始跑，到 16:28:37 结束。也就是说，**基线那次跑的时候负载源已经没了**，
     "同样负载"这个前提不成立。
  5. 另一面：另一个会话在 16:00 把自己的 Playwright 重置超时归咎于"这个会话的后端全套"；这个会话在 16:25 把自己的计时测试失败归咎于对方的 API。两边都看到了对方，都说得部分对。
  6. 还有一个测量上的矛盾：另一个会话在 16:28 连续采样 `Win32_Processor.LoadPercentage` 得到 100、87、91、77、100，同时用 `Get-Process` 算 10 秒内各进程的 CPU 增量，最大的只有 1.5 秒。
     会话判断 "that WMI load figure was misleading"。材料里没有第三种测量来判定两者哪个对（例如 Docker 虚拟机里的 PostgreSQL 不一定出现在 `Get-Process` 的前几名里）。
- **根因：** 结论（负载导致计时失败）大概率没错，但用来支撑它的对照实验没有控制住负载这个变量。另外，这个测试的失败可能还和 5.1 的首次请求补算有关（推测，见 5.1）。
- **怎么修：** 没有改代码；这是一个方法上的教训。
- **怎么证明：** 时间线来自两个会话记录里的时间戳和 PID。
- **经验：** 在多个会话共用的机器上，"A 在负载下失败、B 在负载下通过"只有在 A 和 B 交替、紧挨着、多次运行时才有说服力；每次运行都要当场记录负载（哪个进程、多少 CPU），
  而不是在开始时看一眼。一个读数和另一个读数矛盾时，先别下结论。
- **范围：** 环境（共享开发机）。
- **来源：** wt-adoring-raman-2c26f4 10-10 16:19–16:30；wt-cool-cartwright-7673d9 10-10 16:00、16:25–16:29。

### 16.3 两个分支各自绿、合起来红：提示词门禁

- **现象：** WP8 和 WP10b 合并后，全套后端 651 个里 1 个失败：`test_prompt_gate.py::test_the_repository_prompts_pass_the_gate`：
  `call_center@1: no eval run id in the header (eval_run: evals/call_center/report.json)`，`followup_calls@1` 同样。
- **怎么查的：** 读失败信息：WP10b 新加的门禁要求每个提示词头部写明评测它的 run id，而 WP8 是并行写的，当时还没有这个门禁。
- **根因：** 两个分支各自是绿的，合在一起才违反对方的规则。
- **怎么修：** 在一个用完即删的 mock 数据库上对 WP8 的两个 agent 加 `--stamp` 重跑，把 run id 写进提示词头部；被评测脚本改写的结果文件按 14.8 的方式还原。
- **怎么证明：** 门禁、AI Ops 和注册表相关的 31 个测试通过。
- **经验：** 并行开发时，"各自绿"不等于"合起来绿"，合并后一定要跑全套。新加的门禁或守卫要提醒并行的分支（12.1 对 WP7 的交接提醒是同一个做法）。
- **范围：** 平台（WP8 + WP10b 合并）。
- **来源：** main__7a422c19 10-10 10:23–10:40。
- **代码：** `apps/api/prompts/call_center/1.md`、`apps/api/prompts/followup_calls/1.md`；提交 0485ffb。

### 16.4 LF 和 CRLF 让 `git merge-file` 报出假冲突

- **现象：** 账单会话在提交前，用 `git merge-file` 预演自己的 PROGRESS.md 和兄弟会话（去标识化）的版本能不能干净合并，结果报冲突。
- **怎么查的：** 看冲突块，内容上没有重叠。再检查三个文件的换行符（`grep -c $'\r'`、`od -c`、`diff --strip-trailing-cr`）。结论是 "`git show` gives LF, while the working copies are CRLF"：
  仓库开了 `core.autocrlf=true`，所以工作区文件是 CRLF，而 `git show` 取出的 base 是 LF。
- **根因：** 三方合并的三份输入换行符不一致，每一行都被当成不同的行。
- **怎么修：** 三个文件都转成 LF（`tr -d '\r'`）后再 `git merge-file`，结果 `exit 0 (0 = clean)`。
- **怎么证明：** 合并干净，账单那条记录落在注释后面，兄弟会话的新段落跟在后面；随后提交 d2955ff。
- **经验：** 在 Windows 上比较或合并文本时，先确认换行符一致。"整段都冲突"或"每行都不同"往往是换行符问题，不是内容问题。
- **范围：** 环境（并行会话合并 PROGRESS.md）。
- **来源：** wt-lucid-mestorf-0d5ad0 10-09 21:10–21:11。

### 16.5 App 后台检出 worktree 失败：`fatal: unable to write new index file`（未完全确认）

- **现象：** WP7 会话两次用 App 开一个新会话（各自带一个新 worktree）去修 2.6 的竞态，两次都没启动：`failed to start (Background full checkout failed: other)`，新会话连任务都没收到。
- **怎么查的：**
  1. 先查常见原因：`df -h` 看 C:/D: 剩余空间（C: 93%、D: 97%，都还有 36–38 GB），`git worktree list`，在 `.git` 下找 `*.lock` 文件，看 `core.longpaths`。都正常，也没有留下半截 worktree。
  2. 手动做同样的事：`git worktree add --detach` 一个临时目录，687 个文件全部检出成功，随后删掉。于是**先下结论"App 偶发问题"**，第二次重试（改从 `feat/hospital-platform` 切分支）。
  3. 第二次以同样的错误失败，用户也说"这个也是同样的原因"。这推翻了"偶发"的结论。
  4. 去找 App 自己的日志：`C:\Users\lenovo\AppData\Local\Claude\Logs\main.log`（要 `grep -a`，文件被当成二进制）。找到真正的 git 报错：
     `Git command failed: git -c core.longpaths=true checkout HEAD -- . :(exclude).claude { code: 128, ... stderr: 'fatal: unable to write new index file\n' ...}`，随后 App 删掉了这个 worktree。
     两次失败报错相同。
  5. 这个报错的意思是 git 要替换 index 文件时另一个进程正开着它。查 git 配置（`core.fscache=true`、`core.autocrlf=true`，没有异常）和进程：`Get-CimInstance Win32_Process` 找到
     TortoiseGit 的状态缓存 `TGitCache.exe`（pid 15824），它会扫描 `.claude\worktrees` 下的新 worktree。
  6. 按 App 的两步顺序手动复现（先 `git worktree add --no-checkout`，检出两个文件，再 `git checkout HEAD -- . ':(exclude).claude'`）：**3 次都成功**。所以 git 和仓库本身没问题，
     TGitCache 只是"最可能"的嫌疑。
  7. 对比日志里以前成功的创建：`stage1=1648ms`、`3356ms`、`5570ms`；失败那次 `stage1=6599ms`，当天机器更慢。会话据此推断是"App 启动新会话进程 / TGitCache 或杀毒扫描，
     与后台检出替换 index 撞在一起"的时间竞争，但**没有直接证据证实**。
- **根因：** **未完全确认。** 确认的是：App 后台全量检出时 `git checkout` 写 index 失败，同样步骤手动执行 3/3 成功；嫌疑是 TGitCache.exe（或杀毒软件）在机器较慢时占着 index 文件
  （修复会话的任务背景里也是这个推测）。
- **怎么修（绕过）：** 不走 App 的后台检出，手动 `git worktree add -b claude/fix-medrec-reconcile-race <dir> feat/hospital-platform`，再让新会话直接用这个目录。中间还碰到一个护栏：
  WP7 会话想直接改那个目录里的文件，被 hook 拦下（`PreToolUse:Edit hook error: This session is running in an isolated git worktree at ...wp7-documentation-c03e03, but ...fix-medrec-reconcile-race\...\service.py belongs to a different worktree.`）。
  会话没有用 shell 绕过，而是问用户，按用户选择在预建目录里开新会话，这次启动成功，修复提交为 84478f2。两次失败各留下一个空分支（`claude/sad-gates-cf1b9a`、
  `claude/fix-medrec-reconcile-race-44a919`），确认都指向 0d6814f、没有独有提交、没被检出后删除。
- **怎么证明：** 预建目录的会话正常启动并完成修复；没有做能证实 TGitCache 的实验（例如停掉 TGitCache 再让 App 建 worktree）。
- **经验：** 手动复现成功一次就判"偶发"太早了，第二次同样失败才是真信号。工具报 "other" 这类笼统错误时，先找它自己的日志拿到底层命令的原始 stderr。"手动能过、工具里不能过"
  通常指向时序或并发的外部进程（索引器、杀毒、缓存服务），可以先绕开（预建 worktree），把未证实的嫌疑如实写成嫌疑。护栏拦下操作时，问人，不要绕过。
- **范围：** 环境（App 和 git）。
- **来源：** wt-wp7-documentation-c03e03 10-10 05:20–05:35；wt-fix-medrec-reconcile-race 任务背景。

---

## 十七、遇到问题时的检查清单

1. 报错原文是什么？出现在哪个阶段（启动、请求中、teardown、评测中）？频率多少？日志里每种报错各出现几次？
2. 什么条件下出现：哪天的数据、什么负载、哪个数据库、哪个模型、是否并发、跑的是哪个版本的代码？
3. 看过现场了吗：失败快照、trace 的网络记录、`pageerror` 和 console、服务器日志里的状态码、`pg_stat_activity`、卡住进程的调用栈？
4. 能不能写一个最小脚本，直接打到出问题的那一层稳定复现？自然复现太难时，能不能造一个确定的复现（barrier、测试持有的锁、注入异常）？
5. 这是哪一类问题：代码 bug、并发、模型行为、标签或口径、环境、数据依赖日期、测试自己的假设？
6. 是新问题还是老问题？在基线提交的干净 worktree 上（或把旧版文件放回去），同一份数据、同样的测试跑过吗？
7. 机器上同时在跑什么（各进程的 CPU 增量、别的会话的 API 和 pytest、Docker 磁盘）？当场记下来了吗？"单独重跑能过"之后，还追了"是谁在慢"吗？
8. 我的假设是什么？什么实验能推翻它？（只改一个变量；记下元数据：耗时、token、结束原因、锁等待。）能不能直接把怀疑的机制关掉，看问题是否消失？
9. 做新旧对比之前，量过噪声底吗（旧代码对旧代码、交替跑几轮）？
10. 应该修在哪一层？修在这一层，会不会影响别的东西（别的模型、别的 agent、别的星期几、并行的分支）？
11. 写了"修之前失败"的测试吗？在旧代码或故意弄坏的实现上确认过它会失败吗？负面测试关掉的是唯一的途径吗？
12. 结果是不是"太好"了（满分、0 失败、0/0 达标）？评测集和规则是不是出自同一个人？
13. 被测功能有几条路径（模型 / 兜底、中文 / 英文 / 法文、离线 / Temporal）？每条都跑过吗？
14. 有没有"先查后写"？拿锁之后重读了吗？人会不会走在流程前面，在流程问之前就做完了？
15. 用脚本改了代码，看过 diff 和不可见字符（`cat -A`、控制字符扫描）吗？
16. 停掉后台任务后，子进程真的退出了吗？自己的测试库、worktree、junction 清理了吗？C: 还剩多少空间？
17. 修复的效果量过了吗（时间、成功率、分数），和修之前比了吗？
18. 有没有顺带扫一遍相邻情况（所有星期、所有 schema、所有并发路径、所有能到达同一终态的入口）？
19. 提交说明和这份手册里写下现象、原因、修复和证据了吗？没查清的写"未查明"、推测写"推测"了吗？

---

## 十八、换模型续作：同步、合并、重试实现与真实密钥验证

这一节是 2026-10-12 把"真实密钥流程测试"任务从 Opus 5.5 切到 Opus 4.8 后续作时踩的坑（过程另见 [`model-switch-2026-10-12.md`](model-switch-2026-10-12.md)）。

### 18.1 本地独有的 main 提交，让"同步 base 分支"的工具空转

- **现象**：worktree 分支要同步到 main，但 app 的 `sync_with_base_branch`（从 origin 拉 base 再合并）报 "already up to date"，什么也没带进来。
- **怎么查的**：`git rev-parse main origin/main HEAD` → `main`=d075d95，而 `origin/main`=`HEAD`=6f9a7a8；`git merge-base --is-ancestor 434acf0 origin/main` 为假（434acf0 不在 origin/main 上）。
- **根因**：换模型前的会话把 5 个提交（434acf0…d075d95）只提交在**本地 main**、没 push；`sync_with_base_branch` 的 base 来自 origin，看到的还是 6f9a7a8=自己的 HEAD，所以判定"已最新"。
- **怎么修**：本地独有提交用本地合并。HEAD 是 main 的祖先，`git merge --ff-only main` 快进到 d075d95，不产生合并提交。合并前先 `git diff --name-only 6f9a7a8 main` 确认带入的文件不含沙箱保护路径（`.claude/hooks`、`.claude/skills`、`.mcp.json`），本地 merge 才安全。
- **怎么证明**：`git log --oneline` 线性直达 d075d95，无额外合并提交。
- **经验**：worktree 的 base 同步工具只认 origin。用它还是本地 merge，先 `git rev-parse` 对比 main / origin/main / HEAD 三个 ref；本地独有提交一律本地 merge。
- **代码与提交**：无代码改动（仅同步动作）。

### 18.2 工作区不干净时安全合并，并保住未提交的 WIP

- **现象**：要同步 main，但工作区有三个文件（`config.py` / `gateway.py` / `test_gemini.py`）的未提交 WIP（不能丢），直接 merge 会被 git 拒绝或覆盖。
- **怎么查的**：`git diff --stat` 看 WIP 范围；`git diff 6f9a7a8 main -- <三文件>` 预判冲突——只有 `test_gemini.py` 会冲突（main 的 maxItems 测试与 WIP 都插在 `test_gateway_retries_after_invalid_json_then_needs_human` 之后）。
- **根因**：WIP 基于 6f9a7a8；main 的 maxItems 测试与新测试插在同一处。
- **怎么修**：不用共享的 `git stash`（多会话共用一个栈，容易弹错别人的）。改为：`git diff > scratchpad/*.patch` 备份 → `git checkout -- <三文件>` 清空 → `git merge --ff-only main` → `git apply --3way <patch>` 重打 → 手动把 `test_gemini.py` 冲突解成"两套测试都保留"。
- **怎么证明**：无冲突标记残留；两套测试并存，`uv run pytest tests/test_gemini.py` 43 passed。
- **经验**：脏工作区合并，补丁文件 + `git apply --3way` 比共享 stash 更安全可控；`--3way` 能找到祖先 blob 时会给出可解析的冲突标记。
- **代码与提交**：01c1881（换模型前 checkpoint）、0e2f903（实现）。

### 18.3 退避重试要"在调用超时内"：先把测试断言当契约逐条 trace，再写实现

- **现象（预判，没等它咬到）**：`test_retries_stop_after_four_attempts` 用 `match=r"rate or quota limit reached \(after 4 attempts\)"`，要求子串连续。若把 "(after N attempts)" 直接 append 到消息末尾，中间会隔一个 ". Google: …"，正则就不匹配。
- **怎么查的**：动 `providers.py` 前，把 `test_gemini.py` 里 5 个重试/截断测试的断言逐条手推（时钟推进、每次 attempt 的超时、`sleeps` 列表、消息如何拼）。
- **根因**：消息结构要让 hint 和 "(after N)" 相邻；退避还不能超出这次调用自己的超时预算。
- **怎么修**：`_api_error` 返回 `(prefix, detail, retryable)`，消息拼成 `f"{prefix}{suffix}{detail}"`，suffix 夹在 hint 和 Google 详情之间；`_generate` 每次 attempt 用 `self.timeout_s - (clock.now - start)` 作剩余超时，退避前再查剩余够不够（`MIN_ATTEMPT_S=5`），不够就不再试；时钟可注入（`clock or time`）让测试不真睡。
- **怎么证明**：`uv run pytest tests/test_gemini.py` 43 passed（含 5 个重试/截断测试）。
- **经验**：实现前把别人写好的测试断言当契约逐条 trace，能在写代码前就定出文案/格式/时序约束，省掉写—跑—红的来回。
- **代码与提交**：`app/llm/providers.py` 的 `GeminiProvider._api_error`/`_generate`；提交 0e2f903。

### 18.4 放在仓库外(scratchpad)的脚本 import 不到 app 包

- **现象**：从 scratchpad 跑 `setup_live_db.py`，`import app.core.config` 抛 `ModuleNotFoundError: No module named 'app'`。
- **怎么查的**：看 traceback；对比仓库里 `scripts/documentation_eval.py` 开头有 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`。
- **根因**：`python <script>` 时 `sys.path[0]` 是脚本所在目录（scratchpad），不是 cwd（apps/api），所以 `app` 包不在路径上（cwd 只有 `-m` 或交互式时才进 path）。
- **怎么修**：运行时设 `PYTHONPATH=<apps/api>`。
- **怎么证明**：重跑 DB setup 成功（迁移到 0010 + 灌种子）。
- **经验**：仓库外的一次性脚本要显式把包根加进 `PYTHONPATH` 或脚本内 `sys.path.insert`；仓库内脚本靠自身相对路径 insert。
- **代码与提交**：无（scratchpad 脚本）。

### 18.5 全量 run 里孤立失败的看板测试：先单独复跑定性，再看接口是否涉及改动面

- **现象**：`apps/api` 广测 776 passed，仅 `test_phase3.py::test_board_updates_with_new_studies_and_sign_offs` 失败：`assert (7 > 5 and 92 == (92 + 1))`——version 5→7（跳了 2）、unread 没 +1。
- **怎么查的**：(1) 读 `POST /api/backlog/simulate-completion`（`app/modules/backlog/router.py:161`）——它只按 `datetime.now()` 挑下一个预约 `scheduling.complete`，**完全不碰 LLM/gateway**；(2) 单独复跑 `pytest tests/test_phase3.py` → 29 passed（含该用例）。
- **根因**：本次改动在 provider/gateway 层，测试全走 MockProvider（对 tier/retry/thinking 是 no-op），mock 路径字节级不变——与该用例无关。单独跑全过 → 是全量 run 里的**测试间状态泄漏 flake**（前序某测试改了预约/看板状态，影响了 simulate-completion 选中的"下一个预约"）。哪个前序测试泄漏未深究，属既有问题。
- **怎么修**：不属本任务范围，列为 follow-up（未修）。
- **怎么证明**：`pytest tests/test_phase3.py` 29 passed。
- **经验**：全量 run 里孤立的失败，先单独复跑定性（flake 还是真回归）；"失败接口是否触及本次改动面"是判断相关性的快捷依据——这里接口不碰 LLM，直接排除。
- **代码与提交**：无（既有 flake，未修）。

### 18.6 Postgres/Docker 卡死挡住 DB 版 live eval：改在 provider 层做不依赖 DB 的因果验证

- **现象**：真实密钥的 2-case 文书 eval 探针在 `get_engine().connect()` 抛 `psycopg.errors.ConnectionTimeout: connection timeout expired`；几分钟前建库还正常。
- **怎么查的**：(1) traceback 不是 `AssertionError`，说明已过 `assert gw.mode=='gemini'`——**key 生效、网关是通的**，卡在 DB；(2) `netstat :5433` 只有约 8 个连接（不是连接数打满）；(3) 直连 `postgres` 库诊断也 6s 超时——Postgres 本身无响应；(4) `docker ps`/`docker system df` 也 hang（>120s）——Docker 自身卡住。符合本机"Docker 盘(C:)会满导致 Postgres 卡死"的已知 quirk。
- **根因**：环境问题（Docker/Postgres 无响应），非代码、非 key。
- **怎么修**：不擅自重启 owner 的 Docker。改走**不依赖 DB** 的验证：直接在 provider 层（不经 gateway 的 store 日志）用真实 discharge-summary synthesis prompt（`discharge_summary@1`）+ 真实 `DischargeSummary` schema + 合成住院数据，对同一输入做默认 thinking vs `thinking_level=LOW` 的因果对照调用，读 `usage_metadata` 的 `finish_reason`/`thoughts_token_count`。
- **怎么证明**：两次 live 调用都 finish=STOP、JSON 校验通过；LOW 把 thinking token 从 4498 降到 1366（约 1/3）、时延从 23.6s 降到 12.5s——直接印证 Finding 1 的机制（thinking 吃输出预算）被 LOW 缓解。
- **局限（未复现 MAX_TOKENS）**：合成病例比真实最坏情况（报告里 7676/8000 → MAX_TOKENS）轻，默认 thinking 这次只到 4498/8000、没触发 MAX_TOKENS；真实截断已记录在 [`../evals/live-run-2026-10-12.md`](../evals/live-run-2026-10-12.md)，`MAX_TOKENS`→`OutputTruncated`→重试 由单测覆盖。要真复现截断得用更重的输入或完整 DB 版 eval。
- **经验**：当"端到端"被环境（DB/Docker）挡住时，把验证下沉到**修复真正所在的那一层**（这里是 provider 的 `thinking_config`），既绕开环境，又更直接。改动面在哪，就在哪一层验证。
- **代码与提交**：探针为 scratchpad 脚本；被验证的实现在 0e2f903（`app/llm/providers.py`）。

### 18.7 截断后原样重试 = 白花一倍钱：让重试真的不一样（合并前 review 发现）

- **现象**：合并前的 review（两名验证者确认）指出——`OutputTruncated` 后网关重试的是**几乎相同**的请求：同样的 `max_output_tokens`、同样的 thinking（tier 没变），只把截断错误塞进通用的 "failed validation" 文案里。条件没变，第二次极可能再截断 → 成本翻倍却无收益。`OutputTruncated` 的 docstring 与 0e2f903 的提交说明都写了 "shorter-answer hint"，但实际并不存在那个 hint。
- **怎么查的**：读网关 `structured()` 的重试分支——`error = str(e)`（截断消息）被拼进 `f"...failed validation: {error}\nReturn JSON that matches the schema."`，`complete_json` 的 `max_tokens`/`tier` 原样传入。确认"hint"只是把错误回显，既没告诉模型写短，也没降 thinking。
- **根因**：重试没有改变导致截断的任何条件（输出预算、thinking 量），也没给模型"写短点"的指令。
- **怎么修**：网关区分"截断重试"和"校验重试"。截断重试：(a) 明确指令 "你上次在输出上限被截断(MAX_TOKENS)，请更简洁：只给 schema 要求的字段、短值、不重复"；(b) 把该次重试的 thinking 压到 `MINIMAL`（新增 `complete_json(..., thinking_level=...)` 覆盖参，provider 用它而非 tier 的档）以腾出输出预算。
- **怎么证明**：`test_gateway_retries_a_truncated_answer_once_and_logs_why_and_its_tokens` 改为断言**重试实际发了什么**——重试请求含 "Answer more concisely" / "cut off at the model's output limit"，且 `calls[1]["config"].thinking_config.thinking_level == MINIMAL`（首 call 的 thinking_config 为 None）。`uv run pytest tests/test_gemini.py` 44 passed。
- **经验**：若"重试"不改变失败的成因，就是等额再花一次钱。重试要么换条件（更小输出、更低 thinking），要么换指令；并用测试断言重试**真的**带了这些，而不只是把错误回显。
- **代码与提交**：`app/llm/gateway.py` 的 `structured()`、`app/llm/providers.py` 的 `complete_json(thinking_level=...)`；提交 0be9140。

### 18.8 `thinking_level` 只有 Gemini 3.x 认：2.5 要的是 `thinking_budget`（合并前 review 发现）

- **现象**：合并前的 review（分歧票）指出——thinking 以 `ThinkingConfig(thinking_level=...)` 发给 `GEMINI_MODEL_*` 指到的任何模型；而 `docs/PROGRESS.md`（312/322/323 行）把 `gemini-2.5-flash` 列为已验证、已定价、可按档覆盖的模型，2.5 系用的是整数 `thinking_budget`，不是 `thinking_level`。给 2.5 发 `thinking_level` 是不支持的字段。
- **怎么查的**：`grep -rn "gemini-2\.5" docs/` 确认 PROGRESS.md 确实把 2.5 当可配置模型；核对 google-genai 的 `ThinkingConfig` 同时有 `thinking_level`（3.x）和 `thinking_budget`（2.x）两个字段。
- **根因**：`_thinking_config` 不看模型名，一律发 `thinking_level`。
- **怎么修**：`_thinking_config(model, tier, override)` 按模型名分流——名字含 `gemini-2.` 的发 `thinking_budget`（MINIMAL→0、LOW→512、MEDIUM→4096、HIGH→16384），其余（3.x 及默认）发 `thinking_level`。默认 `gemini-3.5-flash` 路径不变。
- **怎么证明**：新增 `test_thinking_is_a_level_for_gemini_3_and_a_budget_for_gemini_2_5`——gemini-3.5-flash 得 `thinking_level=LOW`（budget 为 None），gemini-2.5-flash 得 `thinking_budget=512`（level 为 None）。44 passed。
- **局限**：2.5 的 budget 档位是合理映射、**未经真实 2.5 live 调用校准**（本机默认用 3.5）；真要用 2.5 建议 live 核一次。
- **代码与提交**：`app/llm/providers.py` 的 `_thinking_config`；提交 0be9140。

---

## 附录、kg-qa 子项目

`apps/kg-qa` 是从早先的 AIMed 仓库连同 git 历史一起导入的子项目（医学知识图谱问答，DeepSeek + Neo4j），不属于规格里的 21 个系统。下面三个问题发生在 2026-08，
早于本套件，只有提交说明，没有会话记录，所以"怎么查的"只写到提交说明记下的程度。

### A.1 生成中删除的历史被随后写回的 checkpoint "复活"

- **现象：** 用户点"新建对话"时删除旧 thread 的历史，如果这时回答还在生成，被删的历史会被生成结束时写回的 checkpoint 重新建出来，变成永久孤儿：thread_id 已经从 session 轮换掉，以后没人能再删。
- **怎么查的：** 提交说明记下的分析：客户端 abort 拦不住服务端，Starlette 只在下次写 chunk 时才发现连接断了，而 agent 卡在工具步骤时几秒都不产出 chunk。
- **根因：** 删除和"正在进行的写入"之间没有协调。
- **怎么修：** 按 thread 计数登记正在生成的运行；reset 遇到正在生成的就登记"待删"，等生成结束（正常、异常、客户端断开都走 finally）再删。轮换 session_id 而不是复用，保证清理失败时
  新对话也不会带上旧上下文；只在后端返回成功后才清空界面。
- **怎么证明：** 测试 58 项通过。已知限制：仍有一个亚毫秒窗口（请求已捕获旧 thread_id 但生成器尚未首次迭代）；会话历史没有 TTL。
- **经验：** 删除一个"还有人在写"的东西，要么等写完再删，要么让写入方知道它已被删除；客户端断开不等于服务端停止。
- **范围：** kg-qa。
- **代码：** `apps/kg-qa/src/backend/chat_service.py`；提交 bfb78c8（"历史清理延后（chat_service）"）。

### A.2 流式输出里剥除语言标记：标记会被切到多个 token 块

- **现象：** 界面指定回答语言时，在用户消息末尾加了机读标记 `(Answer-Language: xx)`，模型可能把它带进输出；流式输出时这个标记会被切到多个 token 块里，逐块过滤去不掉。
- **怎么修：** 流式输出做尾部缓冲，兜底剥除标记。另外工具返回的提示和错误文案改成英文，由模型翻译成回答语言，避免中文文案直接漏进英文回答。
- **经验：** 对流式输出做字符串过滤时，要假设目标字符串会跨块出现，留一段尾部缓冲再判断。
- **范围：** kg-qa。
- **代码：** 提交 e978bc5。

### A.3 聊天界面的一组前端 bug

- **现象与原因**（提交说明只记了结论，没有排查过程）：
  - 流式回答被读屏软件每秒重复朗读约 60 次：`aria-busy` 加在气泡上，盖不住祖先的 live region；
  - 组件上的 `display` 盖掉了 `[hidden]`，欢迎区永不消失；
  - 中文输入法选词时按回车误发送；
  - 中止生成会丢掉最后一个分片；
  - 重试会重复插入用户气泡；
  - 主题切换时带 transition 的颜色停在旧主题的值；
  - `.btn-danger` 悬停用 `brightness()` 把白字对比度压到 4.09:1。
- **经验：** `[hidden]` 会被显式的 `display` 覆盖；输入框回车要判断 `isComposing`；无障碍属性要放在 live region 本身；视觉效果改动后要重新算对比度。
- **范围：** kg-qa。
- **代码：** 提交 bfb78c8（"同时修复"一节）。

### A.4 其他小记

| 问题 | 处理 | 来源 |
| --- | --- | --- |
| import 时建 MySQL 表的副作用 | 改成显式 `init_db()` | 提交 319e9d9 |
| 冷启动 `create_mysql_db` 的 port/tag 崩溃；在线写回缺 NOT NULL 的 id；流式过滤让中间叙述漏出 | 同一提交里修复 | 提交 319e9d9 |

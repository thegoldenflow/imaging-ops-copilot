# 模型切换过程记录（2026-10-12）

本文件记录"真实密钥流程测试"这一任务在**模型切换前后**分别发生的事，供 owner 核对
"模型切换是否影响了工作质量"。只做记录，不改代码。

任务目标：让 Gemini 的 LLM 调用路径在**真实密钥 live run** 下更稳健。工作分支
`claude/recursing-chaum-8379cb`（git worktree）。

交叉引用：
- 真实密钥 live run 报告：[`../evals/live-run-2026-10-12.md`](../evals/live-run-2026-10-12.md)
- 调试案例库（中文）：[`debugging-casebook.md`](debugging-casebook.md)

> **关于"哪个模型"**：按各提交的 `Co-Authored-By` 署名，**换模型前**的会话署名为
> `Claude Opus 5.5`，**换模型后**署名为 `Claude Opus 4.8`。即本次是从 Opus 5.5 切到
> Opus 4.8（版本号方向是 5.5 → 4.8）。owner 的交接说明只说"切到 claude-opus-4-8"，
> 未点名切换前的模型；"Opus 5.5"这一名字来自提交署名，在此如实标注。

> **证据口径**：凡标【已核实】的，均已用 `git` 或实际读取文件确认；凡标【父会话转述】
> 或【未核实】的，是来自父会话交接留言或尚未亲自验证的过程描述，按原样记录，不夸大。

---

## ① 背景与任务

- 这是医学影像运维 copilot（imaging-ops-copilot）。本次任务由父会话（标题"真实密钥流程
  测试"）发起，目标是把 Gemini 走 Vertex AI 的真实调用路径做稳。
- 基线：`origin/main` 停在合并提交 `6f9a7a8`（"Merge pull request #4: hospital platform
  extension, phases 6-8"）。【已核实：`git log origin/main` 顶端为 `6f9a7a8`】
- 中途一次 **API 安全分类器误报（safety-classifier false positive）** 中断了上一个模型的
  回合（与工作内容无关）。随后 **owner 把会话模型切到 `claude-opus-4-8`**，由切换后的
  模型继续。
- owner 现在担心"模型切换影响质量"，要求把换模型前后"分别做了什么 / 遇到什么问题 /
  采取了什么措施"记下来。

---

## ② 换模型前（署名 `Claude Opus 5.5` 的会话）

这一阶段的提交时间集中在 2026-10-12 **03:24–04:57**（-0400）。【已核实：`git log` 的提交时间】

### 2.1 真实密钥 live run（已提交）

- **做了什么**：用真实 Gemini 密钥（Agent Platform key，三档都用 `gemini-3.5-flash`，
  `LLM_TIMEOUT_S=30`）在 `main`=`6f9a7a8` 上第一次把整套 suite 跑了一遍：每个 agent 的
  eval 集、平台套件、语音服务 eval、影像 app 流程、12 步住院故事线。全部为合成数据。
- **产出**：写入 [`evals/live-run-2026-10-12.md`](../evals/live-run-2026-10-12.md)，并更新
  `docs/PROGRESS.md`。
- **提交**：`376361b`（"docs: first real-key run on Gemini (2026-10-12)"）。
  【已核实：`git log -- evals/live-run-2026-10-12.md` 命中 `376361b`；该提交同时改
  `docs/PROGRESS.md`】
- **这次 run 暴露的主要问题**（见报告 Findings 节，逐条）：
  1. **Finding 1 — 思考（thinking）吃满输出预算**：Gemini 把 thinking token 计入
     `max_output_tokens`。文书综合调用里 8,000 token 花了 7,676 在思考上，可见输出只有
     309 token，以 `MAX_TOKENS` 结束，截断的 JSON 连续两次解析失败。默认思考还连带造成
     大量超时（全量 run 约 9%、语音 eval 15–18%，30s 截止）、电话 agent 平均回复 4.7s、
     故事线第 9 步超时。用 `thinking_level=LOW` 复测同一文书病例，6 次调用全部 3–9s 完成。
  2. **Finding 2 — 大 `maxItems` 被拒**（见 2.2，已修）。
  3. **Finding 3 — 高并发下 429**：2–3 个进程同时调用时，Vertex 返回
     `429 RESOURCE_EXHAUSTED`；故事线患者的胸片（ST-DEMO1）因此失败两次。建议对 429/504
     做指数退避重试。
  4. **Finding 4 — 空转成本**：API 仅运行时，住院模拟的 order review / medication
     reconciliation 自己会调模型，约 **$4/小时**；只有 passcode，没有调用次数上限。
  5. Finding 5–7：eval harness 健壮性、开发库 `ioc` 落后 4 个迁移（0006/0010）、若干
     低于基线的模型结果。

### 2.2 修复：Gemini 结构化输出 schema 去掉大 `maxItems`（已提交）

- **问题**：Vertex AI 对 `response_json_schema` 中**对象数组**上的大 `maxItems` 返回
  `400 INVALID_ARGUMENT`。隔离实验（casebook 案例 3.1）确认：**`maxItems` 40、60 → 400；
  改成 10 → 成功**；`maxLength`、`minimum/maximum` 单独保留都能过。受影响的是用药核对
  （medication reconciliation）与 3 个文书 agent（出院小结、患者指导、护理交接）。
  > 口径差异提示：父会话"已知事实"写的是"maxItems=40 失败，10 可过"；仓库里提交信息和
  > casebook 更完整，写的是"40、60 不行，10 可以"。以仓库为准。
- **修法**：`GeminiProvider` 发送前去掉 `maxItems`（函数 `gemini_schema`）；网关仍用
  **完整的 Pydantic 模型**校验回答，超限的列表走校验失败重试（retries once），约束不丢。
- **证明**：单测 `test_schema_sent_to_gemini_has_no_item_limits_and_the_gateway_still_enforces_them`
  （`apps/api/tests/test_gemini.py`）；真实调用里受影响的 5 个 schema 全部从 400 变成合法
  回答，故事线的用药核对步骤随之通过。
- **提交**：`434acf0`（"fix(llm): send Gemini structured-output schemas without maxItems"），
  改 `apps/api/app/llm/providers.py`（+14）、`apps/api/tests/test_gemini.py`（+17）。
  【已核实：`git show --stat 434acf0`】

### 2.3 调试案例库与 CLAUDE.md 工作流（已提交）

- **做了什么**：把开发/测试/运行过程中遇到的非平凡问题写成中文调试案例库
  [`docs/debugging-casebook.md`](debugging-casebook.md)，并在 `docs/README.md` 登记。
- **提交**：
  - `f9b1a62`（"docs: debugging casebook, the problems met so far and how each was solved"）——
    首版，`docs/debugging-casebook.md` +436 行、`docs/README.md` +1。
  - `87ef532`（"docs(claude): record every fixed problem in the debugging casebook"）——
    在 `CLAUDE.md` 加工作流第 6 步："每修一个非平凡 bug / 诊断一次失败都要进 casebook"，`CLAUDE.md` +1。
  - `d075d95`（"docs: casebook covers the whole project, from the session transcripts"）——
    casebook 扩到 **111 个案例**（84 新增、20 个补充调查过程），`docs/debugging-casebook.md`
    +2434/−137。
  【以上均已核实：`git show --stat`】
- casebook 案例 3.1（`maxItems`，提交 `434acf0`）与 MAX_TOKENS/thinking 案例，与 live run
  报告的 Finding 2、Finding 1 互相对应。

### 2.4 下一批稳健性改造（**换模型前未提交**，留在工作区）

- **做了什么（未提交）**：开始按 live run 的 Finding 1/3 做下一批改造——
  - 按 tier 设置 thinking level；
  - 把 `MAX_TOKENS` 结束作为**独立错误**；
  - 带退避（backoff）的重试。
  写好了**测试、`config.py`、`gateway.py`**，但**没写 `providers.py` 的实现**。
  【部分可从 `01c1881` 的内容反推：见 ④/⑤。"未写 providers.py"已由 `git show 01c1881:...providers.py`
  核实——该提交里 `providers.py` 还是旧版，没有新类】
- **未推送**：上面这些提交当时**只在本地**，没有 push。`origin/main` 仍停在 `6f9a7a8`。
  【已核实：`origin/main` = `6f9a7a8`】

---

## ③ 模型切换

- **触发原因**【父会话转述】：上一个模型的回合被 **API 安全分类器误报
  （safety-classifier false positive）** 中断，与工作内容无关。此处如实标注为父会话转述，
  不做任何放大或额外推断。
- **措施**：owner 将会话模型切换为 `claude-opus-4-8`，让工作继续。
- **时间线旁证**：换模型前的提交集中在 03:24–04:57；换模型后的 checkpoint 提交 `01c1881`
  在 **11:41**。两段之间有明显时间间隔，与"中途中断 + 切换"一致。【已核实：提交时间】

---

## ④ 换模型后（本会话，署名 `Claude Opus 4.8`）

### 4.1 同步 worktree：本地独有的 5 个提交

- **问题**：`main` 上那 5 个提交（`434acf0`、`376361b`、`f9b1a62`、`87ef532`、`d075d95`）
  是**本地独有**的，`origin/main` 没有（仍是 `6f9a7a8`）。因此 app 的 host 同步工具
  `sync_with_base_branch`（它从 origin 拉取）在这里不适用。
- **措施**：改用本地 `git merge --ff-only main`，把分支从 `6f9a7a8` 快进到 `d075d95`。
- **证明**：当前分支历史是从 `6f9a7a8` 一路线性到 `d075d95`→`01c1881`，无额外合并提交，
  与 ff-only 一致。【已核实：`git log --oneline` 线性历史；`origin/main` 仍 `6f9a7a8`】

### 4.2 脏工作区下的安全合并与冲突解决

- **做法**【过程为父会话/本会话转述，末态已核实】：为了在"工作区不干净"（有 2.4 的未提交
  WIP）时安全合并，先用 `git diff` 把未提交 WIP 存成补丁到 scratchpad，清空这三个文件，
  合并 `main`，再用 `git apply --3way` 重新打上。
- **冲突**：`apps/api/tests/test_gemini.py` 因为 `main` 的 `maxItems` 测试和新测试插在同一处
  而冲突，**手动解决为"两套测试都保留"**。
- **末态证明**【已核实】：现在 `test_gemini.py` 里**两套测试并存**——既有 `maxItems` 的
  `test_schema_sent_to_gemini_has_no_item_limits_and_the_gateway_still_enforces_them`，
  也有新加的 thinking / MAX_TOKENS / retry 测试（`test_thinking_level_is_sent_per_tier`、
  `test_max_tokens_finish_is_its_own_error`、
  `test_rate_limits_and_gateway_timeouts_are_retried_with_backoff`、
  `test_retries_stop_after_four_attempts`、`test_retries_stay_inside_the_call_timeout`、
  `test_request_errors_are_not_retried` 等）。

### 4.3 核实依赖与配置假设（全部【已核实】）

在动 `providers.py` 实现前，核对了这些假设：

- `google-genai` **2.28.0**：`apps/api/pyproject.toml` 写 `"google-genai>=2.28.0"`，
  `uv.lock` 固定 `version = "2.28.0"`。
- `types.ThinkingLevel`（`HIGH`/`MEDIUM`/`LOW`/`MINIMAL`）、`ThinkingConfig(thinking_level=...)`、
  config 的 `model_copy`、`HttpOptions.retry_options`：在 `test_gemini.py` 中确有使用
  （如 `types.ThinkingLevel.HIGH/LOW/MINIMAL`、`config.thinking_config.thinking_level`、
  `http_options.retry_options`）。
- `config/models.yaml` 有 **`gemini-reasoning`**（tier: reasoning），另有 `gemini-fast`
  （fast）、`gemini-voice`（voice）。
- `apps/api/app/core/config.py` 从环境读 **`GEMINI_THINKING_REASONING/FAST/VOICE`**
  得到每档 thinking level（`gemini_thinking_reasoning/fast/voice`）；合法值
  `GEMINI_THINKING_LEVELS = ("MINIMAL","LOW","MEDIUM","HIGH","DEFAULT")`，`DEFAULT` 表示
  不下发、由模型用自己的默认。
- 测试里默认的 embedded agent 是 **fast tier、`max_output_tokens == 2000`**
  （`test_gateway_sends_the_thinking_level_of_the_agents_model_tier`）。

### 4.4 应 owner 要求提交"换模型前"的 checkpoint

- **做了什么**：把 2.4 的那批 WIP（测试 + `config.py` + `gateway.py`）作为"换模型前"的
  checkpoint 提交。
- **提交**：`01c1881`（"wip(llm): pre-model-switch checkpoint — thinking level,
  MAX_TOKENS, retry"），改 `apps/api/app/core/config.py`（+17）、
  `apps/api/app/llm/gateway.py`、`apps/api/tests/test_gemini.py`（+166）；
  **不含 `providers.py`**。【已核实：`git show --stat 01c1881`】
- 该提交的 message **明确写明**：`providers.py` 的实现尚未完成，**此刻后端无法干净 import**，
  这是切到 `claude-opus-4-8` 之前那批工作的一个有意的 checkpoint，实现与真实密钥重跑在它之后继续。
  - **客观核实**：在 `01c1881` 处，`gateway.py` 第 40 行 `from app.llm.providers import (... OutputTruncated ...)`
    并以 `complete_json(..., tier=...)` 调用；而 `01c1881` 的 `providers.py` 既没有
    `OutputTruncated` 类、`complete_json` 也没有 `tier` 形参 → 必然
    `ImportError`/`TypeError`，"无法干净 import"属实。【已核实：`git show 01c1881:...gateway.py`
    与 `git show 01c1881:...providers.py` 对比】
  - 说明：`01c1881` 的作者署名虽是 `Claude Opus 4.8`（提交动作由换模型后的会话完成），但其
    **代码内容是换模型前（Opus 5.5）产出的 WIP**。

### 4.5 尚未完成（换模型后仍在进行）

- `apps/api/app/llm/providers.py` 的实现：thinking / tier / `OutputTruncated` / retry+backoff /
  clock 注入。**目前正在工作区进行、尚未提交**。
  - 【已核实：当前工作区 `providers.py` 有未暂存改动，`git diff --stat` 为
    `+77/−25`（102 行）】已加入 `import random`、`class OutputTruncated(InvalidOutput)`、
    `InvalidOutput` 携带失败那次的 token 数、`GeminiProvider` 的 `thinking`/`clock` 参数与
    重试常量（`MAX_ATTEMPTS = 4`、`RETRYABLE_CODES = {429,500,502,503,504}`、
    退避含 jitter、`MIN_ATTEMPT_S = 5.0`），并给各 provider 的 `complete_json` 加了 `tier` 形参。
  - 设计演进：live run Finding 3 原建议用 SDK 的 `HttpRetryOptions`；当前实现改为
    **在调用自身超时内做 provider 级重试**（测试
    `test_vertex_client_uses_agent_platform_configuration` 断言
    `http_options.retry_options is None  # the provider retries, inside the call's timeout`）。
- **跑单测**：本（写文档的）会话**尚未运行 pytest**，不对当前工作区的测试结果作任何断言。
- **真实密钥 live 重跑**：尚未进行。

---

## ⑤ 质量连续性检查点（供 owner 核对切换是否掉质量）

### 关键提交清单（分支 `claude/recursing-chaum-8379cb`，时间顺序）

| 提交 | 标题 | 署名 | 阶段 |
| --- | --- | --- | --- |
| `6f9a7a8` | Merge pull request #4: hospital platform extension | （无 Claude 署名） | 基线（= `origin/main`） |
| `434acf0` | fix(llm): send Gemini structured-output schemas without maxItems | Claude Opus 5.5 | 换模型前 |
| `376361b` | docs: first real-key run on Gemini (2026-10-12) | Claude Opus 5.5 | 换模型前 |
| `f9b1a62` | docs: debugging casebook, the problems met so far... | Claude Opus 5.5 | 换模型前 |
| `87ef532` | docs(claude): record every fixed problem in the debugging casebook | Claude Opus 5.5 | 换模型前 |
| `d075d95` | docs: casebook covers the whole project... | Claude Opus 5.5 | 换模型前 |
| `01c1881` | wip(llm): pre-model-switch checkpoint — thinking level, MAX_TOKENS, retry | Claude Opus 4.8 | 换模型后提交（内容为换模型前 WIP） |

【以上 SHA、标题、署名均已核实：`git log --format` 含 `Co-Authored-By` trailer】

### 当前测试 / 可编译状态

- `6f9a7a8`：PR #4 合并后的基线。
- `434acf0`：带 `maxItems` 单测，提交信息称该修复下 `test_gemini.py` 32 passed（引自 live run 报告，
  **本会话未复跑**）。
- `01c1881`：**后端无法干净 import**（已客观核实，见 4.4）——这是有意的 checkpoint，不是回归。
- **当前工作区（HEAD=`01c1881` + 未暂存的 `providers.py`）**：`providers.py` 的实现正在补齐
  `01c1881` 缺的那部分（`OutputTruncated`、`tier`、retry 等）。**本会话未运行 pytest，
  不声称其能 import 或测试通过。**

### 未完成项清单

- [ ] `apps/api/app/llm/providers.py` 实现收尾（thinking / tier / `OutputTruncated` /
      retry+backoff / clock 注入）——工作区进行中，未提交。
- [ ] 跑 `apps/api` 单测（`uv run pytest -q`）确认后端恢复可干净 import、全绿。
- [ ] 真实密钥 live run 重跑，验证 Finding 1（thinking/`MAX_TOKENS`）与 Finding 3（429 退避）
      确实改善。
- [ ] 把本地独有的提交与后续实现 push 到 `origin`（当前 `origin/main` 仍 `6f9a7a8`）。

### 连续性小结

- 换模型前后的工作是**同一条线**：live run 发现问题（`376361b` + 报告）→ 先修 `maxItems`
  （`434acf0`）→ 落 casebook 与工作流（`f9b1a62`/`87ef532`/`d075d95`）→ 开始 thinking/
  MAX_TOKENS/retry 改造（WIP）。换模型后先把这批 WIP 以 `01c1881` 定点，再核实依赖、
  继续补 `providers.py`。
- 换模型这一步本身**未改变技术方向或数据模型**，只是把一次被安全分类器误报中断的会话接续下去。
- 目前**唯一未完成的实现**是 `providers.py`，且其测试尚未在本会话跑过——这一点在 `01c1881`
  的 message、本文件 ④/⑤ 中均如实标注，未声称已完成。

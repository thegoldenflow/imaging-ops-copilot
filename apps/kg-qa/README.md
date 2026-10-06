# smart_medical —— 智慧医疗知识图谱问答

基于「知识图谱 + LangGraph Agent」的医疗问答 demo。FastAPI 暴露 `/chat` 流式接口，Agent（DeepSeek）通过工具查询 Neo4j 医疗知识图谱：

- `entity_alignment`：把用户提到的实体对齐到图谱标准实体（PostgreSQL 同义词表 + Chroma 向量检索）
- `check_syntax_error`：用 LLM 校验生成的 Cypher
- `neo4j_query`：**只读**执行 Cypher 查询

离线管道 `src/datasync` 负责清洗数据、导入 Neo4j、DBSCAN 实体对齐并写入 PostgreSQL / Chroma。

**数据存储**：Neo4j（知识图谱）+ 单个 PostgreSQL（实体映射表 `entity_mapping` **与** LangGraph 对话记忆，不同表共库）+ Chroma（本地文件向量库）。

> ⚠️ 本项目为 demo。上线前仍有安全/合规待办，见 `.claude/plans/bug-shimmering-lobster.md`（B/C 部分：完整鉴权、HTTPS 反代、医疗合规备案、评测等）。

## 目录结构
```
src/
  backend/        FastAPI 应用（app.py / chat_service.py / schemas.py / templates/）
  agent/          LangGraph agent（__init__.py / tools_def.py / prompts.py / schema.py）
  configuration/  配置与连接（config.py 读环境变量 / dependency.py 惰性连接）
  datasync/       离线数据管道（data_prepare.py / entity_alignment.py）
data/             知识图谱与标注数据
  terminology/   小规模、可审核的中英文术语 fixture
pretrained/       嵌入模型权重（需自行下载，见下）
```

## 依赖服务
- Python 3.12（**无需 GPU**，CPU 即可）
- Neo4j 5.26（Docker）— 作用域子查询语法要求 5.23+
- PostgreSQL 16（Docker）— 实体映射表 + 对话记忆
- DeepSeek API Key
- 嵌入模型 `BAAI/bge-base-zh-v1.5`（本地 CPU 运行）

## 1. 起后端组件（Docker）
```bash
cp .env.example .env      # 先填好 .env（见下方「配置」）
docker compose up -d
docker compose ps         # 等 neo4j / postgres 变 healthy（首启约 30-60s）
```
compose 会自动创建 Postgres 库 `smart_medical`，无需手动建库。

## 2. 安装 Python 依赖 + 模型
```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
# 无 GPU：Windows 上 `pip install torch` 默认即 CPU 版；
# Linux/容器请用 CPU 版：pip install torch --index-url https://download.pytorch.org/whl/cpu

# 下载嵌入模型到 pretrained/bge-base-zh-v1.5
pip install -U "huggingface_hub[cli]"
huggingface-cli download BAAI/bge-base-zh-v1.5 --local-dir pretrained/bge-base-zh-v1.5
```

## 配置（.env）
```bash
python -c "import secrets;print(secrets.token_urlsafe(48))"   # 生成 SESSION_SECRET_KEY
```
关键项：`DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`(默认 deepseek-chat)、`NEO4J_PASSWORD`、`POSTGRES_URI`/`POSTGRES_PASSWORD`、`SESSION_SECRET_KEY`。
`POSTGRES_URI` 与 compose 的 `POSTGRES_*` 必须一致；`NEO4J_URI` 指向 `neo4j://localhost:7687`。

Neo4j 建议单独创建只读账号供在线查询使用（纵深防御；`routing_=READ` 已能在服务端挡写）：
```bash
docker exec -it sm_neo4j cypher-shell -u neo4j -p "<你的NEO4J_PASSWORD>" \
  "CREATE USER readonly SET PASSWORD 'your-readonly-pass' CHANGE NOT REQUIRED; GRANT ROLE reader TO readonly;"
```
并在 `.env` 设 `NEO4J_READONLY_USER=readonly` / `NEO4J_READONLY_PASSWORD=...`（不设则回退主账号）。

## 3. 离线：构建图谱与索引（首次/更新数据时）
> `data/knowledge_graph/medical_kg.jsonl` 不在仓库里（已从 git 历史中移除，并加入 `.gitignore`）。建库前需自行把该文件放到这个路径。

```bash
cd src
python -m datasync.data_prepare
```
会建 PostgreSQL 表、清空并导入 Neo4j、创建 Chroma 向量索引（余弦距离）。CPU 下全量建库约几分钟。

## 4. 运行后端
```bash
cd src
uvicorn backend.app:app --host 127.0.0.1 --port 8000
# 或： python -m backend.app
```
- 健康检查：`GET http://127.0.0.1:8000/healthz`
- 前端页面：`http://127.0.0.1:8000/`
- 公网部署：置于 HTTPS 反向代理（Nginx/Caddy）之后，并设 `SESSION_HTTPS_ONLY=true`

## 冒烟自检
```bash
cd src
python -m compileall -q backend agent configuration datasync   # 语法检查
curl http://127.0.0.1:8000/healthz                              # 期望 {"status":"ok",...}
```
在页面提问「糖尿病可以吃什么药」验证：实体对齐 → Cypher 校验 → 只读查询 → 自然语言回答；尝试诱导删库的输入应被拒绝。

## Phase 1 中英文实体支持（增量、非破坏性）

Neo4j 仍以现有中文实体和事实为唯一知识图谱。英文支持发生在实体对齐层：

```
英文术语 → PostgreSQL 审核过的英文别名 → 中文标准实体 → 原有 Neo4j 查询 → 英文回答
```

仓库只附带 6 个用于验证架构的小规模术语样例：头痛、恶心、高血压、糖尿病、偏头痛、神经内科。它们位于 `data/terminology/bilingual_medical_sample.json`，不代表完整或已经完成临床术语治理的数据集。

先执行仅 PostgreSQL 的确定性别名迁移：

```bash
cd src
python -m datasync.bilingual_terminology --skip-vector
```

该命令只增量迁移 `entity_mapping` 并导入 fixture，不连接、不清空、不重建 Neo4j；可以安全重复执行。不要为了添加英文别名运行 `datasync.data_prepare`，因为后者是首次建库用的破坏性全量流程。

确定性映射是英文检索的第一优先级。若要让 fixture 之外的英文表达使用语义回退，应先在离线评测中选定一个 SentenceTransformer 兼容的多语种模型，将其下载到本地，然后配置：

```dotenv
MULTILINGUAL_EMBEDDING_MODEL_PATH=/absolute/path/to/local-multilingual-model
MULTILINGUAL_VECTOR_COLLECTION=smart_medical_multilingual_v1
```

再幂等执行：

```bash
cd src
python -m datasync.bilingual_terminology
```

多语种向量使用独立、带版本号的 Chroma collection，不会替换现有 `bge-base-zh-v1.5` 或中文 `smart_medical` collection。中文查询继续走原来的中文模型；英文查询只在审核过的别名未命中时才查询多语种索引。向量推断产生的缓存会标为 `is_reviewed=0`，不能自动升级为权威术语。

## 安全默认值
- 所有密钥/凭证走环境变量，代码无硬编码
- `/chat` 限流（`CHAT_RATE_LIMIT`）+ 输入长度上限（`MAX_MESSAGE_LENGTH`）
- Neo4j 在线查询强制只读（READ 路由 + 写关键字拦截；建议配合只读账号）
- 前端 DOMPurify 默认白名单（不放开 iframe），页面含医疗免责声明

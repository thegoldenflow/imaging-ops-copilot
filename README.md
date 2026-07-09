# smart_medical —— 智慧医疗知识图谱问答

基于「知识图谱 + LangGraph Agent」的医疗问答 demo。FastAPI 暴露 `/chat` 流式接口，Agent（DeepSeek）通过工具查询 Neo4j 医疗知识图谱：

- `entity_alignment`：把用户提到的实体对齐到图谱标准实体（MySQL 同义词表 + Chroma 向量检索）
- `check_syntax_error`：用 LLM 校验生成的 Cypher
- `neo4j_query`：**只读**执行 Cypher 查询

离线管道 `src/datasync` 负责清洗数据、导入 Neo4j、DBSCAN 实体对齐并写入 MySQL / Chroma。

> ⚠️ 本项目为 demo。上线前仍有安全/合规待办，见 `.claude/plans/bug-shimmering-lobster.md`（B/C 部分：完整鉴权、HTTPS 反代、医疗合规备案、评测等）。

## 目录结构
```
src/
  backend/        FastAPI 应用（app.py / chat_service.py / schemas.py / templates/）
  agent/          LangGraph agent（__init__.py / tools_def.py / prompts.py / schema.py）
  configuration/  配置与连接（config.py 读环境变量 / dependency.py 惰性连接）
  datasync/       离线数据管道（data_prepare.py / entity_alignment.py）
data/             知识图谱与标注数据
pretrained/       嵌入模型权重（需自行下载，见下）
```

## 依赖服务
- Python 3.12
- Neo4j 5.23+（作用域子查询语法要求）
- MySQL 8（实体映射表）
- PostgreSQL（对话记忆 checkpointer；可选，留空则用进程内内存）
- DeepSeek API Key
- 嵌入模型 `BAAI/bge-base-zh-v1.5`

## 安装
```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
# GPU 用户：按 https://pytorch.org 安装对应 CUDA 版 torch

# 下载嵌入模型到 pretrained/bge-base-zh-v1.5
pip install -U "huggingface_hub[cli]"
huggingface-cli download BAAI/bge-base-zh-v1.5 --local-dir pretrained/bge-base-zh-v1.5
```

## 配置
```bash
cp .env.example .env      # 填入真实值
python -c "import secrets;print(secrets.token_urlsafe(48))"   # 生成 SESSION_SECRET_KEY
```

Neo4j 建议单独创建只读账号供在线查询使用（防止提示注入写库）：
```cypher
CREATE USER readonly SET PASSWORD 'your-readonly-pass' CHANGE NOT REQUIRED;
GRANT ROLE reader TO readonly;
```

## 离线：构建图谱与索引（首次/更新数据时）
```bash
cd src
python -m datasync.data_prepare
```
会建 MySQL 表、清空并导入 Neo4j、创建 Chroma 向量索引（余弦距离）。

## 运行后端
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

## 安全默认值
- 所有密钥/凭证走环境变量，代码无硬编码
- `/chat` 限流（`CHAT_RATE_LIMIT`）+ 输入长度上限（`MAX_MESSAGE_LENGTH`）
- Neo4j 在线查询强制只读（READ 路由 + 写关键字拦截；建议配合只读账号）
- 前端 DOMPurify 默认白名单（不放开 iframe），页面含医疗免责声明

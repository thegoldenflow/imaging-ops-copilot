import os
import logging
from pathlib import Path

import dotenv

dotenv.load_dotenv()  # 加载 .env（LangSmith key、数据库/密钥等），见 .env.example

logger = logging.getLogger(__name__)


def _get_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


# --------- 数据库配置（全部走环境变量，禁止硬编码密码） ---------
# 单一 PostgreSQL：同时承载实体映射表(entity_mapping) 与 LangGraph 对话记忆(checkpointer)，
# 二者是不同表、共用一个库。留空则记忆回退 InMemory，但实体对齐仍需要可用的 Postgres。
POSTGRES_URI = os.getenv(
    "POSTGRES_URI", "postgresql://postgres:postgres@localhost:5432/smart_medical"
)

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# 管理员账号：仅离线管道（建库/导入）使用
NEO4J_CONFIG = {
    "uri": NEO4J_URI,
    "auth": (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
}

# 只读账号：在线 /chat 查询路径使用（未单独配置时回退到主账号，但强烈建议单独建只读用户）
NEO4J_READONLY_CONFIG = {
    "uri": NEO4J_URI,
    "auth": (
        os.getenv("NEO4J_READONLY_USER", os.getenv("NEO4J_USER", "neo4j")),
        os.getenv("NEO4J_READONLY_PASSWORD", os.getenv("NEO4J_PASSWORD", "")),
    ),
}

# --------- LLM 模型（DeepSeek，可配置）---------
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
CYPHER_CHECKER_MODEL = os.getenv("CYPHER_CHECKER_MODEL", DEEPSEEK_MODEL)

# --------- 会话/安全 ---------
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "")
SESSION_HTTPS_ONLY = _get_bool("SESSION_HTTPS_ONLY", False)  # 生产（反代 HTTPS 后）置 True

# --------- 运行参数 ---------
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "auto")  # auto / cpu / cuda
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "2000"))
CHAT_RATE_LIMIT = os.getenv("CHAT_RATE_LIMIT", "20/minute")
NEO4J_QUERY_TIMEOUT = float(os.getenv("NEO4J_QUERY_TIMEOUT", "15"))
NEO4J_MAX_ROWS = int(os.getenv("NEO4J_MAX_ROWS", "50"))
# 在线实体对齐：Chroma 余弦距离阈值（distance = 1 - cos_sim），超过则判为不匹配返回 None
ENTITY_ALIGN_MAX_DISTANCE = float(os.getenv("ENTITY_ALIGN_MAX_DISTANCE", "0.35"))
AGENT_RECURSION_LIMIT = int(os.getenv("AGENT_RECURSION_LIMIT", "25"))

# --------- 路径配置 ---------
ROOT_DIR = Path(__file__).parent.parent.parent
WEB_STATIC_DIR = ROOT_DIR / "src" / "backend" / "templates"
EMBEDDING_MODEL_PATH = ROOT_DIR / "pretrained" / "bge-base-zh-v1.5"
VECTOR_STORE_DIR = ROOT_DIR / "data" / "vectorstore"

# --------- 其他开关 ---------
AGENT_WITH_MEMORY = _get_bool("AGENT_WITH_MEMORY", True)
AGENT_STREAM_OUTPUT = _get_bool("AGENT_STREAM_OUTPUT", True)


def resolve_device(preferred: str = None) -> str:
    """解析嵌入模型运行设备：auto 时根据 CUDA 是否可用自动选择，避免无 GPU 机器崩溃。"""
    pref = (preferred or EMBEDDING_DEVICE or "auto").strip().lower()
    if pref in ("cpu", "cuda"):
        return pref
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def get_session_secret() -> str:
    """会话签名密钥：优先环境变量；缺失时生成随机密钥并告警（重启后会话失效）。"""
    if SESSION_SECRET_KEY:
        return SESSION_SECRET_KEY
    import secrets

    logger.warning("SESSION_SECRET_KEY 未设置，已生成随机临时密钥（重启后会话失效，生产请务必配置）")
    return secrets.token_urlsafe(48)

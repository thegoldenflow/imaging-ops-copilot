import re
import logging

import neo4j
from langchain_core.prompts import PromptTemplate

from agent.schema import CypherCheckerResponse
from agent.prompts import cypher_checker_prompt
from configuration import config, dependency

logger = logging.getLogger(__name__)

# Neo4j 节点 label → 实体对齐所用的小写语义 schema（兼作白名单）。
# entity_alignment.py 里 MySQL 的 entity_schema 与 Chroma 的 type 存的是小写语义类型，
# 而 agent 按 prompt 传入的是大写 Neo4j label（Disease...），这里做一次显式映射。
label_to_schema = {
    "Disease": "disease",
    "Symptom": "symptom",
    "Cause": "cause",
    "Drug": "drug",
    "Food": "food",
    "People": "people",
    "Check": "check",
}

# 写操作/危险过程关键字：应用层早拦截（真正的保证是只读账号 + READ 路由）。
_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH)\b"
    r"|\bLOAD\s+CSV\b"
    r"|\b(apoc|dbms)\.",
    re.IGNORECASE,
)

_cypher_checker_llm = None
_entity_aligner = None


def _get_cypher_checker_llm():
    global _cypher_checker_llm
    if _cypher_checker_llm is None:
        from langchain_deepseek import ChatDeepSeek

        _cypher_checker_llm = ChatDeepSeek(model="deepseek-chat").with_structured_output(
            CypherCheckerResponse
        )
    return _cypher_checker_llm


def _get_entity_aligner():
    global _entity_aligner
    if _entity_aligner is None:
        from datasync.entity_alignment import EntityAlignment

        _entity_aligner = EntityAlignment()
    return _entity_aligner


def _looks_like_write(cypher: str) -> bool:
    return bool(_WRITE_KEYWORDS.search(cypher or ""))


def _strip_embeddings(value):
    """递归剥离结果中的 *embedding 字段，避免把向量灌进 LLM 上下文（token 爆炸/成本）。"""
    if isinstance(value, dict):
        return {
            k: _strip_embeddings(v)
            for k, v in value.items()
            if "embedding" not in k.lower()
        }
    if isinstance(value, list):
        return [_strip_embeddings(v) for v in value]
    return value


def entity_alignment(entitys_to_alignment: list):
    """
    当需要将用户查询中的实体对齐到图数据库已有实体时使用。
    """
    logger.info(f"开始调用工具：entity_alignment，对齐参数为：{entitys_to_alignment}")
    aligner = _get_entity_aligner()
    for node in entitys_to_alignment:
        schema = label_to_schema.get(node.get("label"))
        if not schema:
            continue
        try:
            res = aligner(node["entity"], schema)
        except Exception:
            logger.exception("实体对齐失败，保留原始实体")
            res = None
        if res:
            node["entity"] = res
    return entitys_to_alignment


def check_syntax_error(cypher: str):
    """
    检查 cypher 语句是否存在语法错误，或是否不符合已有 schema 结构。
    执行 Cypher 前必须先用该工具检测合法性。
    """
    logger.info("开始调用工具：check_syntax_error")
    logger.info(f"当前需要校验的Cypher语句为:{cypher}")
    prompt = PromptTemplate.from_template(cypher_checker_prompt)
    chain = prompt | _get_cypher_checker_llm()
    res = chain.invoke({"neo4j_schema": dependency.get_neo4j_schema(), "cypher": cypher})
    logger.info(f"Cypher语句LLM校验结果为:{res}")
    # 返回 dict 而非 Pydantic 对象，工具消息内容更可控
    return res.model_dump() if hasattr(res, "model_dump") else res


def neo4j_query(cypher, params=None):
    """
    当需要从 neo4j 数据库查询数据时使用（仅只读）。
    """
    logger.info("开始调用工具：neo4j_query")
    logger.info(f"当前调用的cypher为:{cypher}")

    # 只读强制（应用层早拦截；数据库层由只读账号 + READ 路由兜底）
    if _looks_like_write(cypher):
        msg = "拒绝执行：本助手仅支持只读查询，检测到写/危险操作关键字。"
        logger.warning(f"{msg} cypher={cypher}")
        return msg

    if not params:
        params = {}

    driver = dependency.get_neo4j_readonly_driver()
    try:
        result = driver.execute_query(
            cypher,
            parameters_=params,
            database_=config.NEO4J_DATABASE,
            routing_=neo4j.RoutingControl.READ,
        )
    except Exception as e:
        logger.exception("neo4j_query 执行失败")
        return f"查询执行失败：{e}"

    records = [
        _strip_embeddings(r.data()) for r in result.records[: config.NEO4J_MAX_ROWS]
    ]
    logger.info(f"neo4j_query 返回 {len(records)} 行")
    return records


if __name__ == '__main__':
    pass

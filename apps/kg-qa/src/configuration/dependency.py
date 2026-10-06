"""连接与重资源的惰性单例管理。

改造要点：
- 不再在 import 时连接 Neo4j / 加载模型（原实现让后端 import 就依赖数据库可用）。
- 提供只读 driver 供在线 /chat 查询使用，写路径与读路径分离。
- 提供 close() 供应用 lifespan 关闭连接。
"""
import logging

import neo4j

from configuration import config

logger = logging.getLogger(__name__)

_neo4j_driver = None
_neo4j_readonly_driver = None
_neo4j_schema = None


def get_neo4j_driver():
    """管理员 driver（离线/写场景）。"""
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = neo4j.GraphDatabase.driver(
            uri=config.NEO4J_CONFIG["uri"], auth=config.NEO4J_CONFIG["auth"]
        )
    return _neo4j_driver


def get_neo4j_readonly_driver():
    """只读 driver（在线 /chat 查询）。"""
    global _neo4j_readonly_driver
    if _neo4j_readonly_driver is None:
        _neo4j_readonly_driver = neo4j.GraphDatabase.driver(
            uri=config.NEO4J_READONLY_CONFIG["uri"],
            auth=config.NEO4J_READONLY_CONFIG["auth"],
        )
    return _neo4j_readonly_driver


def get_neo4j_schema(refresh: bool = False) -> str:
    """获取并缓存图数据库 schema 字符串（注入到主 agent 的 system prompt）。"""
    global _neo4j_schema
    if _neo4j_schema is None or refresh:
        from langchain_neo4j import Neo4jGraph

        graph = Neo4jGraph(
            url=config.NEO4J_CONFIG["uri"],
            username=config.NEO4J_CONFIG["auth"][0],
            password=config.NEO4J_CONFIG["auth"][1],
            database=config.NEO4J_DATABASE,
        )
        _neo4j_schema = graph.get_schema
    return _neo4j_schema


def close():
    """关闭所有已打开的连接（应用关闭时调用）。"""
    global _neo4j_driver, _neo4j_readonly_driver
    for d in (_neo4j_driver, _neo4j_readonly_driver):
        if d is not None:
            try:
                d.close()
            except Exception:
                logger.exception("关闭 Neo4j driver 失败")
    _neo4j_driver = None
    _neo4j_readonly_driver = None

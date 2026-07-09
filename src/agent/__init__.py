import logging

from agent.schema import CheckSyntaxError, EntityAlignmentList, Neo4jQueryParams
from agent.tools_def import check_syntax_error, neo4j_query, entity_alignment
from agent.prompts import major_agent_system_prompt_template
from configuration import config

logger = logging.getLogger(__name__)


def build_checkpointer():
    """
    构造对话记忆 checkpointer。

    返回 (checkpointer, context_manager)：
    - 优先使用 PostgresSaver（POSTGRES_URI 已配置），持久化、可跨 worker、重启不丢；
    - 失败或未配置时回退到进程内 InMemorySaver（仅本地调试）；
    - context_manager 需在应用关闭时 __exit__（Postgres 场景管理连接池），InMemory 场景为 None。
    """
    if not config.AGENT_WITH_MEMORY:
        return None, None

    if config.POSTGRES_URI:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            cm = PostgresSaver.from_conn_string(config.POSTGRES_URI)
            saver = cm.__enter__()
            saver.setup()  # 首次运行建表，幂等
            logger.info("对话记忆：使用 PostgresSaver")
            return saver, cm
        except Exception:
            logger.exception("PostgresSaver 初始化失败，回退到 InMemorySaver")

    from langgraph.checkpoint.memory import InMemorySaver

    logger.warning("对话记忆：使用 InMemorySaver（重启丢失、不跨 worker，仅供本地调试）")
    return InMemorySaver(), None


def get_agent(neo4j_schema, checkpointer=None):
    """
    创建主 agent。
    :param neo4j_schema: 注入 system prompt 的图数据库 schema 字符串
    :param checkpointer: 对话记忆，由 build_checkpointer() 提供
    """
    from langchain.agents import create_agent
    from langchain_deepseek import ChatDeepSeek
    from langchain.tools import tool

    llm = ChatDeepSeek(model=config.DEEPSEEK_MODEL)

    neo4j_query_tool = tool(neo4j_query, args_schema=Neo4jQueryParams)
    check_syntax_tool = tool(check_syntax_error, args_schema=CheckSyntaxError)
    entity_alignment_tool = tool(entity_alignment, args_schema=EntityAlignmentList)

    agent = create_agent(
        model=llm,
        tools=[neo4j_query_tool, check_syntax_tool, entity_alignment_tool],
        checkpointer=checkpointer,
        system_prompt=major_agent_system_prompt_template.format(neo4j_schema=neo4j_schema),
    )
    return agent

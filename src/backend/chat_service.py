import logging

from langchain_core.messages import AIMessageChunk

from agent import get_agent, build_checkpointer
from configuration import config, dependency

logger = logging.getLogger(__name__)


def _chunk_text(msg) -> str:
    """
    从流式消息块中提取要展示给用户的文本。

    - 只保留 AIMessageChunk（丢弃 ToolMessage、工具节点等）；
    - 丢弃携带 tool_calls / tool_call_chunks 的块（agent 的中间"调用工具"步骤，避免中间叙述泄漏）；
    - content 可能是字符串或内容块列表，统一规范化为字符串。
    """
    if not isinstance(msg, AIMessageChunk):
        return ""
    if getattr(msg, "tool_calls", None) or getattr(msg, "tool_call_chunks", None):
        return ""

    content = msg.content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        content = "".join(parts)
    if not isinstance(content, str):
        return ""
    return content


class ChatService:
    def __init__(self):
        self.agent = None
        self._checkpointer_cm = None

    def startup(self):
        """应用启动时初始化：构造记忆 + 加载 schema + 创建 agent。失败抛出以便快速失败。"""
        logger.info("ChatService 启动中...")
        checkpointer, self._checkpointer_cm = build_checkpointer()
        schema = dependency.get_neo4j_schema()
        self.agent = get_agent(schema, checkpointer=checkpointer)
        logger.info("ChatService 启动完成")

    def shutdown(self):
        """应用关闭时释放资源。"""
        if self._checkpointer_cm is not None:
            try:
                self._checkpointer_cm.__exit__(None, None, None)
            except Exception:
                logger.exception("关闭 checkpointer 失败")
        dependency.close()

    def chat(self, user_query, session_id):
        """
        聊天入口，根据配置决定是否流式输出。
        """
        if self.agent is None:
            yield "服务尚未就绪，请稍后再试。"
            return

        agent_config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": config.AGENT_RECURSION_LIMIT,
        }
        try:
            if config.AGENT_STREAM_OUTPUT:
                for msg, _metadata in self.agent.stream(
                    {"messages": [("user", user_query)]},
                    config=agent_config,
                    stream_mode="messages",
                ):
                    text = _chunk_text(msg)
                    if text:
                        yield text
            else:
                result = self.agent.invoke(
                    {"messages": [("user", user_query)]}, config=agent_config
                )
                yield result["messages"][-1].content
        except Exception:
            logger.exception("chat 处理失败")
            yield "系统暂时无法响应，请稍后再试。"


if __name__ == '__main__':
    pass

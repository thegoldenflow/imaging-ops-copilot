import logging
import re

from langchain_core.messages import AIMessageChunk

from agent import get_agent, build_checkpointer
from configuration import config, dependency

logger = logging.getLogger(__name__)


def response_language_for_query(user_query: str) -> str:
    """用于本地错误文案；成功回答的语言约束由 agent system prompt 执行。"""
    text = user_query or ""
    han_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    return "en" if latin_count > han_count else "zh"


def _agent_message(user_query: str, language: str | None) -> str:
    """界面指定了回答语言时，在用户消息末尾附加机读标记；未指定则原样透传（兼容老客户端）。"""
    if language in ("zh", "en"):
        return f"{user_query}\n\n(Answer-Language: {language})"
    return user_query


_MARKER_RE = re.compile(r"\(\s*Answer-Language\s*:\s*(?:zh|en)\s*\)", re.IGNORECASE)
_MARKER_PREFIX = "(answer-language: "


def _could_be_marker(tail: str) -> bool:
    lowered = tail.lower()
    return _MARKER_PREFIX.startswith(lowered) or lowered.startswith(_MARKER_PREFIX)


def _strip_answer_language_marker(chunks):
    """
    从流式输出中剥除语言标记：提示词已要求模型不要复述它，这里是兜底。

    标记会被切分到多个 token 块，故对可能是标记开头的尾部做缓冲，凑齐后再判断。
    """
    buffer = ""
    for chunk in chunks:
        buffer = _MARKER_RE.sub("", buffer + chunk)
        hold_at = buffer.rfind("(")
        if hold_at != -1 and _could_be_marker(buffer[hold_at:]):
            emit, buffer = buffer[:hold_at], buffer[hold_at:]
        else:
            emit, buffer = buffer, ""
        if emit:
            yield emit
    if buffer:
        yield buffer


def _localized_message(language: str, key: str) -> str:
    messages = {
        "not_ready": {
            "zh": "服务尚未就绪，请稍后再试。",
            "en": "The service is not ready yet. Please try again shortly.",
        },
        "temporary_failure": {
            "zh": "系统暂时无法响应，请稍后再试。",
            "en": "The system is temporarily unable to respond. Please try again later.",
        },
    }
    return messages[key][language]


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

    def chat(self, user_query, session_id, language=None):
        """
        聊天入口，根据配置决定是否流式输出。

        language: 界面所选回答语言（"zh"/"en"）；None 时回落到按问题语言检测。
        """
        if language in ("zh", "en"):
            response_language = language
        else:
            response_language = response_language_for_query(user_query)
        if self.agent is None:
            yield _localized_message(response_language, "not_ready")
            return

        agent_message = _agent_message(user_query, language)
        agent_config = {
            "configurable": {"thread_id": session_id},
            "recursion_limit": config.AGENT_RECURSION_LIMIT,
        }
        try:
            yield from _strip_answer_language_marker(
                self._run_agent(agent_message, agent_config)
            )
        except Exception:
            logger.exception("chat 处理失败")
            yield _localized_message(response_language, "temporary_failure")

    def _run_agent(self, agent_message, agent_config):
        """按配置以流式或一次性方式产出 agent 文本。"""
        if config.AGENT_STREAM_OUTPUT:
            for msg, _metadata in self.agent.stream(
                {"messages": [("user", agent_message)]},
                config=agent_config,
                stream_mode="messages",
            ):
                text = _chunk_text(msg)
                if text:
                    yield text
        else:
            result = self.agent.invoke(
                {"messages": [("user", agent_message)]}, config=agent_config
            )
            yield result["messages"][-1].content


if __name__ == '__main__':
    pass

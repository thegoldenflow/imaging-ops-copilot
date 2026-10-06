import logging
import re
import threading
from collections import Counter

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
        self.checkpointer = None
        self._checkpointer_cm = None
        # 正在生成中的 thread（同一 thread 可能有并发请求，故用计数）与待延后删除的 thread。
        # /chat 跑在线程池里，这里的状态必须加锁。
        self._runs_lock = threading.Lock()
        self._active_runs = Counter()
        self._deferred_deletes = set()

    def startup(self):
        """应用启动时初始化：构造记忆 + 加载 schema + 创建 agent。失败抛出以便快速失败。"""
        logger.info("ChatService 启动中...")
        checkpointer, self._checkpointer_cm = build_checkpointer()
        self.checkpointer = checkpointer
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
        # 清空引用，避免关闭后仍有人拿着已释放的连接池 / agent
        self._checkpointer_cm = None
        self.checkpointer = None
        self.agent = None
        dependency.close()

    def reset_thread(self, session_id) -> bool:
        """
        删除某个会话在 checkpointer 中的历史，返回是否已同步删除完成。

        医疗对话属于敏感内容，用户点"新建对话"时应真正删掉，而不只是换个 thread_id
        留一堆孤儿记录。但清理失败不能阻断新建对话——调用方总会换用新的 thread_id，
        所以这里吞掉异常只记日志。

        若该 thread 还有生成在进行中（用户在回答流式输出途中点了"新建对话"），此刻删除会
        被随后写回的 checkpoint 复活成永远删不掉的孤儿，因此登记为延后删除，等生成结束
        再删。此时返回 False 表示"尚未删完"。
        """
        if self.checkpointer is None or not session_id:
            return False

        with self._runs_lock:
            if self._active_runs.get(session_id):
                self._deferred_deletes.add(session_id)
                logger.info("会话 %s 仍在生成，历史将在生成结束后清理", session_id)
                return False

        return self._delete_thread(session_id)

    def _delete_thread(self, thread_id) -> bool:
        delete_thread = getattr(self.checkpointer, "delete_thread", None)
        if delete_thread is None:  # 老版本 langgraph 没有该 API
            logger.warning("checkpointer 不支持 delete_thread，跳过历史清理")
            return False

        try:
            delete_thread(thread_id)
            return True
        except Exception:
            logger.exception("清理会话历史失败: thread_id=%s", thread_id)
            return False

    def _begin_run(self, thread_id):
        with self._runs_lock:
            self._active_runs[thread_id] += 1

    def _end_run(self, thread_id):
        """生成结束（正常/异常/客户端断开）后调用：必要时补做延后的历史清理。"""
        with self._runs_lock:
            self._active_runs[thread_id] -= 1
            if self._active_runs[thread_id] > 0:
                return
            del self._active_runs[thread_id]
            if thread_id not in self._deferred_deletes:
                return
            self._deferred_deletes.discard(thread_id)

        if self.checkpointer is None:  # 已 shutdown
            return
        logger.info("会话 %s 生成已结束，执行延后的历史清理", thread_id)
        self._delete_thread(thread_id)

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
        # 标记生成中，让并发的 reset_thread 知道现在删历史会被写回复活。
        # finally 在正常结束、异常、以及客户端断开（生成器被 close，抛 GeneratorExit）时都会执行。
        self._begin_run(session_id)
        try:
            yield from _strip_answer_language_marker(
                self._run_agent(agent_message, agent_config)
            )
        except Exception:
            logger.exception("chat 处理失败")
            yield _localized_message(response_language, "temporary_failure")
        finally:
            self._end_run(session_id)

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

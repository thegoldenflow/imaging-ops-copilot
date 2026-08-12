"""覆盖 /ui-config 与 /session/reset 两个端点。

不启动 lifespan（TestClient 不作为上下文管理器使用），因此不需要 Neo4j / Postgres / LLM。
"""
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app, service  # noqa: E402
from backend.chat_service import ChatService  # noqa: E402
from backend.schemas import Question  # noqa: E402
from configuration import config  # noqa: E402


@app.get("/__test__/session-id")
def _test_session_id(request: Request):
    """
    测试专用只读探针。

    不解 cookie：SESSION_SECRET_KEY 未配置时 get_session_secret() 每次调用都返回新的随机
    密钥，测试端再签一次必然对不上（只有本机 .env 恰好配了才"碰巧"通过）。
    也不走 POST /chat：那条路径会顺手创建 session_id，于是"轮换"和"整个 session 被销毁"
    在断言里无法区分。
    """
    return {"session_id": request.session.get("session_id")}


class RecordingCheckpointer:
    def __init__(self, exc=None):
        self.deleted = []
        self.exc = exc

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)
        if self.exc is not None:
            raise self.exc


class LegacyCheckpointer:
    """老版本 langgraph 没有 delete_thread。"""


def session_id_of(client):
    """读取当前服务端 session 里的 session_id（不存在时为 None，且不会创建）。"""
    return client.get("/__test__/session-id").json()["session_id"]


def start_session(client) -> str:
    """先发一次 /chat 让服务端建立 session_id，返回该 id。"""
    with patch.object(service, "agent", None):
        client.post("/chat", json={"message": "hi"})
    session_id = session_id_of(client)
    assert session_id, "前置条件失败：/chat 应当创建 session_id"
    return session_id


class UiConfigTests(unittest.TestCase):
    def test_exposes_configured_max_message_length(self):
        client = TestClient(app)
        with patch.object(config, "MAX_MESSAGE_LENGTH", 1234):
            response = client.get("/ui-config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"max_message_length": 1234})

    def test_matches_the_limit_actually_enforced_by_the_schema(self):
        # 下发给前端的上限必须就是 Question 真正校验的那个值，
        # 否则会出现前端放行、后端 422 的割裂
        client = TestClient(app)
        advertised = client.get("/ui-config").json()["max_message_length"]

        enforced = next(
            m.max_length
            for m in Question.model_fields["message"].metadata
            if getattr(m, "max_length", None) is not None
        )
        self.assertEqual(advertised, enforced)

    def test_message_at_the_advertised_limit_is_accepted_and_one_over_is_rejected(self):
        # 直接验证边界：前端按这个数放行，后端就必须收
        client = TestClient(app)
        limit = client.get("/ui-config").json()["max_message_length"]

        with patch.object(service, "agent", None):
            ok = client.post("/chat", json={"message": "x" * limit})
        self.assertEqual(ok.status_code, 200)

        too_long = client.post("/chat", json={"message": "x" * (limit + 1)})
        self.assertEqual(too_long.status_code, 422)

    def test_does_not_leak_sensitive_config(self):
        client = TestClient(app)
        payload = client.get("/ui-config").json()
        self.assertEqual(set(payload), {"max_message_length"})


class SessionResetEndpointTests(unittest.TestCase):
    def test_reset_rotates_session_id(self):
        client = TestClient(app)
        before = start_session(client)

        response = client.post("/session/reset")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        after = session_id_of(client)
        # 必须是"换成另一个有效 id"，而不是把 session 整个删掉
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_reset_deletes_previous_thread_history(self):
        client = TestClient(app)
        previous = start_session(client)

        checkpointer = RecordingCheckpointer()
        with patch.object(service, "checkpointer", checkpointer):
            response = client.post("/session/reset")

        self.assertEqual(checkpointer.deleted, [previous])
        self.assertTrue(response.json()["history_cleared"])

    def test_reset_without_existing_session_still_succeeds(self):
        client = TestClient(app)
        checkpointer = RecordingCheckpointer()
        with patch.object(service, "checkpointer", checkpointer):
            response = client.post("/session/reset")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertFalse(response.json()["history_cleared"])
        self.assertEqual(checkpointer.deleted, [])  # 没有旧 thread 可删

    def test_reset_succeeds_even_if_history_cleanup_fails(self):
        # 清理失败不能阻断新建对话：id 仍要轮换，否则用户会继续沿用旧上下文
        client = TestClient(app)
        before = start_session(client)

        checkpointer = RecordingCheckpointer(exc=RuntimeError("db down"))
        with patch.object(service, "checkpointer", checkpointer):
            response = client.post("/session/reset")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertFalse(response.json()["history_cleared"])
        self.assertNotEqual(session_id_of(client), before)

    def test_reset_is_post_only(self):
        client = TestClient(app)
        self.assertEqual(client.get("/session/reset").status_code, 405)


class ChatUsesRotatedThreadTests(unittest.TestCase):
    """端到端验证"新建对话"真正换了 agent 的 thread_id——这是该功能的核心契约。"""

    def test_chat_switches_thread_after_reset(self):
        from langchain_core.messages import AIMessageChunk

        used_threads = []

        class RecordingAgent:
            def stream(self, agent_input, config=None, **_kwargs):
                used_threads.append(config["configurable"]["thread_id"])
                yield AIMessageChunk(content="ok"), {}

        client = TestClient(app)
        with patch.object(service, "agent", RecordingAgent()), patch(
            "backend.chat_service.config.AGENT_STREAM_OUTPUT", True
        ):
            client.post("/chat", json={"message": "q1"})
            client.post("/chat", json={"message": "q2"})
            client.post("/session/reset")
            client.post("/chat", json={"message": "q3"})

        self.assertEqual(len(used_threads), 3)
        self.assertEqual(used_threads[0], used_threads[1], "重置前应复用同一个 thread")
        self.assertNotIn(used_threads[2], used_threads[:2], "重置后必须换新 thread")


class ResetThreadTests(unittest.TestCase):
    def test_returns_false_without_checkpointer(self):
        svc = ChatService()
        self.assertFalse(svc.reset_thread("thread-1"))

    def test_returns_false_for_empty_session_id(self):
        svc = ChatService()
        svc.checkpointer = RecordingCheckpointer()
        self.assertFalse(svc.reset_thread(None))
        self.assertFalse(svc.reset_thread(""))
        self.assertEqual(svc.checkpointer.deleted, [])

    def test_deletes_thread_when_supported(self):
        svc = ChatService()
        svc.checkpointer = RecordingCheckpointer()
        self.assertTrue(svc.reset_thread("thread-1"))
        self.assertEqual(svc.checkpointer.deleted, ["thread-1"])

    def test_swallows_checkpointer_errors(self):
        svc = ChatService()
        svc.checkpointer = RecordingCheckpointer(exc=RuntimeError("boom"))
        self.assertFalse(svc.reset_thread("thread-1"))

    def test_tolerates_checkpointer_without_delete_thread(self):
        svc = ChatService()
        svc.checkpointer = LegacyCheckpointer()
        self.assertFalse(svc.reset_thread("thread-1"))


class DeferredCleanupTests(unittest.TestCase):
    """回答流式输出途中点"新建对话"时，历史必须等生成结束后再删。

    否则 agent 随后写回的 checkpoint 会把刚删掉的 thread 复活，而那个 thread_id 已经从
    session 里轮换掉了，谁也再删不到它——敏感对话就此永久残留。
    """

    def _service_with_agent(self, chunks):
        from langchain_core.messages import AIMessageChunk

        class SlowAgent:
            def stream(self, agent_input, config=None, **_kwargs):
                for c in chunks:
                    yield AIMessageChunk(content=c), {}

        svc = ChatService()
        svc.agent = SlowAgent()
        svc.checkpointer = RecordingCheckpointer()
        return svc

    def test_delete_is_deferred_until_generation_finishes(self):
        svc = self._service_with_agent(["part1 ", "part2"])
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            stream = svc.chat("q", "thread-1")
            self.assertEqual(next(stream), "part1 ")  # 生成进行中

            # 此刻请求清理：不能立即删
            self.assertFalse(svc.reset_thread("thread-1"))
            self.assertEqual(svc.checkpointer.deleted, [])
            self.assertIn("thread-1", svc._deferred_deletes)

            list(stream)  # 生成结束

        self.assertEqual(svc.checkpointer.deleted, ["thread-1"])
        self.assertEqual(svc._deferred_deletes, set())
        self.assertEqual(svc._active_runs, Counter())

    def test_deferred_delete_runs_even_if_client_disconnects(self):
        svc = self._service_with_agent(["part1 ", "part2"])
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            stream = svc.chat("q", "thread-1")
            next(stream)
            svc.reset_thread("thread-1")
            stream.close()  # 客户端断开 -> GeneratorExit

        self.assertEqual(svc.checkpointer.deleted, ["thread-1"])

    def test_delete_is_immediate_when_nothing_is_generating(self):
        svc = self._service_with_agent(["x"])
        self.assertTrue(svc.reset_thread("thread-1"))
        self.assertEqual(svc.checkpointer.deleted, ["thread-1"])
        self.assertEqual(svc._deferred_deletes, set())

    def test_concurrent_runs_on_same_thread_delete_only_after_the_last_one(self):
        svc = self._service_with_agent(["a ", "b"])
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            first = svc.chat("q1", "thread-1")
            second = svc.chat("q2", "thread-1")
            next(first)
            next(second)
            svc.reset_thread("thread-1")

            list(first)
            self.assertEqual(svc.checkpointer.deleted, [], "还有一个生成在跑，不能删")
            list(second)

        self.assertEqual(svc.checkpointer.deleted, ["thread-1"])

    def test_run_tracking_is_cleaned_up_when_agent_raises(self):
        class BoomAgent:
            def stream(self, *_a, **_k):
                raise RuntimeError("boom")
                yield  # pragma: no cover

        svc = ChatService()
        svc.agent = BoomAgent()
        svc.checkpointer = RecordingCheckpointer()
        with patch("backend.chat_service.config.AGENT_STREAM_OUTPUT", True):
            list(svc.chat("q", "thread-1", language="en"))
        self.assertEqual(svc._active_runs, Counter(), "异常路径也必须解除生成中标记")
        self.assertTrue(svc.reset_thread("thread-1"))

    def test_shutdown_releases_the_checkpointer_context_manager(self):
        # 这条断言是为了盯住真正的资源释放：删掉 __exit__ 调用后必须有测试失败，
        # 否则连接池泄漏可以悄悄溜过去
        exits = []

        class RecordingCM:
            def __exit__(self, *exc_info):
                exits.append(exc_info)
                return False

        svc = ChatService()
        svc._checkpointer_cm = RecordingCM()
        with patch("backend.chat_service.dependency.close"):
            svc.shutdown()
        self.assertEqual(exits, [(None, None, None)])

    def test_shutdown_survives_a_failing_context_manager(self):
        class BoomCM:
            def __exit__(self, *_exc_info):
                raise RuntimeError("pool already closed")

        svc = ChatService()
        svc._checkpointer_cm = BoomCM()
        closed = []
        with patch("backend.chat_service.dependency.close", lambda: closed.append(True)):
            svc.shutdown()  # 不应抛出
        self.assertEqual(closed, [True], "checkpointer 关闭失败不应阻断后续资源释放")
        self.assertIsNone(svc._checkpointer_cm)

    def test_shutdown_drops_released_references(self):
        svc = ChatService()
        svc.checkpointer = RecordingCheckpointer()
        svc.agent = object()
        with patch("backend.chat_service.dependency.close"):
            svc.shutdown()
        self.assertIsNone(svc.checkpointer)
        self.assertIsNone(svc.agent)
        self.assertIsNone(svc._checkpointer_cm)

    def test_reset_after_shutdown_is_a_noop(self):
        svc = ChatService()
        svc.checkpointer = RecordingCheckpointer()
        with patch("backend.chat_service.dependency.close"):
            svc.shutdown()
        self.assertFalse(svc.reset_thread("thread-1"))


if __name__ == "__main__":
    unittest.main()

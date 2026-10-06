import os
import uuid
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from configuration import config
from backend.chat_service import ChatService
from backend.schemas import Question

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

service = ChatService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化重资源（模型、DB、agent），失败则快速失败并给出清晰错误
    logger.info("应用启动中...")
    service.startup()
    yield
    logger.info("应用关闭中...")
    service.shutdown()


limiter = Limiter(key_func=get_remote_address)
app = FastAPI(lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.get_session_secret(),  # 强随机/环境变量，禁止硬编码
    max_age=3600,
    https_only=config.SESSION_HTTPS_ONLY,  # 反代 HTTPS 后置 True
    same_site="lax",
)

app.mount("/static", StaticFiles(directory=str(config.WEB_STATIC_DIR)), name="static")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "agent_ready": service.agent is not None}


@app.get("/")
def read_root():
    return RedirectResponse("/static/index.html")


@app.get("/ui-config")
def ui_config():
    """前端启动时拉取的界面参数，避免把上限之类的值在 JS 里写死。只暴露非敏感项。"""
    return {"max_message_length": config.MAX_MESSAGE_LENGTH}


@app.post("/session/reset")
@limiter.limit(config.SESSION_RESET_RATE_LIMIT)
def reset_session(request: Request):
    """
    开始新对话：轮换 session_id 并清掉旧 thread 的历史。

    轮换（而不是复用同一个 id）保证即使历史清理失败，新对话也不会带上旧上下文。
    仅接受 POST，配合 SessionMiddleware 的 same_site="lax" 防跨站触发。
    """
    session = request.session
    previous_id = session.get("session_id")
    session["session_id"] = str(uuid.uuid4())

    history_cleared = service.reset_thread(previous_id)
    # 未启用记忆时本就没有历史可清，不该报警（否则每次点击都刷一条误导性 WARNING）
    if previous_id and not history_cleared and service.checkpointer is not None:
        logger.warning("会话 %s 的历史未能清理，已切换到新 thread", previous_id)

    return {"ok": True, "history_cleared": history_cleared}


@app.post("/chat")
@limiter.limit(config.CHAT_RATE_LIMIT)
def read_item(question: Question, request: Request):
    # 维护 Session，使用 session_id 作为 langgraph agent 的 thread_id 来保存对话历史
    session = request.session
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    current_session_id = session["session_id"]

    return StreamingResponse(
        service.chat(
            question.message,
            session_id=current_session_id,
            language=question.language,
        ),
        media_type="text/plain; charset=utf-8",
    )


if __name__ == '__main__':
    # 生产：绑 127.0.0.1，由 HTTPS 反向代理对外；通过 HOST/PORT 环境变量覆盖
    uvicorn.run(
        "backend.app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )

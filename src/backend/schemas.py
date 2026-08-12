from typing import Literal, Optional

from pydantic import BaseModel, Field

from configuration import config


class Question(BaseModel):
    # 限制输入长度，防止超长输入耗尽 LLM 成本 / 上下文
    message: str = Field(..., min_length=1, max_length=config.MAX_MESSAGE_LENGTH)
    # UI 所选回答语言；None = 老客户端，回落到按问题语言检测
    language: Optional[Literal["zh", "en"]] = None


class Answer(BaseModel):
    message: str

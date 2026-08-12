from typing import Any, Dict

from pydantic import BaseModel,Field

# StructuredOutputSchema
class CypherCheckerResponse(BaseModel):
    is_legal:bool=Field(description="当前语句是否为合法语句，如果是，则返回True, 否则返回False")
    error_msg:str=Field(description="如果is_legal为False,返回具体错误信息，否则返回空字符串")
    solve_method:str=Field(description="如果is_legal为False,返回解决方法，否则返回空字符串")
    original_cypher:str=Field(description="原始输入的Cypher语句")


# ToolsSchema
class CheckSyntaxError(BaseModel):
    cypher:str =Field(description="需要检测是否合法的cypher语句")


class Neo4jQueryParams(BaseModel):
    cypher:str = Field(description="需要执行的Cypher查询语句")
    params:dict[str,Any]=Field(
        default_factory=dict,
        description=(
            "Cypher 查询参数。值可以是字符串、数字、布尔值、null 或列表；"
            "没有参数时使用空字典。"
        ),
    )

class EntityAlignmentList(BaseModel):
    entitys_to_alignment:list[Dict[str,str]] = Field(
        description=(
            "需要对齐的中英文医疗实体列表。每项包含 entity 和 label；"
            "entity 保留用户原始术语，label 使用 Neo4j 标签名称，例如 Disease、"
            "Symptom、Department 或 Drug。工具返回的 entity 是图谱使用的中文标准词。"
        )
    )

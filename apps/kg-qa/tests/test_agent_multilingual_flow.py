import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from langchain_core.language_models.fake_chat_models import (  # noqa: E402
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage  # noqa: E402

import agent  # noqa: E402
from agent.schema import Neo4jQueryParams  # noqa: E402


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Scripted LLM that still exercises the real LangChain/LangGraph tool loop."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


class TracingAligner:
    def __init__(self, mappings, trace):
        self.mappings = mappings
        self.trace = trace

    def __call__(self, text, schema):
        result = self.mappings[(text, schema)]
        self.trace.append(("entity_alignment", text, schema, result))
        return result


class AgentMultilingualFlowTests(unittest.TestCase):
    scenarios = (
        {
            "question": "I have a headache and nausea.",
            "entities": [
                {"entity": "headache", "label": "Symptom"},
                {"entity": "nausea", "label": "Symptom"},
            ],
            "mappings": {
                ("headache", "symptom"): "头痛",
                ("nausea", "symptom"): "恶心",
            },
            "cypher": (
                "MATCH (d:Disease)-[:HAVE]->(s:Symptom) "
                "WHERE s.name IN $symptoms RETURN DISTINCT d.name AS disease"
            ),
            "params": {"symptoms": ["头痛", "恶心"]},
            "answer": "Headache and nausea can occur with several conditions.",
        },
        {
            "question": "What department should I see for migraines?",
            "entities": [{"entity": "migraines", "label": "Disease"}],
            "mappings": {("migraines", "disease"): "偏头痛"},
            "cypher": (
                "MATCH (d:Disease)-[:BELONG]->(dept:Department) "
                "WHERE d.name = $disease RETURN dept.name AS department"
            ),
            "params": {"disease": "偏头痛"},
            "answer": "The graph recommends the neurology department.",
        },
        {
            "question": "Can hypertension cause headaches?",
            "entities": [
                {"entity": "hypertension", "label": "Disease"},
                {"entity": "headaches", "label": "Symptom"},
            ],
            "mappings": {
                ("hypertension", "disease"): "高血压",
                ("headaches", "symptom"): "头痛",
            },
            "cypher": (
                "MATCH (d:Disease)-[:HAVE]->(s:Symptom) "
                "WHERE d.name = $disease AND s.name = $symptom "
                "RETURN d.name AS disease, s.name AS symptom"
            ),
            "params": {"disease": "高血压", "symptom": "头痛"},
            "answer": (
                "The graph associates hypertension with headache, but does not "
                "by itself prove causation."
            ),
        },
    )

    def _run_scenario(self, scenario):
        trace = []

        def check_syntax_error(cypher):
            """Capture the runtime checker call."""
            trace.append(("check_syntax_error", cypher))
            return {
                "is_legal": True,
                "error_msg": "",
                "solve_method": "",
                "original_cypher": cypher,
            }

        check_syntax_error.__name__ = "check_syntax_error"

        def neo4j_query(cypher, params=None):
            """Capture the runtime Neo4j call without using a live graph."""
            trace.append(("neo4j_query", cypher, params))
            return [{"audit": "synthetic graph result"}]

        neo4j_query.__name__ = "neo4j_query"

        model = ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "entity_alignment",
                            "args": {
                                "entitys_to_alignment": copy.deepcopy(
                                    scenario["entities"]
                                )
                            },
                            "id": "align",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "check_syntax_error",
                            "args": {"cypher": scenario["cypher"]},
                            "id": "check",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "neo4j_query",
                            "args": {
                                "cypher": scenario["cypher"],
                                "params": scenario["params"],
                            },
                            "id": "query",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content=scenario["answer"]),
            ]
        )
        aligner = TracingAligner(scenario["mappings"], trace)
        with (
            patch("langchain_deepseek.ChatDeepSeek", return_value=model),
            patch.object(agent, "check_syntax_error", check_syntax_error),
            patch.object(agent, "neo4j_query", neo4j_query),
            patch("agent.tools_def._get_entity_aligner", return_value=aligner),
        ):
            runtime_agent = agent.get_agent("audit schema", checkpointer=None)
            result = runtime_agent.invoke(
                {"messages": [("user", scenario["question"])]}
            )
        return trace, result["messages"][-1].content

    def test_representative_english_questions_use_chinese_canonical_params(self):
        for scenario in self.scenarios:
            with self.subTest(question=scenario["question"]):
                trace, final_answer = self._run_scenario(scenario)
                alignment_calls = [item for item in trace if item[0] == "entity_alignment"]
                query_calls = [item for item in trace if item[0] == "neo4j_query"]

                self.assertEqual(
                    [(item[1], item[2]) for item in alignment_calls],
                    [
                        (
                            entity["entity"],
                            entity["label"].lower(),
                        )
                        for entity in scenario["entities"]
                    ],
                )
                self.assertEqual(
                    [item[3] for item in alignment_calls],
                    list(scenario["params"].values())[0]
                    if len(scenario["params"]) == 1
                    and isinstance(list(scenario["params"].values())[0], list)
                    else [mapping for mapping in scenario["mappings"].values()],
                )
                self.assertEqual(len(query_calls), 1)
                self.assertEqual(query_calls[0][1], scenario["cypher"])
                self.assertEqual(query_calls[0][2], scenario["params"])
                self.assertTrue(final_answer.isascii())
                self.assertEqual(final_answer, scenario["answer"])

    def test_neo4j_tool_schema_accepts_list_parameters(self):
        parsed = Neo4jQueryParams(
            cypher="MATCH (s:Symptom) WHERE s.name IN $symptoms RETURN s",
            params={"symptoms": ["头痛", "恶心"]},
        )
        self.assertEqual(parsed.params["symptoms"], ["头痛", "恶心"])


if __name__ == "__main__":
    unittest.main()

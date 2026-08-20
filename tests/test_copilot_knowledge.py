import asyncio
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np

from backend import personal_agent, vector_memory
from backend.config import settings
from backend.copilot import knowledge_compiler, prepared_answer_index, prepared_answer_matcher
from backend.copilot.project_anchors import ensure_project_anchor_nodes
from backend.graphs import copilot_prep as prep_graph
from backend.routers import copilot
from backend.storage import copilot_preps
from backend.storage import copilot_sessions


class _FakeEmbedding:
    def get_text_embedding(self, text):
        if "unrelated" in text.lower():
            return [0.0, 1.0, 0.0]
        return [1.0, 0.0, 0.0]

    def get_text_embedding_batch(self, texts):
        return [self.get_text_embedding(text) for text in texts]


class CopilotKnowledgeStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "interviews.db"
        self.patches = [
            patch.object(settings, "base_dir", self.root),
            patch.object(settings, "db_path", self.db_path),
            patch.object(vector_memory, "DB_PATH", self.db_path),
            patch.object(copilot_preps, "DB_PATH", self.db_path),
            patch.object(copilot_sessions, "DB_PATH", self.db_path),
            patch.object(personal_agent, "get_embedding", return_value=_FakeEmbedding()),
            patch.object(vector_memory, "get_embedding", return_value=_FakeEmbedding()),
            patch.object(prepared_answer_index, "get_embedding", return_value=_FakeEmbedding()),
        ]
        for item in self.patches:
            item.start()
        vector_memory.init_memory_table()
        personal_agent.init_personal_agent_tables()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_scoped_document_search_distinguishes_none_empty_and_selected(self):
        first = personal_agent.create_document("aws.md", b"AWS deployment material", "user-a")
        second = personal_agent.create_document("k8s.md", b"Kubernetes material", "user-a")
        foreign = personal_agent.create_document("private.md", b"private material", "user-b")

        all_hits = personal_agent.search_documents("deployment", "user-a", top_k=10)
        self.assertEqual({hit["document_id"] for hit in all_hits}, {first["document_id"], second["document_id"]})
        self.assertEqual(personal_agent.search_documents("deployment", "user-a", document_ids=[]), [])
        selected = personal_agent.search_documents(
            "deployment", "user-a", top_k=10, document_ids=[first["document_id"]]
        )
        self.assertEqual({hit["document_id"] for hit in selected}, {first["document_id"]})
        with self.assertRaisesRegex(ValueError, "不存在或无权访问"):
            personal_agent.get_documents_by_ids([foreign["document_id"]], "user-a", require_ready=True)

    def test_balanced_search_keeps_every_selected_document_and_chunk_evidence(self):
        first = personal_agent.create_document("Rchain-project-overview.md", b"Rchain architecture overview", "user-a")
        second = personal_agent.create_document("behavior.md", b"team conflict story", "user-a")
        hits = personal_agent.search_documents(
            "Rchain architecture", "user-a", top_k=2,
            document_ids=[first["document_id"], second["document_id"]], min_per_document=1,
        )
        self.assertEqual({item["document_id"] for item in hits}, {first["document_id"], second["document_id"]})
        self.assertTrue(all(item["chunk_id"] and len(item["content_hash"]) == 64 for item in hits))

    def test_memory_schema_migrates_user_id_before_creating_scoped_indexes(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DROP TABLE memory_vectors")
            conn.execute("""
                CREATE TABLE memory_vectors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    topic TEXT,
                    session_id TEXT,
                    metadata TEXT DEFAULT '{}',
                    embedding BLOB NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

        vector_memory.init_memory_table()

        with sqlite3.connect(self.db_path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memory_vectors)")}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(memory_vectors)")}
        self.assertIn("user_id", columns)
        self.assertIn("idx_mv_user_type_session", indexes)

    def test_prep_persists_full_input_and_delete_cleans_variant_vectors(self):
        long_jd = "后端工程师" * 100
        snapshot = [{"document_id": "doc-1", "filename": "notes.md", "updated_at": "now"}]
        copilot_preps.create_prep("prep-1", "user-a", "ACME", "Backend", long_jd, ["doc-1"], snapshot)
        loaded = copilot_preps.get_prep("prep-1", "user-a")
        self.assertEqual(loaded["jd_text"], long_jd)
        self.assertEqual(loaded["document_ids"], ["doc-1"])
        self.assertEqual(loaded["source_snapshot"], snapshot)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO memory_vectors "
                "(chunk_type, content, session_id, embedding, user_id) VALUES (?, ?, ?, ?, ?)",
                (prepared_answer_index.COPILOT_QUESTION_VARIANT, "question", "prep-1", b"\x00" * 12, "user-a"),
            )
        self.assertTrue(copilot_preps.delete_prep("prep-1", "user-a"))
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_vectors WHERE session_id='prep-1' AND user_id='user-a'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_index_and_match_are_scoped_by_user_and_prep(self):
        tree = {"nodes": {"project": {"topic": "Shared Library", "sample_questions": ["Why Shared Library?"]}}}
        answer = {
            "answer_id": "pa-1",
            "node_id": "project",
            "topic": "Shared Library",
            "question_variants": ["Why not put it in Jenkinsfile?"],
            "prepared_answer": "We centralized reusable pipeline logic.",
            "short_answer": "For reuse and review.",
            "source_refs": [],
            "usable": True,
        }
        compiled = {"prepared_answers": {"pa-1": answer}}
        prepared_answer_index.build_prepared_answer_index(
            prep_id="prep-a", user_id="user-a", strategy_tree=tree, compiled_knowledge=compiled
        )

        hit = prepared_answer_matcher.match_prepared_answer(
            prep_id="prep-a",
            user_id="user-a",
            utterance_embedding=[1.0, 0.0, 0.0],
            compiled_knowledge=compiled,
        )
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["answer_id"], "pa-1")
        self.assertFalse(prepared_answer_matcher.match_prepared_answer(
            prep_id="prep-a",
            user_id="user-b",
            utterance_embedding=[1.0, 0.0, 0.0],
            compiled_knowledge=compiled,
        )["matched"])
        self.assertFalse(prepared_answer_matcher.match_prepared_answer(
            prep_id="prep-a",
            user_id="user-a",
            utterance_embedding=[0.0, 1.0, 0.0],
            compiled_knowledge=compiled,
        )["matched"])

    def test_rchain_alias_routes_to_overview_without_embedding(self):
        tree = {"nodes": {"rchain-overview": {
            "topic": "Rchain overview", "sample_questions": ["介绍 Rchain"],
        }}}
        answer = {
            "answer_id": "pa-rchain", "node_id": "rchain-overview", "topic": "Rchain",
            "anchor_id": "anchor-rchain", "answer_kind": "overview",
            "question_variants": ["Tell me about Rchain"],
            "prepared_answer": "Rchain 是我的项目。", "expanded_answer": "Rchain 的完整项目介绍。",
            "short_answer": "Rchain 项目。", "source_refs": [], "usable": True,
        }
        compiled = {
            "prepared_answers": {"pa-rchain": answer},
            "project_anchors": [{
                "anchor_id": "anchor-rchain", "project_name": "Rchain",
                "aliases": ["Rchain", "R Chain"], "document_ids": ["doc-rchain"],
            }],
        }
        prepared_answer_index.build_prepared_answer_index(
            prep_id="wireless-car", user_id="user-a", strategy_tree=tree, compiled_knowledge=compiled,
        )
        snapshot = prepared_answer_index.load_prepared_index_snapshot("wireless-car", "user-a", compiled)
        hit = prepared_answer_matcher.match_alias_prepared_answer(
            snapshot=snapshot, compiled_knowledge=compiled,
            utterance_text="介绍下 Rchain 这个项目",
        )
        self.assertEqual(hit["answer_id"], "pa-rchain")
        self.assertEqual(hit["reason"], "project_alias")

    def test_copilot_session_persists_final_displayed_answer_and_routing(self):
        copilot_sessions.start_session("session-1", "prep-1", "user-a", 2)
        copilot_sessions.create_turn("turn-1", "session-1", "prep-1", "user-a", 1, "hr", "介绍 Rchain")
        copilot_sessions.complete_turn(
            "turn-1", "user-a", answer="完整 Rchain 回答", route="prepared",
            node_id="rchain-overview", answer_id="pa-rchain", score=1.0,
            sources=[{"document_id": "doc-rchain", "chunk_id": "42"}],
            latency={"total_ms": 8},
        )
        loaded = copilot_sessions.get_session("session-1", "user-a")
        self.assertEqual(loaded["turns"][0]["answer"], "完整 Rchain 回答")
        self.assertEqual(loaded["turns"][0]["route"], "prepared")
        self.assertEqual(loaded["turns"][0]["sources"][0]["chunk_id"], "42")

    def test_contextual_follow_up_prefers_previous_strategy_node(self):
        answers = {
            "pa-previous": {
                "answer_id": "pa-previous", "node_id": "previous", "topic": "Project",
                "prepared_answer": "Previous node answer", "short_answer": "Previous",
                "source_refs": [], "usable": True,
            },
            "pa-other": {
                "answer_id": "pa-other", "node_id": "other", "topic": "Other",
                "prepared_answer": "Other answer", "short_answer": "Other",
                "source_refs": [], "usable": True,
            },
        }
        rows = [
            ("previous", "pa-previous", [0.75, math.sqrt(1 - 0.75 ** 2), 0.0]),
            ("other", "pa-other", [0.82, math.sqrt(1 - 0.82 ** 2), 0.0]),
        ]
        with sqlite3.connect(self.db_path) as conn:
            for node_id, answer_id, vector in rows:
                conn.execute(
                    "INSERT INTO memory_vectors "
                    "(chunk_type, content, session_id, metadata, embedding, user_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        prepared_answer_index.COPILOT_QUESTION_VARIANT,
                        f"question-{node_id}",
                        "prep-follow-up",
                        json.dumps({"node_id": node_id, "answer_id": answer_id}),
                        vector_memory._serialize(np.asarray(vector, dtype=np.float32)),
                        "user-a",
                    ),
                )

        hit = prepared_answer_matcher.match_prepared_answer(
            prep_id="prep-follow-up",
            user_id="user-a",
            utterance_embedding=[1.0, 0.0, 0.0],
            compiled_knowledge={"prepared_answers": answers},
            utterance_text="为什么要这么做？",
            last_node_id="previous",
        )
        self.assertTrue(hit["matched"])
        self.assertEqual(hit["node_id"], "previous")
        self.assertAlmostEqual(hit["semantic_score"], 0.75, places=3)


class KnowledgeCompilerTests(unittest.TestCase):
    def test_project_anchor_guarantees_six_connected_nodes(self):
        tree = {"root_nodes": ["existing"], "nodes": {
            "existing": {
                "id": "existing", "topic": "Rchain 总览", "sample_questions": ["介绍 Rchain"],
                "anchor_id": "anchor-rchain", "answer_kind": "overview", "children": [],
            },
        }}
        result = ensure_project_anchor_nodes(tree, [{
            "anchor_id": "anchor-rchain", "project_name": "Rchain",
            "aliases": ["Rchain"], "document_ids": ["doc-rchain"],
            "authoritative_document_ids": ["doc-rchain"],
        }])
        project_nodes = [
            node for node in result["nodes"].values()
            if node.get("anchor_id") == "anchor-rchain"
        ]
        self.assertEqual({node.get("answer_kind") for node in project_nodes}, {
            "overview", "architecture", "role", "challenge", "tradeoff", "result",
        })
        self.assertEqual(len(result["nodes"]["existing"]["children"]), 5)
    def test_strategy_tree_prompt_receives_resume_and_selected_materials_directly(self):
        captured = {}

        class FakeLLM:
            async def ainvoke(self, messages):
                captured["prompt"] = messages[-1].content
                return SimpleNamespace(content=json.dumps({
                    "root_nodes": [], "nodes": {}, "phase_order": [],
                }))

        with patch.object(prep_graph, "get_copilot_llm", return_value=FakeLLM()):
            asyncio.run(prep_graph._run_hr_strategy(
                "company",
                {"role_title": "Backend"},
                {"overall_fit": 90},
                {
                    "profile_summary": "profile fact",
                    "resume_context": "resume project fact",
                    "material_context": "selected document project detail",
                },
                "user-a",
            ))

        self.assertIn("resume project fact", captured["prompt"])
        self.assertIn("selected document project detail", captured["prompt"])

    def test_partial_failure_and_unselected_sources_are_isolated(self):
        class FakeLLM:
            async def ainvoke(self, messages):
                prompt_text = messages[-1].content
                if '"topic": "Broken"' in prompt_text:
                    raise RuntimeError("node failed")
                return SimpleNamespace(content=json.dumps({
                    "question_variants": ["How did you build it?"],
                    "prepared_answer": "I used the selected evidence.",
                    "short_answer": "Selected evidence.",
                    "key_points": ["review"],
                    "source_refs": [
                        {"source_type": "personal_document", "document_id": "doc-selected", "evidence": "supported"},
                        {"source_type": "personal_document", "document_id": "doc-foreign", "evidence": "must drop"},
                    ],
                    "confidence": 0.9,
                    "warnings": [],
                }))

        tree = {"nodes": {
            "good": {"topic": "Project", "intent": "project", "sample_questions": ["Tell me about it"]},
            "broken": {"topic": "Broken", "intent": "technical", "sample_questions": ["Break?"]},
        }}
        context = {
            "resume_context": "resume",
            "profile_summary": "profile",
            "selected_documents": [{"document_id": "doc-selected", "filename": "selected.md"}],
        }
        with (
            patch.object(knowledge_compiler, "get_copilot_llm", return_value=FakeLLM()),
            patch.object(knowledge_compiler, "retrieve_selected_materials", return_value=[]),
        ):
            result = asyncio.run(knowledge_compiler.compile_strategy_tree(
                strategy_tree=tree,
                jd_text="jd",
                candidate_context=context,
                fit_report={},
                prep_hints=[],
                user_id="user-a",
                document_ids=["doc-selected"],
            ))

        self.assertEqual(result["compile_stats"]["compiled_answers"], 1)
        self.assertEqual(result["uncompiled_nodes"][0]["node_id"], "broken")
        answer = next(iter(result["prepared_answers"].values()))
        self.assertEqual([source["document_id"] for source in answer["source_refs"]], ["doc-selected"])
        self.assertTrue(any("未选择" in warning for warning in answer["warnings"]))


class PreparedRealtimeRouteTests(unittest.TestCase):
    def test_prepared_hit_does_not_call_answer_coach(self):
        class Navigator:
            def get_node(self, node_id):
                return {"topic": "Project", "intent": "project", "children": [], "recommended_points": []}

            def get_children(self, node_id):
                return []

        websocket = SimpleNamespace(send_json=AsyncMock())
        session = {
            "navigator": Navigator(),
            "prep": {"compiled_knowledge": {"prepared_answers": {}}},
            "prep_id": "prep-1",
            "user_id": "user-a",
            "prepared_enabled": True,
            "conversation": [],
            "turn_count": 0,
            "last_node_id": None,
        }
        match = {
            "matched": True,
            "score": 0.95,
            "node_id": "project",
            "answer_id": "pa-1",
            "prepared_answer": "Prepared response",
            "short_answer": "Short response",
            "topic": "Project",
            "matched_question": "Question",
            "sources": [],
        }
        with (
            patch("backend.copilot.intent_classifier.classify_intent", new=AsyncMock(return_value={
                "node_id": "project", "intent": "project", "confidence": 0.9,
                "utterance_embedding": [1.0, 0.0],
            })),
            patch("backend.copilot.prepared_answer_matcher.match_prepared_answer", return_value=match),
            patch("backend.copilot.answer_advisor.prepare_advice_context", return_value={"prompt": "unused", "risk_alert": ""}),
            patch("backend.copilot.answer_advisor.stream_advice") as stream_advice,
            patch("backend.copilot.hr_profiler.should_run", return_value=False),
            patch.object(copilot, "_run_interview_monitor", new=AsyncMock()),
        ):
            asyncio.run(copilot._process_utterance(websocket, session, "Question"))

        stream_advice.assert_not_called()
        message_types = [call.args[0]["type"] for call in websocket.send_json.await_args_list]
        self.assertEqual(message_types, ["copilot_update", "prepared_answer"])


if __name__ == "__main__":
    unittest.main()

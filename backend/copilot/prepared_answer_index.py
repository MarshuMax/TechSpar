"""SQLite/NumPy index for precompiled Copilot question variants."""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np

from backend.llm_provider import get_embedding
from backend.vector_memory import _deserialize, _get_conn, _serialize

COPILOT_QUESTION_VARIANT = "copilot_question_variant"


def build_prepared_answer_index(
    *,
    prep_id: str,
    user_id: str,
    strategy_tree: dict,
    compiled_knowledge: dict,
) -> int:
    """Replace a prep's variant index atomically and return inserted row count."""
    if not prep_id:
        raise ValueError("prep_id is required to build the prepared answer index")
    answers = compiled_knowledge.get("prepared_answers", {})
    answer_by_node = {
        answer.get("node_id"): answer
        for answer in answers.values()
        if isinstance(answer, dict) and answer.get("node_id")
    }
    items: list[tuple[str, str, str | None, int]] = []
    for node_id, node in (strategy_tree.get("nodes") or {}).items():
        answer = answer_by_node.get(node_id)
        questions = list(node.get("sample_questions") or [])
        if answer:
            questions.extend(answer.get("question_variants") or [])
        for index, question in enumerate(dict.fromkeys(str(q).strip() for q in questions if str(q).strip())):
            items.append((question, node_id, answer.get("answer_id") if answer else None, index))

    vectors = get_embedding(user_id).get_text_embedding_batch([item[0] for item in items]) if items else []
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM memory_vectors WHERE chunk_type=? AND session_id=? AND user_id=?",
            (COPILOT_QUESTION_VARIANT, prep_id, user_id),
        )
        now = datetime.now().isoformat()
        for (question, node_id, answer_id, variant_index), vector in zip(items, vectors):
            metadata = json.dumps({
                "prep_id": prep_id,
                "node_id": node_id,
                "answer_id": answer_id,
                "variant_index": variant_index,
            }, ensure_ascii=False)
            conn.execute(
                "INSERT INTO memory_vectors "
                "(chunk_type, content, topic, session_id, metadata, embedding, user_id, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    COPILOT_QUESTION_VARIANT,
                    question,
                    prep_id,
                    metadata,
                    _serialize(np.asarray(vector, dtype=np.float32)),
                    user_id,
                    now,
                ),
            )
        conn.commit()
        return len(items)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_prepared_variant_rows(prep_id: str, user_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT content, metadata, embedding FROM memory_vectors "
        "WHERE chunk_type=? AND session_id=? AND user_id=?",
        (COPILOT_QUESTION_VARIANT, prep_id, user_id),
    ).fetchall()
    conn.close()
    result: list[dict] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except json.JSONDecodeError:
            metadata = {}
        result.append({
            "question": row["content"],
            "metadata": metadata,
            "embedding": _deserialize(row["embedding"]),
        })
    return result


def load_navigator_embeddings(prep_id: str, user_id: str) -> dict[str, list[tuple[str, list[float]]]]:
    grouped: dict[str, list[tuple[str, list[float]]]] = {}
    for row in load_prepared_variant_rows(prep_id, user_id):
        node_id = row["metadata"].get("node_id")
        if node_id:
            grouped.setdefault(node_id, []).append((row["question"], row["embedding"].tolist()))
    return grouped

"""SQLite/NumPy index for precompiled Copilot question variants."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from backend.llm_provider import get_embedding
from backend.vector_memory import _deserialize, _get_conn, _serialize

COPILOT_QUESTION_VARIANT = "copilot_question_variant"


@dataclass(frozen=True)
class PreparedIndexSnapshot:
    rows: tuple[dict, ...]
    matrix: np.ndarray
    node_ids: tuple[str, ...]
    node_centroids: np.ndarray
    aliases: tuple[tuple[str, str, str], ...]


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
    if len(vectors) != len(items):
        raise ValueError(f"embedding count mismatch: expected {len(items)}, got {len(vectors)}")
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


def load_prepared_index_snapshot(
    prep_id: str,
    user_id: str,
    compiled_knowledge: dict,
) -> PreparedIndexSnapshot | None:
    rows = load_prepared_variant_rows(prep_id, user_id)
    answers = compiled_knowledge.get("prepared_answers", {})
    rows = [
        row for row in rows
        if row["metadata"].get("node_id")
        and row["metadata"].get("answer_id") in answers
        and np.isfinite(row["embedding"]).all()
    ]
    if not rows:
        return None
    dimensions = {row["embedding"].shape for row in rows}
    if len(dimensions) != 1:
        return None
    matrix = np.stack([row["embedding"] for row in rows]).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.clip(norms, 1e-10, None)
    grouped: dict[str, list[np.ndarray]] = {}
    for row, vector in zip(rows, matrix):
        node_id = row["metadata"].get("node_id")
        if node_id:
            grouped.setdefault(node_id, []).append(vector)
    node_ids = tuple(grouped)
    node_centroids = np.stack([
        np.mean(grouped[node_id], axis=0) for node_id in node_ids
    ]).astype(np.float32) if node_ids else np.empty((0, matrix.shape[1]), dtype=np.float32)
    if len(node_centroids):
        node_centroids /= np.clip(np.linalg.norm(node_centroids, axis=1, keepdims=True), 1e-10, None)
    alias_pairs: list[tuple[str, str]] = []
    answer_by_anchor_kind = {
        (answer.get("anchor_id"), answer.get("answer_kind")): answer_id
        for answer_id, answer in answers.items()
        if isinstance(answer, dict) and answer.get("anchor_id") and answer.get("answer_kind")
    }
    for anchor in compiled_knowledge.get("project_anchors", []):
        for alias in anchor.get("aliases", []):
            normalized = "".join(str(alias).lower().split())
            for kind in ("overview", "architecture", "role", "challenge", "tradeoff", "result"):
                answer_id = answer_by_anchor_kind.get((anchor.get("anchor_id"), kind))
                if normalized and answer_id:
                    alias_pairs.append((normalized, kind, answer_id))
    return PreparedIndexSnapshot(
        rows=tuple(rows), matrix=matrix, node_ids=node_ids,
        node_centroids=node_centroids, aliases=tuple(alias_pairs),
    )


def replace_prepared_answer_index(
    *, prep_id: str, user_id: str, node: dict, answer: dict,
) -> int:
    """Re-embed only one edited answer's question variants."""
    questions = list(dict.fromkeys(
        str(value).strip()
        for value in [*(node.get("sample_questions") or []), *(answer.get("question_variants") or [])]
        if str(value).strip()
    ))
    vectors = get_embedding(user_id).get_text_embedding_batch(questions) if questions else []
    if len(vectors) != len(questions):
        raise ValueError(f"embedding count mismatch: expected {len(questions)}, got {len(vectors)}")
    conn = _get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM memory_vectors WHERE chunk_type=? AND session_id=? AND user_id=? "
            "AND json_extract(metadata, '$.answer_id')=?",
            (COPILOT_QUESTION_VARIANT, prep_id, user_id, answer["answer_id"]),
        )
        now = datetime.now().isoformat()
        for index, (question, vector) in enumerate(zip(questions, vectors)):
            metadata = json.dumps({
                "prep_id": prep_id,
                "node_id": answer.get("node_id"),
                "answer_id": answer["answer_id"],
                "variant_index": index,
            }, ensure_ascii=False)
            conn.execute(
                "INSERT INTO memory_vectors "
                "(chunk_type, content, topic, session_id, metadata, embedding, user_id, created_at) "
                "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                (COPILOT_QUESTION_VARIANT, question, prep_id, metadata,
                 _serialize(np.asarray(vector, dtype=np.float32)), user_id, now),
            )
        conn.commit()
        return len(questions)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_navigator_embeddings(prep_id: str, user_id: str) -> dict[str, list[tuple[str, list[float]]]]:
    grouped: dict[str, list[tuple[str, list[float]]]] = {}
    for row in load_prepared_variant_rows(prep_id, user_id):
        node_id = row["metadata"].get("node_id")
        if node_id:
            grouped.setdefault(node_id, []).append((row["question"], row["embedding"].tolist()))
    return grouped

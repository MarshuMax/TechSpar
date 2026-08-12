"""Low-latency matcher for precompiled Copilot answers."""
from __future__ import annotations

import numpy as np

from backend.copilot.prepared_answer_index import load_prepared_variant_rows

PREPARED_MATCH_THRESHOLD = 0.80
FOLLOW_UP_NODE_BONUS = 0.10


def _is_contextual_follow_up(text: str) -> bool:
    """Recognize short questions whose meaning depends on the previous turn."""
    normalized = "".join(text.lower().split())
    cues = (
        "为什么这么", "为什么这样", "为什么要这么", "为什么要这样",
        "这么做", "这样做", "具体呢", "然后呢", "后来呢", "怎么实现的",
        "遇到了什么", "如何解决的", "whataboutthat", "whydothat", "howexactly",
    )
    return len(normalized) <= 32 and any(cue in normalized for cue in cues)


def match_prepared_answer(
    *,
    prep_id: str,
    user_id: str,
    utterance_embedding: list[float] | None,
    compiled_knowledge: dict,
    utterance_text: str = "",
    last_node_id: str | None = None,
    top_k: int = 5,
    threshold: float = PREPARED_MATCH_THRESHOLD,
) -> dict:
    if not utterance_embedding:
        return {"matched": False, "score": 0.0, "reason": "embedding_unavailable"}
    rows = load_prepared_variant_rows(prep_id, user_id)
    if not rows:
        return {"matched": False, "score": 0.0, "reason": "index_missing"}

    query = np.asarray(utterance_embedding, dtype=np.float32)
    compatible = [row for row in rows if row["embedding"].shape == query.shape]
    if not compatible:
        return {"matched": False, "score": 0.0, "reason": "embedding_dimension_mismatch"}
    matrix = np.stack([row["embedding"] for row in compatible])
    query_norm = np.linalg.norm(query)
    row_norms = np.linalg.norm(matrix, axis=1)
    if query_norm < 1e-10:
        return {"matched": False, "score": 0.0, "reason": "empty_embedding"}
    semantic_scores = (matrix @ query) / (np.clip(row_norms, 1e-10, None) * query_norm)
    contextual_follow_up = bool(last_node_id and _is_contextual_follow_up(utterance_text))
    ranking_scores = semantic_scores.copy()
    if contextual_follow_up:
        for index, row in enumerate(compatible):
            if row["metadata"].get("node_id") == last_node_id:
                ranking_scores[index] = min(1.0, ranking_scores[index] + FOLLOW_UP_NODE_BONUS)
    order = np.argsort(ranking_scores)[::-1][:top_k]
    answers = compiled_knowledge.get("prepared_answers", {})
    candidates: list[dict] = []
    for index in order:
        row = compatible[int(index)]
        semantic_score = float(semantic_scores[int(index)])
        score = float(ranking_scores[int(index)])
        answer_id = row["metadata"].get("answer_id")
        answer = answers.get(answer_id) if answer_id else None
        candidates.append({
            "score": round(score, 4),
            "semantic_score": round(semantic_score, 4),
            "node_id": row["metadata"].get("node_id"),
            "answer_id": answer_id,
            "matched_question": row["question"],
        })
        if score >= threshold and answer and answer.get("usable", True):
            return {
                "matched": True,
                "score": round(score, 4),
                "semantic_score": round(semantic_score, 4),
                "node_id": answer.get("node_id"),
                "answer_id": answer_id,
                "matched_question": row["question"],
                "prepared_answer": answer.get("prepared_answer", ""),
                "short_answer": answer.get("short_answer", ""),
                "topic": answer.get("topic", ""),
                "sources": answer.get("source_refs", []),
                "candidates": candidates,
            }
    return {
        "matched": False,
        "score": candidates[0]["score"] if candidates else 0.0,
        "reason": "below_threshold",
        "candidates": candidates,
    }

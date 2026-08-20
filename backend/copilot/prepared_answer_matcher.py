"""Low-latency matcher for precompiled Copilot answers."""
from __future__ import annotations

import numpy as np

from backend.copilot.prepared_answer_index import (
    PreparedIndexSnapshot,
    load_prepared_index_snapshot,
)

PREPARED_MATCH_THRESHOLD = 0.74
PREPARED_MIN_SCORE_GAP = 0.025
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
    min_score_gap: float = PREPARED_MIN_SCORE_GAP,
    snapshot: PreparedIndexSnapshot | None = None,
) -> dict:
    if not utterance_embedding:
        return {"matched": False, "score": 0.0, "reason": "embedding_unavailable"}
    snapshot = snapshot or load_prepared_index_snapshot(prep_id, user_id, compiled_knowledge)
    if snapshot is None:
        return {"matched": False, "score": 0.0, "reason": "index_missing"}

    query = np.asarray(utterance_embedding, dtype=np.float32)
    if snapshot.matrix.shape[1:] != query.shape:
        return {"matched": False, "score": 0.0, "reason": "embedding_dimension_mismatch"}
    query_norm = np.linalg.norm(query)
    if query_norm < 1e-10:
        return {"matched": False, "score": 0.0, "reason": "empty_embedding"}
    normalized_query = query / query_norm

    # Stage 1 narrows the immutable in-memory snapshot to the most likely nodes.
    if len(snapshot.node_centroids):
        node_scores = snapshot.node_centroids @ normalized_query
        top_nodes = {
            snapshot.node_ids[int(index)]
            for index in np.argsort(node_scores)[::-1][:12]
        }
        row_indexes = [
            index for index, row in enumerate(snapshot.rows)
            if row["metadata"].get("node_id") in top_nodes
        ]
    else:
        row_indexes = list(range(len(snapshot.rows)))
    compatible = [snapshot.rows[index] for index in row_indexes]
    semantic_scores = snapshot.matrix[row_indexes] @ normalized_query
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
        candidates.append({
            "score": round(score, 4),
            "semantic_score": round(semantic_score, 4),
            "node_id": row["metadata"].get("node_id"),
            "answer_id": answer_id,
            "matched_question": row["question"],
        })
    for candidate in candidates:
        answer_id = candidate.get("answer_id")
        answer = answers.get(answer_id) if answer_id else None
        score = float(candidate["score"])
        semantic_score = float(candidate["semantic_score"])
        different_answer_scores = [
            other["score"] for other in candidates
            if other.get("answer_id") != answer_id
        ]
        score_gap = score - max(different_answer_scores, default=-1.0)
        decisive = score_gap >= min_score_gap or score >= 0.90 or (
            contextual_follow_up and answer and answer.get("node_id") == last_node_id
        )
        if score >= threshold and decisive and answer and answer.get("usable", True):
            return {
                "matched": True,
                "score": round(score, 4),
                "semantic_score": round(semantic_score, 4),
                "node_id": answer.get("node_id"),
                "answer_id": answer_id,
                "matched_question": candidate["matched_question"],
                "prepared_answer": answer.get("prepared_answer", ""),
                "expanded_answer": answer.get("expanded_answer", answer.get("prepared_answer", "")),
                "short_answer": answer.get("short_answer", ""),
                "topic": answer.get("topic", ""),
                "sources": answer.get("source_refs", []),
                "score_gap": round(score_gap, 4),
                "candidates": candidates,
            }
    return {
        "matched": False,
        "score": candidates[0]["score"] if candidates else 0.0,
        "reason": "below_threshold",
        "candidates": candidates,
    }


def match_alias_prepared_answer(
    *, snapshot: PreparedIndexSnapshot | None, compiled_knowledge: dict, utterance_text: str,
) -> dict | None:
    """Resolve explicit project-name questions before any embedding call."""
    if snapshot is None:
        return None
    normalized = "".join(utterance_text.lower().split())
    kind = "overview"
    kind_cues = {
        "architecture": ("架构", "技术方案", "设计", "architecture"),
        "role": ("负责", "职责", "贡献", "role", "responsib"),
        "challenge": ("难点", "挑战", "问题", "challenge"),
        "tradeoff": ("取舍", "权衡", "为什么不用", "tradeoff"),
        "result": ("结果", "成果", "复盘", "学到", "result"),
    }
    for candidate_kind, cues in kind_cues.items():
        if any(cue in normalized for cue in cues):
            kind = candidate_kind
            break
    matches = [
        (alias, answer_id) for alias, answer_kind, answer_id in snapshot.aliases
        if answer_kind == kind and alias in normalized
    ]
    if not matches:
        return None
    _, answer_id = max(matches, key=lambda item: len(item[0]))
    answer = compiled_knowledge.get("prepared_answers", {}).get(answer_id)
    if not answer or not answer.get("usable", True):
        return None
    return {
        "matched": True,
        "score": 1.0,
        "semantic_score": 1.0,
        "score_gap": 1.0,
        "reason": "project_alias",
        "node_id": answer.get("node_id"),
        "answer_id": answer_id,
        "matched_question": utterance_text,
        "prepared_answer": answer.get("prepared_answer", ""),
        "expanded_answer": answer.get("expanded_answer", answer.get("prepared_answer", "")),
        "short_answer": answer.get("short_answer", ""),
        "topic": answer.get("topic", ""),
        "sources": answer.get("source_refs", []),
        "candidates": [],
    }

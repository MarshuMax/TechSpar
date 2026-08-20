"""Offline compiler for interview-ready answers derived from a strategy tree."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from backend.copilot.material_context import retrieve_selected_materials
from backend.copilot.prompts import KNOWLEDGE_COMPILER_PROMPT
from backend.llm_provider import get_copilot_llm

logger = logging.getLogger("uvicorn")
COMPILER_CONCURRENCY = 3


def _strip_markdown(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1] if "\n" in value else value[3:]
        if value.endswith("```"):
            value = value[:-3]
    return value.strip()


def _answer_id(node_id: str) -> str:
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:12]
    return f"pa_{digest}"


def _normalize_compiled_answer(
    raw: dict,
    *,
    node_id: str,
    node: dict,
    selected_documents: dict[str, dict],
    evidence_hits: list[dict],
) -> dict:
    variants = [
        str(value).strip()
        for value in raw.get("question_variants", [])
        if str(value).strip()
    ]
    variants = list(dict.fromkeys([*(node.get("sample_questions") or []), *variants]))[:8]
    source_refs: list[dict] = []
    warnings = [str(item) for item in raw.get("warnings", []) if str(item).strip()]
    first_hit_by_document = {
        hit["document_id"]: hit for hit in evidence_hits
        if isinstance(hit, dict) and hit.get("document_id")
    }
    for source in raw.get("source_refs", []):
        if not isinstance(source, dict):
            continue
        source_type = source.get("source_type")
        if source_type == "personal_document":
            document = selected_documents.get(str(source.get("document_id", "")))
            if not document:
                warnings.append("模型返回了未选择的资料引用，已移除")
                continue
            hit = first_hit_by_document.get(document["document_id"], {})
            source_refs.append({
                "source_type": "personal_document",
                "document_id": document["document_id"],
                "filename": document["filename"],
                "role": document.get("role", "supporting_material"),
                "chunk_id": hit.get("chunk_id", ""),
                "content_hash": hit.get("content_hash", ""),
                "evidence": str(source.get("evidence", ""))[:500],
            })
        elif source_type in {"resume", "profile"}:
            source_refs.append({
                "source_type": source_type,
                "evidence": str(source.get("evidence", ""))[:500],
            })

    prepared_answer = str(raw.get("prepared_answer", "")).strip()
    expanded_answer = str(raw.get("expanded_answer", "")).strip() or prepared_answer
    short_answer = str(raw.get("short_answer", "")).strip()
    return {
        "answer_id": _answer_id(node_id),
        "node_id": node_id,
        "topic": node.get("topic", ""),
        "intent": node.get("intent", "unknown"),
        "anchor_id": node.get("anchor_id"),
        "answer_kind": node.get("answer_kind"),
        "project_name": node.get("project_name"),
        "question_variants": variants,
        "prepared_answer": prepared_answer,
        "expanded_answer": expanded_answer,
        "short_answer": short_answer,
        "key_points": [str(item) for item in raw.get("key_points", []) if str(item).strip()][:8],
        "source_refs": source_refs,
        "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0.0) or 0.0))),
        "usable": bool(prepared_answer and variants),
        "edit_version": 1,
        "warnings": list(dict.fromkeys(warnings)),
    }


async def compile_strategy_tree(
    *,
    strategy_tree: dict,
    jd_text: str,
    candidate_context: dict,
    fit_report: dict,
    prep_hints: list[dict],
    user_id: str,
    document_ids: list[str],
    materials: list[dict] | None = None,
    on_progress=None,
) -> dict:
    nodes = [
        (node_id, node)
        for node_id, node in (strategy_tree.get("nodes") or {}).items()
        if isinstance(node, dict) and node.get("sample_questions")
    ]
    selected_documents = {
        item["document_id"]: item for item in candidate_context.get("selected_documents", [])
    }
    hints = {item.get("node_id"): item for item in prep_hints if isinstance(item, dict)}
    semaphore = asyncio.Semaphore(COMPILER_CONCURRENCY)
    completed = 0
    progress_lock = asyncio.Lock()
    node_hits: dict[str, list[dict]] = {}
    # Retrieval uses a single selected-document boundary and is intentionally
    # completed before concurrent LLM compilation. This avoids concurrent
    # embedding-provider calls while preserving parallel answer generation.
    for node_id, node in nodes:
        query = "；".join([
            node.get("topic", ""),
            *(node.get("sample_questions") or []),
            *(node.get("recommended_points") or []),
        ])
        node_document_ids = [
            value for value in (node.get("document_ids") or document_ids)
            if value in selected_documents
        ] or document_ids
        node_hits[node_id] = retrieve_selected_materials(
            query=query,
            user_id=user_id,
            document_ids=node_document_ids,
            top_k=max(6, len(node_document_ids)),
            min_per_document=1 if node.get("anchor_id") else 0,
        )

    async def compile_one(node_id: str, node: dict) -> tuple[str, dict | None, str | None]:
        nonlocal completed
        hits = node_hits[node_id]
        roles = {
            item.get("document_id"): item.get("role")
            for item in (materials or []) if isinstance(item, dict)
        }
        hits = sorted(hits, key=lambda hit: (
            roles.get(hit.get("document_id")) != "authoritative_script",
            -float(hit.get("score", 0)),
        ))
        material_context = "\n\n---\n\n".join(
            f"[document_id={hit['document_id']} source={hit['source']}]\n{hit['content']}"
            for hit in hits
        )[:9000] or "本节点没有命中所选个人资料"
        prompt = KNOWLEDGE_COMPILER_PROMPT.format(
            jd_text=jd_text[:5000],
            node=json.dumps(node, ensure_ascii=False),
            resume_context=str(candidate_context.get("resume_context", ""))[:5000],
            profile_summary=str(candidate_context.get("profile_summary", ""))[:3000],
            material_context=material_context,
            fit_report=json.dumps(fit_report, ensure_ascii=False)[:4000],
            risk_hint=json.dumps(hints.get(node_id, {}), ensure_ascii=False)[:2000],
        )
        error = None
        compiled = None
        try:
            async with semaphore:
                response = await get_copilot_llm(user_id).ainvoke([
                    SystemMessage(content="你是面试知识编译器。只返回 JSON。"),
                    HumanMessage(content=prompt),
                ])
            raw = json.loads(_strip_markdown(response.content))
            compiled = _normalize_compiled_answer(
                raw,
                node_id=node_id,
                node=node,
                selected_documents=selected_documents,
                evidence_hits=hits,
            )
            if not compiled["usable"]:
                error = "编译结果缺少答案或问题变体"
                compiled = None
        except Exception as exc:
            logger.warning("Knowledge compile failed for node %s: %s", node_id, exc)
            error = str(exc)[:500]
        async with progress_lock:
            completed += 1
            if on_progress:
                await on_progress(completed, len(nodes))
        return node_id, compiled, error

    results = await asyncio.gather(*(compile_one(node_id, node) for node_id, node in nodes))
    prepared_answers: dict[str, dict] = {}
    uncompiled_nodes: list[dict] = []
    for node_id, compiled, error in results:
        if compiled:
            prepared_answers[compiled["answer_id"]] = compiled
        else:
            uncompiled_nodes.append({"node_id": node_id, "error": error or "unknown"})
    variant_count = sum(len(item["question_variants"]) for item in prepared_answers.values())
    return {
        "version": 2,
        "document_ids": document_ids,
        "materials": materials or [],
        "project_anchors": candidate_context.get("project_anchors", []),
        "source_snapshot": candidate_context.get("selected_documents", []),
        "prepared_answers": prepared_answers,
        "uncompiled_nodes": uncompiled_nodes,
        "compile_stats": {
            "strategy_nodes": len(nodes),
            "compiled_answers": len(prepared_answers),
            "question_variants": variant_count,
            "source_documents": len(document_ids),
        },
        "index_status": "pending",
    }

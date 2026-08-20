"""Build user-scoped candidate context for Copilot prep and compilation."""
from __future__ import annotations

import asyncio

from backend.config import settings
from backend.indexer import query_resume
from backend.memory import get_profile, get_profile_summary
from backend.personal_agent import get_documents_by_ids, search_documents
from backend.copilot.project_anchors import extract_project_anchors

MATERIAL_CONTEXT_CHAR_BUDGET = 24_000
MATERIAL_ROLES = {"authoritative_script", "supporting_material"}


def _has_resume(user_id: str) -> bool:
    resume_dir = settings.user_resume_path(user_id)
    return resume_dir.exists() and any(
        path.is_file() and path.suffix.lower() == ".pdf" for path in resume_dir.iterdir()
    )


def retrieve_selected_materials(
    *,
    query: str,
    user_id: str,
    document_ids: list[str],
    top_k: int = 6,
    query_embedding: list[float] | None = None,
    min_per_document: int = 0,
    materials: list[dict] | None = None,
) -> list[dict]:
    if not document_ids:
        return []
    hits = search_documents(
        query,
        user_id,
        top_k=top_k,
        document_ids=document_ids,
        query_embedding=query_embedding,
        min_per_document=min_per_document,
    )
    roles = {
        item.get("document_id"): item.get("role")
        for item in (materials or []) if isinstance(item, dict)
    }
    # Authoritative scripts are a hard evidence tier. They remain first whenever
    # relevant, while similarity still orders hits inside each tier.
    return sorted(
        hits,
        key=lambda hit: (
            roles.get(hit.get("document_id")) != "authoritative_script",
            -float(hit.get("score", 0)),
        ),
    )


async def build_candidate_context(
    *,
    user_id: str,
    jd_text: str,
    document_ids: list[str],
    materials: list[dict] | None = None,
) -> dict:
    """Build bounded prep context while enforcing the selected-document boundary."""
    selected_documents = get_documents_by_ids(document_ids, user_id, require_ready=True)
    roles = {
        item.get("document_id"): item.get("role")
        for item in (materials or [])
        if isinstance(item, dict) and item.get("role") in MATERIAL_ROLES
    }
    selected_documents = [
        {**item, "role": roles.get(item["document_id"], "supporting_material")}
        for item in selected_documents
    ]
    resume_context = "未上传简历"
    if _has_resume(user_id):
        try:
            resume_context = str(await asyncio.to_thread(
                query_resume,
                "总结候选人的项目经历、职责、技术栈、工程实践和量化结果",
                user_id,
                top_k=5,
            ))[:6000]
        except Exception:
            resume_context = "简历检索失败"

    material_hits = await asyncio.to_thread(
        retrieve_selected_materials,
        query=f"目标岗位要求、相关项目经历、技术方案、职责和成果：{jd_text[:2500]}",
        user_id=user_id,
        document_ids=document_ids,
        top_k=max(12, len(document_ids) + 6),
        min_per_document=1,
        materials=materials,
    )
    parts: list[str] = []
    for hit in material_hits:
        parts.append(
            f"[chunk_id={hit.get('chunk_id', '')} document_id={hit['document_id']} "
            f"role={roles.get(hit['document_id'], 'supporting_material')} "
            f"source={hit['source']}]\n{str(hit['content'])[:1200]}"
        )
    material_context = "\n\n---\n\n".join(parts)[:MATERIAL_CONTEXT_CHAR_BUDGET]
    project_anchors = await extract_project_anchors(
        selected_documents=selected_documents,
        material_hits=material_hits,
        user_id=user_id,
    )
    return {
        "resume_context": resume_context,
        "profile": get_profile(user_id),
        "profile_summary": get_profile_summary(user_id),
        "selected_documents": [
            {
                "document_id": item["document_id"],
                "filename": item["filename"],
                "updated_at": item.get("updated_at", ""),
                "role": item.get("role", "supporting_material"),
            }
            for item in selected_documents
        ],
        "material_context": material_context,
        "material_hits": material_hits,
        "project_anchors": project_anchors,
    }

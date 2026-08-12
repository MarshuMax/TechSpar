"""Build user-scoped candidate context for Copilot prep and compilation."""
from __future__ import annotations

import asyncio

from backend.config import settings
from backend.indexer import query_resume
from backend.memory import get_profile, get_profile_summary
from backend.personal_agent import get_documents_by_ids, search_documents

MATERIAL_CONTEXT_CHAR_BUDGET = 12_000


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
) -> list[dict]:
    if not document_ids:
        return []
    return search_documents(
        query,
        user_id,
        top_k=top_k,
        document_ids=document_ids,
    )


async def build_candidate_context(
    *,
    user_id: str,
    jd_text: str,
    document_ids: list[str],
) -> dict:
    """Build bounded prep context while enforcing the selected-document boundary."""
    selected_documents = get_documents_by_ids(document_ids, user_id, require_ready=True)
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
        top_k=10,
    )
    parts: list[str] = []
    for hit in material_hits:
        parts.append(
            f"[document_id={hit['document_id']} source={hit['source']}]\n{hit['content']}"
        )
    material_context = "\n\n---\n\n".join(parts)[:MATERIAL_CONTEXT_CHAR_BUDGET]
    return {
        "resume_context": resume_context,
        "profile": get_profile(user_id),
        "profile_summary": get_profile_summary(user_id),
        "selected_documents": [
            {
                "document_id": item["document_id"],
                "filename": item["filename"],
                "updated_at": item.get("updated_at", ""),
            }
            for item in selected_documents
        ],
        "material_context": material_context,
        "material_hits": material_hits,
    }

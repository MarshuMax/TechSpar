"""Copilot Prep Phase — LangGraph 多 Agent 预处理流水线。

拓扑: fan-out(Company Researcher, JD Analyst, Fit Analyzer) → fan-in
      → HR Strategy Simulator → Risk Assessor → END
"""
import asyncio
import json
import logging

from langchain_core.messages import SystemMessage, HumanMessage

from backend.llm_provider import get_copilot_llm
from backend.memory import get_profile
from backend.copilot.material_context import build_candidate_context
from backend.copilot.knowledge_compiler import compile_strategy_tree
from backend.copilot.company_search import search_company
from backend.copilot.prompts import (
    JD_ANALYST_PROMPT,
    FIT_ANALYZER_PROMPT,
    HR_STRATEGY_PROMPT,
    RISK_ASSESSOR_PROMPT,
)
from backend.copilot.strategy_tree import parse_strategy_tree

logger = logging.getLogger("uvicorn")


async def _run_company_researcher(company: str, position: str) -> str:
    """Agent 1: 搜索公司信息。"""
    return await search_company(company, position)


async def _run_jd_analyst(jd_text: str) -> dict:
    """Agent 2: 拆解 JD。"""
    llm = get_copilot_llm()
    prompt = JD_ANALYST_PROMPT.format(jd_text=jd_text[:6000])
    resp = await llm.ainvoke([
        SystemMessage(content="你是 JD 分析引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ])
    try:
        return json.loads(_strip_markdown(resp.content))
    except json.JSONDecodeError:
        logger.error(f"JD analysis parse failed: {resp.content[:300]}")
        return {"role_title": "", "required_skills": [], "likely_question_dimensions": []}


async def _run_fit_analyzer(jd_text: str, candidate_context: dict, user_id: str) -> dict:
    """Agent 3: 简历-JD 匹配分析。"""
    resume_context = candidate_context.get("resume_context", "未上传简历")
    profile_summary = candidate_context.get("profile_summary", "")
    llm = get_copilot_llm()
    prompt = FIT_ANALYZER_PROMPT.format(
        jd_text=jd_text[:6000],
        resume_context=resume_context,
        profile_summary=profile_summary,
        material_context=candidate_context.get("material_context", "")[:8000] or "未选择个人面试资料",
    )
    resp = await llm.ainvoke([
        SystemMessage(content="你是匹配分析引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ])
    try:
        return json.loads(_strip_markdown(resp.content))
    except json.JSONDecodeError:
        logger.error(f"Fit analysis parse failed: {resp.content[:300]}")
        return {"overall_fit": 0, "highlights": [], "gaps": [], "talking_points": []}


async def _run_hr_strategy(
    company_report: str,
    jd_analysis: dict,
    fit_report: dict,
    candidate_context: dict,
    user_id: str,
) -> dict:
    """Agent 4: 生成提问策略树。"""
    jd_analysis_str = json.dumps(jd_analysis, ensure_ascii=False, indent=2)
    fit_report_str = json.dumps(fit_report, ensure_ascii=False, indent=2)
    profile_summary = candidate_context.get("profile_summary", "")

    role_title = jd_analysis.get("role_title", "技术岗位")

    llm = get_copilot_llm()
    prompt = HR_STRATEGY_PROMPT.format(
        role_title=role_title,
        company_report=company_report[:3000],
        jd_analysis=jd_analysis_str[:3000],
        fit_report=fit_report_str[:3000],
        profile_summary=profile_summary[:3000],
        resume_context=str(candidate_context.get("resume_context", ""))[:5000],
        material_context=(
            str(candidate_context.get("material_context", ""))[:8000]
            or "本次未选择个人面试资料"
        ),
    )
    resp = await llm.ainvoke([
        SystemMessage(content="你是面试策略引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ])
    return parse_strategy_tree(resp.content)


async def _run_risk_assessor(
    strategy_tree: dict,
    profile: dict,
    fit_report: dict,
) -> tuple[list[dict], list[dict], str]:
    """Agent 5: 风险评估。"""
    nodes = strategy_tree.get("nodes", {})
    risk_nodes = [
        {"node_id": nid, "topic": n.get("topic", ""), "risk_level": n.get("risk_level", "safe")}
        for nid, n in nodes.items()
        if n.get("risk_level") in ("danger", "caution")
    ]

    if not risk_nodes:
        return [], [], ""

    weak_points = profile.get("weak_points", [])
    weak_text = json.dumps(weak_points[:10], ensure_ascii=False)
    gaps = fit_report.get("gaps", []) if isinstance(fit_report, dict) else []
    gaps_text = json.dumps(gaps[:10], ensure_ascii=False)
    risk_text = json.dumps(risk_nodes, ensure_ascii=False)

    llm = get_copilot_llm()
    prompt = RISK_ASSESSOR_PROMPT.format(
        weak_points=weak_text,
        gaps=gaps_text,
        risk_nodes=risk_text,
    )
    resp = await llm.ainvoke([
        SystemMessage(content="你是风险评估引擎。只返回 JSON。"),
        HumanMessage(content=prompt),
    ])
    try:
        result = json.loads(_strip_markdown(resp.content))
        return result.get("risk_map", []), result.get("prep_hints", []), result.get("risk_summary", "")
    except json.JSONDecodeError:
        logger.error(f"Risk assessment parse failed: {resp.content[:300]}")
        return [], [], ""


async def run_copilot_prep(
    jd_text: str,
    user_id: str,
    company: str = "",
    position: str = "",
    document_ids: list[str] | None = None,
    prep_id: str = "",
    on_progress=None,
) -> dict:
    """执行完整的 Copilot Prep Pipeline。

    Args:
        on_progress: async callback(progress_text) for status updates
    Returns:
        CopilotPrepState dict
    """
    from backend.user_context import set_current_user

    # Bind user for the whole prep pipeline — nested copilot subsystem calls
    # (company/jd analysts, strategy tree) resolve this user's LLM/embedding via
    # the ContextVar; asyncio.create_task below copies the context.
    set_current_user(user_id)

    document_ids = document_ids or []
    profile = get_profile(user_id)

    # Layer 0: 三个 Analyst 并行
    if on_progress:
        await on_progress("正在并行分析公司信息、岗位要求和简历匹配度...")

    company_task = asyncio.create_task(_run_company_researcher(company, position))
    jd_task = asyncio.create_task(_run_jd_analyst(jd_text))
    context_task = asyncio.create_task(build_candidate_context(
        user_id=user_id, jd_text=jd_text, document_ids=document_ids,
    ))

    company_report, jd_analysis, candidate_context = await asyncio.gather(
        company_task, jd_task, context_task,
    )
    fit_report = await _run_fit_analyzer(jd_text, candidate_context, user_id)

    # Layer 1: HR Strategy Simulator
    if on_progress:
        await on_progress("正在生成 HR 提问策略树...")

    strategy_tree = await _run_hr_strategy(
        company_report, jd_analysis, fit_report, candidate_context, user_id,
    )

    # Layer 2: Risk Assessor
    if on_progress:
        await on_progress("正在评估风险路径...")

    risk_map, prep_hints, risk_summary = await _run_risk_assessor(strategy_tree, profile, fit_report)

    if on_progress:
        await on_progress("正在预编译面试答案 0 / %d..." % len(strategy_tree.get("nodes", {})))

    async def compiler_progress(done: int, total: int):
        if on_progress:
            await on_progress(f"正在预编译面试答案 {done} / {total}...")

    compiled_knowledge = await compile_strategy_tree(
        strategy_tree=strategy_tree,
        jd_text=jd_text,
        candidate_context=candidate_context,
        fit_report=fit_report,
        prep_hints=prep_hints,
        user_id=user_id,
        document_ids=document_ids,
        on_progress=compiler_progress,
    )

    if on_progress:
        await on_progress("正在建立高速检索索引...")
    from backend.copilot.prepared_answer_index import build_prepared_answer_index
    try:
        await asyncio.to_thread(
            build_prepared_answer_index,
            prep_id=prep_id,
            user_id=user_id,
            strategy_tree=strategy_tree,
            compiled_knowledge=compiled_knowledge,
        )
        compiled_knowledge["index_status"] = "ready"
    except Exception as exc:
        logger.warning("Prepared answer index build failed for prep %s: %s", prep_id, exc)
        compiled_knowledge["index_status"] = "error"
        compiled_knowledge["index_error"] = str(exc)[:500]

    return {
        "user_id": user_id,
        "jd_text": jd_text,
        "resume_context": candidate_context.get("resume_context", ""),
        "profile": profile,
        "document_ids": document_ids,
        "candidate_context": candidate_context,
        "company_report": company_report,
        "jd_analysis": jd_analysis,
        "fit_report": fit_report,
        "question_strategy_tree": strategy_tree,
        "risk_map": risk_map,
        "risk_summary": risk_summary,
        "prep_hints": prep_hints,
        "compiled_knowledge": compiled_knowledge,
        "status": "done",
        "progress": "准备完成",
        "error": "",
    }


def _strip_markdown(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()

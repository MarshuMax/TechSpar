"""Project-anchor extraction and deterministic strategy coverage."""
from __future__ import annotations

import hashlib
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from backend.llm_provider import get_copilot_llm

_PROJECT_MARKERS = re.compile(
    r"(?i)(项目|project|案例|case study|架构|architecture|复盘|overview|总览)"
)
_TRAILING_LABELS = re.compile(
    r"(?i)[\s_\-—]*(项目)?(总览|overview|面试稿|面试资料|定稿|复盘|技术方案|架构|"
    r"三分钟讲清|qa|faq|questions?|case study).*$"
)


def _strip_fences(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _filename_project_name(filename: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", filename).strip()
    stem = re.sub(r"^[\d\s._\-]+", "", stem)
    stem = re.sub(r"[（(\[].*?[）)\]]", " ", stem).strip()
    candidate = _TRAILING_LABELS.sub("", stem).strip(" _-—")
    if not candidate:
        marker = _PROJECT_MARKERS.search(stem)
        candidate = stem[: marker.start()].strip(" _-—") if marker else ""
    return candidate[:80]


def _anchor_id(name: str) -> str:
    return "anchor_" + hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:10]


def _fallback_anchors(selected_documents: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for document in selected_documents:
        filename = str(document.get("filename", ""))
        role = document.get("role", "supporting_material")
        if not _PROJECT_MARKERS.search(filename):
            continue
        name = _filename_project_name(filename)
        if not name or name.lower() in {"项目", "project", "面试", "interview"}:
            continue
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", name.lower())
        item = grouped.setdefault(key, {
            "anchor_id": _anchor_id(name),
            "project_name": name,
            "aliases": [name],
            "document_ids": [],
            "authoritative_document_ids": [],
        })
        item["document_ids"].append(document["document_id"])
        if role == "authoritative_script":
            item["authoritative_document_ids"].append(document["document_id"])
    return list(grouped.values())[:8]


async def extract_project_anchors(
    *,
    selected_documents: list[dict],
    material_hits: list[dict],
    user_id: str,
) -> list[dict]:
    """Extract canonical project names while keeping deterministic filename coverage."""
    fallback = _fallback_anchors(selected_documents)
    if not selected_documents:
        return fallback
    hit_by_document: dict[str, list[str]] = {}
    for hit in material_hits:
        hit_by_document.setdefault(hit["document_id"], []).append(str(hit["content"])[:900])
    evidence = [
        {
            "document_id": item["document_id"],
            "filename": item["filename"],
            "role": item.get("role", "supporting_material"),
            "excerpt": "\n".join(hit_by_document.get(item["document_id"], []))[:1200],
        }
        for item in selected_documents
    ]
    prompt = f"""从下面候选人资料中识别真实的项目经历，并合并同一项目的多个文件。
只返回 JSON：{{"projects":[{{"project_name":"项目规范名称","aliases":["简称/中英文别名"],"document_ids":["资料ID"]}}]}}。
不要把自我介绍、行为题、通用技术笔记当作项目；document_ids 只能来自输入。

资料：{json.dumps(evidence, ensure_ascii=False)}"""
    try:
        response = await get_copilot_llm(user_id).ainvoke([
            SystemMessage(content="你是项目资料归并器。只输出 JSON。"),
            HumanMessage(content=prompt),
        ])
        raw_projects = json.loads(_strip_fences(response.content)).get("projects", [])
    except Exception:
        raw_projects = []

    documents = {item["document_id"]: item for item in selected_documents}
    anchors: list[dict] = []
    seen_names: set[str] = set()
    for project in raw_projects:
        if not isinstance(project, dict):
            continue
        name = str(project.get("project_name", "")).strip()[:80]
        document_ids = list(dict.fromkeys(
            value for value in project.get("document_ids", []) if value in documents
        ))
        if not name or not document_ids:
            continue
        normalized = re.sub(r"\s+", "", name.lower())
        if normalized in seen_names:
            continue
        seen_names.add(normalized)
        aliases = list(dict.fromkeys([
            name,
            *(str(value).strip() for value in project.get("aliases", []) if str(value).strip()),
        ]))[:8]
        anchors.append({
            "anchor_id": _anchor_id(name),
            "project_name": name,
            "aliases": aliases,
            "document_ids": document_ids,
            "authoritative_document_ids": [
                value for value in document_ids
                if documents[value].get("role") == "authoritative_script"
            ],
        })

    # Filename-derived anchors are a safety net: the LLM may never erase an
    # explicitly selected project overview/script from the strategy coverage.
    for item in fallback:
        if any(
            set(item["document_ids"]) & set(existing["document_ids"])
            or item["project_name"].lower() in {a.lower() for a in existing["aliases"]}
            for existing in anchors
        ):
            continue
        anchors.append(item)
    return anchors[:8]


_PROJECT_NODE_KINDS = {
    "overview": ("项目总览", ["请介绍一下{name}项目", "你能完整讲讲{name}吗？", "Tell me about {name}."]),
    "architecture": ("架构与技术方案", ["{name}的整体架构是怎样的？", "{name}为什么采用这套技术方案？"]),
    "role": ("个人职责", ["你在{name}中具体负责什么？", "你对{name}的核心贡献是什么？"]),
    "challenge": ("难点与解决", ["{name}最难的问题是什么，怎么解决的？", "{name}遇到过哪些挑战？"]),
    "tradeoff": ("取舍与决策", ["{name}中做过哪些技术取舍？", "为什么没有选择其他方案？"]),
    "result": ("结果与复盘", ["{name}最终取得了什么结果？", "从{name}中学到了什么？"]),
}


def ensure_project_anchor_nodes(strategy_tree: dict, anchors: list[dict]) -> dict:
    """Guarantee six interview intents for every selected project anchor."""
    nodes = strategy_tree.setdefault("nodes", {})
    roots = strategy_tree.setdefault("root_nodes", [])
    for anchor in anchors:
        anchor_id = anchor["anchor_id"]
        parent_id = next((
            node_id for node_id, node in nodes.items()
            if isinstance(node, dict)
            and node.get("anchor_id") == anchor_id
            and node.get("answer_kind") == "overview"
        ), None)
        existing_kinds = {
            node.get("answer_kind")
            for node in nodes.values()
            if isinstance(node, dict) and node.get("anchor_id") == anchor_id
        }
        for kind, (label, questions) in _PROJECT_NODE_KINDS.items():
            if kind in existing_kinds:
                continue
            node_id = f"project_{anchor_id.removeprefix('anchor_')}_{kind}"
            name = anchor["project_name"]
            node = {
                "id": node_id,
                "topic": f"{name} · {label}",
                "sample_questions": [value.format(name=name) for value in questions],
                "intent": "project",
                "depth": 0 if kind == "overview" else 1,
                "risk_level": "safe",
                "children": [],
                "trigger_condition": f"面试官询问{name}的{label}",
                "recommended_points": [f"严格依据{name}的已选资料回答", "优先给结论，再补关键证据"],
                "anchor_id": anchor_id,
                "answer_kind": kind,
                "project_name": name,
                "document_ids": anchor["document_ids"],
            }
            nodes[node_id] = node
            if kind == "overview":
                parent_id = node_id
                if node_id not in roots:
                    roots.append(node_id)
            elif parent_id and parent_id in nodes:
                nodes[parent_id]["children"].append(node_id)
    return strategy_tree

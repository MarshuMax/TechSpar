"""策略树数据结构、embedding 预计算与节点匹配。"""
import asyncio
import hashlib
import json
import logging
import numpy as np
from pathlib import Path
from typing import Any

from backend.llm_provider import get_embedding, resolve_embedding_config

logger = logging.getLogger("uvicorn")

# 策略树 embedding 落盘缓存目录（在挂载卷 data/ 下，持久化）
_EMB_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "copilot_emb_cache"
# 并发上限：embedding API 常见限流，8 路并发 + 单条 ~1s，112 题约 15-20 秒
_EMBED_CONCURRENCY = 8


class StrategyTreeNavigator:
    """在预计算的策略树上做 embedding 匹配导航。"""

    def __init__(self, tree: dict):
        self.tree = tree
        self.nodes: dict = tree.get("nodes", {})
        self.root_nodes: list[str] = tree.get("root_nodes", [])
        self._embeddings: dict[str, list[tuple[str, list[float]]]] = {}
        self._current_position: str | None = None

    # ---------- embedding 缓存 ----------
    def _cache_key(self) -> str:
        """树内容 + embedding 配置签名；key 不落盘但参与哈希，换 key 自动失效。"""
        c = resolve_embedding_config()
        sig = json.dumps(
            {
                "tree": self.tree,
                "backend": c.get("backend"),
                "api_base": c.get("api_base"),
                "model": c.get("api_model") or c.get("local_model"),
                "key": c.get("api_key"),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(sig.encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict | None:
        p = _EMB_CACHE_DIR / f"{self._cache_key()}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load embedding cache: {e}")
            return None

    def _save_cache(self, data: dict):
        try:
            _EMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            p = _EMB_CACHE_DIR / f"{self._cache_key()}.json"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            logger.warning(f"Failed to save embedding cache: {e}")

    async def precompute_embeddings(self):
        """预计算所有节点 sample_questions 的 embedding。

        同步 API 调用放到线程池执行，避免阻塞事件循环导致
        WebSocket 心跳超时断连；并发受 _EMBED_CONCURRENCY 限制；
        结果按树+配置签名落盘缓存，同一 prep 再次打开秒进。

        新版主路径会在 Prep 阶段写入 prepared index，再由
        load_embeddings() 直接加载；本方法仅作为兼容模式 fallback。
        """
        cached = self._load_cache()
        if cached is not None:
            self._embeddings = cached
            logger.info(
                f"Loaded strategy tree embeddings from cache ({len(self._embeddings)} nodes)"
            )
            return

        embed_model = get_embedding()
        sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

        async def embed_one(q: str):
            async with sem:
                try:
                    emb = await asyncio.to_thread(embed_model.get_text_embedding, q)
                    return q, emb
                except Exception as e:
                    logger.warning(f"Failed to embed question '{q[:30]}...': {e}")
                    return q, None

        for node_id, node in self.nodes.items():
            questions = node.get("sample_questions", [])
            if not questions:
                continue
            results = await asyncio.gather(*(embed_one(q) for q in questions))
            self._embeddings[node_id] = [
                (q, emb) for q, emb in results if emb is not None
            ]

        self._save_cache(self._embeddings)
        logger.info(f"Precomputed embeddings for {len(self._embeddings)} strategy tree nodes")

    def load_embeddings(self, embeddings: dict[str, list[tuple[str, list[float]]]]) -> None:
        """Load embeddings built during Prep instead of recomputing at realtime init."""
        self._embeddings = embeddings

    def match_utterance(self, utterance_embedding: list[float], threshold: float = 0.45) -> tuple[str | None, str | None, float]:
        """匹配 utterance 到最相似的策略树节点。

        Returns: (node_id, intent, similarity_score)
        """
        best_score = -1.0
        best_node_id = None
        utt_vec = np.array(utterance_embedding, dtype=np.float32)
        utt_norm = np.linalg.norm(utt_vec)
        if utt_norm == 0:
            return None, None, 0.0

        for node_id, embs in self._embeddings.items():
            for _, emb in embs:
                emb_vec = np.array(emb, dtype=np.float32)
                emb_norm = np.linalg.norm(emb_vec)
                if emb_norm == 0:
                    continue
                score = float(np.dot(utt_vec, emb_vec) / (utt_norm * emb_norm))
                if score > best_score:
                    best_score = score
                    best_node_id = node_id

        if best_score < threshold or best_node_id is None:
            return None, None, best_score

        node = self.nodes[best_node_id]
        self._current_position = best_node_id
        return best_node_id, node.get("intent", "unknown"), best_score

    def get_children(self, node_id: str) -> list[dict]:
        """获取节点的子节点（追问方向）。"""
        node = self.nodes.get(node_id)
        if not node:
            return []
        return [
            self.nodes[cid]
            for cid in node.get("children", [])
            if cid in self.nodes
        ]

    def get_node(self, node_id: str) -> dict | None:
        return self.nodes.get(node_id)

    @property
    def current_position(self) -> str | None:
        return self._current_position


def parse_strategy_tree(raw_json: str) -> dict:
    """从 LLM 输出解析策略树 JSON。"""
    try:
        text = raw_json.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse strategy tree JSON: {raw_json[:200]}")
        return {"root_nodes": [], "nodes": {}, "phase_order": []}

"""知识库加载与检索（轻量 RAG：关键词 + 中文二元组打分，按身份过滤）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

KB_FILES = [
    "kb_products.json",
    "kb_business.json",
    "kb_branch.json",
    "kb_complaints.json",
    "kb_policies_internal.json",
]

_PUNCT = re.compile(r"[\s，。、；：！？,.;:!?（）()【】\[\]“”\"'·—\-/]+")


def _norm(text: str) -> str:
    return _PUNCT.sub("", (text or "").lower())


def _grams(text: str) -> set[str]:
    s = _norm(text)
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


TYPE_ALIASES = {
    "存款": ["存款", "定期", "活期", "大额存单", "存单", "整存整取"],
    "银行理财": ["理财", "现金管理", "固收"],
    "基金": ["基金", "定投"],
    "保险": ["保险", "年金", "增额"],
    "国债": ["国债"],
}


class KnowledgeBase:
    def __init__(self, data_dir: Path = DATA_DIR):
        self.docs: list[dict] = []
        self.by_id: dict[str, dict] = {}
        self.business: dict[str, dict] = {}
        self.params: dict = {"rows": [], "updated_at": ""}
        self._load(data_dir)

    def _load(self, data_dir: Path) -> None:
        for name in KB_FILES:
            path = data_dir / name
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            for doc in payload.get("docs", []):
                self.docs.append(doc)
                self.by_id[doc["id"]] = doc
                if doc.get("business_key"):
                    self.business[doc["business_key"]] = doc

        params_path = data_dir / "product_params.json"
        if params_path.exists():
            self.params = json.loads(params_path.read_text(encoding="utf-8"))

    # ---------- 检索 ----------
    def search(
        self,
        query: str,
        identity: str = "customer",
        top_k: int = 3,
        types: list[str] | None = None,
        min_score: float = 4.0,
    ) -> list[dict]:
        q = _norm(query)
        qgrams = _grams(query)
        results = []

        for doc in self.docs:
            if identity != "teller" and doc.get("visibility") != "public":
                continue
            if types and doc.get("type") not in types:
                continue

            kw_hits = [kw for kw in doc.get("keywords", []) if _norm(kw) and _norm(kw) in q]
            title_hits = len(qgrams & _grams(doc.get("title", "")))
            content_hits = len(qgrams & _grams(doc.get("content", "")))

            score = 4.0 * len(kw_hits) + min(6, title_hits) * 1.2 + min(8, content_hits) * 0.6
            status = doc.get("status", "")
            if status == "已废止":
                score *= 0.25
            elif status == "即将生效":
                score *= 0.85

            if score < min_score:
                continue

            results.append(
                {
                    "doc": doc,
                    "score": round(score, 2),
                    "matched": kw_hits,
                    "snippet": self._snippet(doc, kw_hits, qgrams),
                }
            )

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    @staticmethod
    def _snippet(doc: dict, kw_hits: list[str], qgrams: set[str]) -> str:
        text = doc.get("content", "")
        sentences = [s for s in re.split(r"[。；\n]", text) if s.strip()]
        if not sentences:
            return ""
        picked, used = [], 0
        for sentence in sentences:
            sn = _norm(sentence)
            if any(k in sn for k in kw_hits) or len(qgrams & _grams(sentence)) >= 2:
                picked.append(sentence.strip())
                used += len(sentence)
                if used > 140 or len(picked) >= 2:
                    break
        if not picked:
            picked = [sentences[0].strip()]
        return "；".join(picked)[:180]

    # ---------- 结构化查询 ----------
    def find_params(self, query: str, limit: int = 5) -> list[dict]:
        q = _norm(query)
        if not q:
            return []
        rows, others = [], []
        for row in self.params.get("rows", []):
            aliases = TYPE_ALIASES.get(row.get("type", ""), [])
            if any(alias in q for alias in aliases):
                rows.append(row)
                continue
            hay = _norm(" ".join(str(v) for v in row.values()))
            if sum(1 for token in _grams(query) if token in hay) >= 3:
                others.append(row)
        return (rows + others)[:limit]

    def get(self, doc_id: str) -> dict | None:
        return self.by_id.get(doc_id)


def source_brief(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "authority": doc.get("authority"),
        "effective_date": doc.get("effective_date"),
        "updated_at": doc.get("updated_at"),
        "status": doc.get("status"),
        "owner": doc.get("owner"),
        "category": doc.get("category"),
        "visibility": doc.get("visibility"),
    }

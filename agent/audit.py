"""对话与工具调用留痕（追加式 JSONL，逐行写入，便于追溯与抽检）。"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

AUDIT_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.jsonl"

_LOCK = threading.Lock()
_SEQ = 0


def init() -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not AUDIT_PATH.exists():
        AUDIT_PATH.touch()


def log_turn(
    *,
    session_id: str,
    identity: str,
    employee_id: str | None,
    user_text: str,
    decision: dict | None,
    escalated: bool,
    blocked: bool,
    risk_keys: list[str],
    reply: str,
    tools: list[str],
) -> int:
    """写入一条留痕。返回记录 id；写盘失败时返回 0，不影响对话主流程。"""
    global _SEQ
    _SEQ += 1
    record = {
        "id": int(time.time() * 1000) % 10_000_000 + _SEQ,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "identity": identity,
        "employee_id": employee_id or "",
        "user_text": (user_text or "")[:400],
        "intent": (decision or {}).get("intent", ""),
        "label": (decision or {}).get("label", ""),
        "confidence": (decision or {}).get("confidence", 0),
        "route": (decision or {}).get("route", ""),
        "escalated": bool(escalated),
        "blocked": bool(blocked),
        "risk_keys": risk_keys,
        "reply_excerpt": (reply or "").replace("\n", " ")[:300],
        "tools": tools,
    }
    try:
        line = json.dumps(record, ensure_ascii=False)
        with _LOCK:
            with AUDIT_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record["id"]
    except Exception:
        return 0


def _read_all() -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    out = []
    try:
        with AUDIT_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return out
    return out


def recent(limit: int = 12) -> list[dict]:
    rows = _read_all()
    return list(reversed(rows))[:limit]


def stats() -> dict:
    rows = _read_all()
    by_intent: dict[str, int] = {}
    for row in rows:
        key = row.get("intent") or ""
        if key:
            by_intent[key] = by_intent.get(key, 0) + 1
    return {
        "total": len(rows),
        "escalated": sum(1 for r in rows if r.get("escalated")),
        "blocked": sum(1 for r in rows if r.get("blocked")),
        "by_intent": dict(sorted(by_intent.items(), key=lambda kv: kv[1], reverse=True)),
    }

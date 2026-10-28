"""合规护栏：输入敏感信息识别、输出禁用词/荐品拦截、免责声明补齐。"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SENSITIVE_WORDS = ["密码", "验证码", "动态口令", "CVV", "cvv", "安全码", "短信码", "卡号", "身份证号"]

# 出现在这些词之后，说明是在“禁止/不得使用”的语境里，属于合规表述而非违规输出
PROHIBIT_PREFIX = ["严禁", "禁止", "不得", "不能", "不可", "不要", "避免", "不承诺", "不应", "勿"]
QUOTE_LEFT = ["“", "「", "‘", "\""]
QUOTE_RIGHT = ["”", "」", "’", "\""]


class ComplianceGuard:
    def __init__(self, data_dir: Path = DATA_DIR):
        payload = json.loads((data_dir / "risk_templates.json").read_text(encoding="utf-8"))
        self.disclaimers: dict[str, str] = payload.get("disclaimers", {})
        self.banned_words: list[str] = payload.get("banned_words", [])
        self.recommendation_patterns = [re.compile(p) for p in payload.get("recommendation_patterns", [])]
        self.sensitive_patterns = [re.compile(p) for p in payload.get("sensitive_patterns", [])]
        self.scripts: dict[str, str] = payload.get("scripts", {})

    # ---------- 输入侧 ----------
    def scan_input(self, text: str) -> dict:
        found = [p.pattern for p in self.sensitive_patterns if p.search(text)]
        mentioned = [w for w in SENSITIVE_WORDS if w in text]
        warn = bool(found or mentioned)
        redacted = text
        for pattern in self.sensitive_patterns:
            redacted = pattern.sub("【已脱敏】", redacted)
        return {
            "warn": warn,
            "redacted": redacted,
            "warning_text": self.scripts.get("sensitive_warning", "") if warn else "",
            "reasons": found + mentioned,
        }

    # ---------- 输出侧 ----------
    def _is_violation(self, text: str, word: str) -> bool:
        """仅当禁用词出现在“承诺/建议”语境时才判违规，引用禁令本身不算。"""
        start = 0
        while True:
            idx = text.find(word, start)
            if idx < 0:
                return False
            prefix = text[max(0, idx - 10) : idx]
            suffix = text[idx + len(word) : idx + len(word) + 2]
            quoted = prefix[-1:] in QUOTE_LEFT and suffix[:1] in QUOTE_RIGHT
            prohibited = any(p in prefix for p in PROHIBIT_PREFIX)
            if not quoted and not prohibited:
                return True
            start = idx + len(word)

    def scan_output(self, text: str) -> dict:
        hits = [w for w in self.banned_words if self._is_violation(text, w)]
        rec_hits = [p.pattern for p in self.recommendation_patterns if p.search(text)]
        if hits or rec_hits:
            return {
                "blocked": True,
                "hits": hits + rec_hits,
                "text": self.scripts.get("blocked_word", "该表述涉及收益或审批承诺，我不能这样表述。"),
            }
        return {"blocked": False, "hits": [], "text": text}

    def ensure(self, text: str, keys: list[str]) -> tuple[str, list[str]]:
        """补齐免责声明与适当性提示，返回 (新文本, 追加的声明列表)。"""
        appended = []
        for key in keys:
            line = self.disclaimers.get(key)
            if line and line not in text:
                text = f"{text}\n\n{line}"
                appended.append(line)
        return text, appended

    @staticmethod
    def has_risk_disclosure(text: str) -> bool:
        return any(w in text for w in ["风险", "不保本", "不构成投资建议", "谨慎", "以合同为准"])

    def decorate_answer(self, text: str, risk_keys: list[str]) -> dict:
        """统一出口：先扫禁用词，再补免责声明。"""
        scan = self.scan_output(text)
        if scan["blocked"]:
            text = scan["text"]
        text, appended = self.ensure(text, risk_keys)
        return {
            "text": text,
            "blocked": scan["blocked"],
            "hits": scan["hits"],
            "appended": appended,
            "risk_ok": self.has_risk_disclosure(text),
        }


GUARD = ComplianceGuard()

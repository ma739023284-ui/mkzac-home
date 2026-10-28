"""业务工作流（取号 / 预约 / 进度查询 / 产品参数），全部为可替换的行内系统接口适配层。"""
from __future__ import annotations

import random
import string
import time
from datetime import datetime, timedelta

from .knowledge import KnowledgeBase

BUSINESS_HINTS: list[tuple[str, list[str]]] = [
    ("social_card", ["社保卡", "医保"]),
    ("update_phone", ["改手机号", "预留手机号", "变更手机", "换手机号"]),
    ("digital_bank", ["手机银行", "网银", "电子银行"]),
    ("prepay", ["提前还款", "提前还贷"]),
    ("mortgage", ["房贷", "住房贷款", "按揭", "买房"]),
    ("business_loan", ["经营贷", "营业执照", "小微企业", "个体户"]),
    ("fx", ["外币", "美元", "欧元", "日元", "兑换", "换汇", "现钞"]),
    ("wealth_first", ["双录", "面签", "风险测评", "首次购买"]),
    ("branch_service", ["卫生间", "轮椅", "无障碍", "母婴", "便民", "老花", "停车"]),
    ("debit_card", ["开户", "开卡", "借记卡", "工资卡", "银行卡", "新卡"]),
    ("mortgage", ["贷款"]),
    ("debit_card", ["账户"]),
]

PREFIX = {"account": "A", "loan": "B", "wealth": "C", "other": "D"}

MANAGER = {
    "account": ("大堂经理 / 账户业务柜员", "A 区 1–3 号窗口"),
    "loan": ("个贷经理", "B 区 4–5 号窗口"),
    "wealth": ("理财经理", "C 区 6–7 号窗口（理财专区）"),
    "other": ("综合柜员", "D 区 8 号窗口"),
}

SLOTS = ["今天 15:30", "明天 10:00", "明天 14:30", "后天 09:30"]


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Flows:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.counter = {"A": 1, "B": 2, "C": 0, "D": 8}
        # 演示种子数据：可在不接真实系统时走通“进度查询”
        self.tickets: dict[str, dict] = {
            "A001": {
                "business_key": "debit_card",
                "business": "借记卡开户",
                "phone_tail": "8888",
                "stage": "资料受理中",
                "wait": 3,
                "window": "A 区 2 号窗口",
                "eta": 8,
                "created_at": _now_iso(),
            },
            "B002": {
                "business_key": "mortgage",
                "business": "个人住房贷款",
                "phone_tail": "6666",
                "stage": "审批中（不对外披露审批结论）",
                "wait": 0,
                "window": "B 区 5 号窗口",
                "eta": 0,
                "created_at": _now_iso(),
            },
        }
        self.appointments: dict[str, dict] = {}

    # ---------- 业务识别 ----------
    def business_key(self, text: str, default_intent: str = "account") -> str:
        for key, words in BUSINESS_HINTS:
            if any(w in text for w in words):
                return key
        return {"account": "debit_card", "loan": "mortgage", "wealth": "wealth_first", "other": "branch_service"}.get(
            default_intent, "branch_service"
        )

    def business_doc(self, business_key: str) -> dict:
        return self.kb.business.get(business_key, {})

    # ---------- 取号 ----------
    def take_number(self, business_key: str, intent: str = "account") -> dict:
        doc = self.business_doc(business_key)
        prefix = PREFIX.get(intent, "D")
        self.counter[prefix] += 1
        ticket = f"{prefix}{self.counter[prefix]:03d}"
        wait = {"account": 6, "loan": 2, "wealth": 1, "other": 4}.get(intent, 4)
        record = {
            "business_key": business_key,
            "business": doc.get("title", "综合业务"),
            "phone_tail": None,
            "stage": "已取号，等待叫号",
            "wait": wait,
            "window": doc.get("window", "大堂服务台"),
            "eta": wait * 3,
            "created_at": _now_iso(),
        }
        self.tickets[ticket] = record
        return {
            "ok": True,
            "ticket": ticket,
            "business": record["business"],
            "wait": wait,
            "window": record["window"],
            "eta": record["eta"],
            "materials": doc.get("materials", []),
            "channel": doc.get("channel", ""),
            "notes": doc.get("notes", ""),
        }

    # ---------- 进度查询 ----------
    def progress(self, ticket: str, phone_tail: str | None, session_tickets: list[str]) -> dict:
        ticket = (ticket or "").strip().upper()
        record = self.tickets.get(ticket)
        if not record:
            return {"ok": False, "reason": "未查询到该取号凭证，请核对号码或到现场凭身份证件查询。"}

        own = ticket in (session_tickets or [])
        if not own:
            if not phone_tail:
                return {"ok": False, "need_verify": True, "reason": "为保护隐私，请同时提供预留手机号后四位。"}
            if phone_tail != record.get("phone_tail"):
                return {"ok": False, "reason": "身份核验未通过，暂时无法查询，请到现场凭身份证件办理。"}

        return {
            "ok": True,
            "ticket": ticket,
            "business": record["business"],
            "stage": record["stage"],
            "wait": record["wait"],
            "window": record["window"],
            "eta": record["eta"],
            "note": "排队数据为演示数据，实际以排队叫号/业务系统实时返回为准。",
        }

    # ---------- 预约 ----------
    def make_appointment(self, business_key: str, intent: str = "account", slot: str | None = None) -> dict:
        doc = self.business_doc(business_key)
        manager, window = MANAGER.get(intent, MANAGER["other"])
        slot = slot or SLOTS[0]
        code = "YY" + "".join(random.choices(string.digits, k=6))
        record = {
            "code": code,
            "business": doc.get("title", "综合业务"),
            "manager": manager,
            "window": window,
            "slot": slot,
            "materials": doc.get("materials", []),
            "created_at": _now_iso(),
        }
        self.appointments[code] = record
        return {"ok": True, **record}

    def available_slots(self) -> list[str]:
        return list(SLOTS)

    # ---------- 产品参数（结构化字段，禁止模型生成数字） ----------
    def params(self, query: str) -> list[dict]:
        return self.kb.find_params(query)


def complaint_ticket(text: str) -> dict:
    code = "TS" + time.strftime("%Y%m%d") + "".join(random.choices(string.digits, k=4))
    due = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    return {
        "code": code,
        "due": due,
        "limit": "一般投诉 3 个工作日内答复；复杂投诉最长 15 日，延期会主动告知",
        "note": "工单已登记并全程留痕，值班经理将按流程跟进。",
    }

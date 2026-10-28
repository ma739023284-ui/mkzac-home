"""网点智能接待与导览助手 · 服务端（Flask）"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from agent import audit
from agent.compliance import GUARD
from agent.flows import Flows, complaint_ticket
from agent.intent import CLARIFY_OPTIONS, detect
from agent.knowledge import KnowledgeBase, source_brief
from agent.llm import LLMClient
from agent.prompts import GROUNDING_SYSTEM

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
VERSION = "1.0.0"

app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="")
app.config["JSON_AS_ASCII"] = False

KB = KnowledgeBase()
FLOWS = Flows(KB)
LLM_CLIENT = LLMClient()
TELLERS = {t["employee_id"]: t for t in json.loads((DATA / "tellers.json").read_text(encoding="utf-8"))["tellers"]}
TOKEN_TTL = 8 * 3600

SESSIONS: dict[str, dict] = {}
TOKENS: dict[str, dict] = {}

audit.init()

TICKET_RE = re.compile(r"(?<![A-Za-z0-9])([A-Da-d]\d{3})(?![0-9])")

INTENT_BY_BUSINESS = {
    "debit_card": "account",
    "social_card": "account",
    "update_phone": "account",
    "digital_bank": "account",
    "mortgage": "loan",
    "business_loan": "loan",
    "prepay": "loan",
    "wealth_first": "wealth",
    "fx": "other",
    "branch_service": "other",
}

PRODUCT_DOCS = {
    "deposit": ("prod_deposit", ["general_wealth"]),
    "wealth": ("prod_wealth", ["wealth", "suitability"]),
    "fund": ("prod_fund", ["fund", "suitability"]),
    "insurance": ("prod_insurance", ["insurance", "suitability"]),
    "bond": ("prod_bond", ["bond"]),
    "compare": ("prod_compare", ["general_wealth", "suitability"]),
}

PRODUCT_WORDS = [
    ("fund", ["基金", "定投"]),
    ("insurance", ["保险", "年金", "增额", "分红"]),
    ("bond", ["国债"]),
    ("wealth", ["理财", "净值", "业绩比较基准", "现金管理", "固收"]),
    ("deposit", ["存款", "定期", "活期", "大额存单", "储蓄"]),
]

ADVICE_WORDS = [
    "买哪个", "买哪只", "买哪款", "推荐", "能涨", "会涨", "该不该买",
    "能不能买", "该买", "抄底", "赚多少", "收益多少", "保证收益", "稳赚", "一定涨",
]

LOAN_COMMIT_WORDS = [
    "额度多少", "能批", "批得下来", "利率多少", "几天放款", "多久放款",
    "能贷多少", "通过吗", "能不能批", "包批", "一定批", "能放款吗",
]


# ---------------------------------------------------------------- 基础工具
def _session(sid: str) -> dict:
    sess = SESSIONS.get(sid)
    if not sess:
        sess = {
            "id": sid,
            "tickets": [],
            "last_ticket": None,
            "await_progress": False,
            "unknown": 0,
            "created": time.time(),
        }
        SESSIONS[sid] = sess
    return sess


def _teller(token: str | None) -> dict | None:
    if not token:
        return None
    rec = TOKENS.get(token)
    if not rec:
        return None
    if rec["exp"] < time.time():
        TOKENS.pop(token, None)
        return None
    return rec


def card(kind="answer", title="", text="", bullets=None, sources=None, options=None, meta=None, warning=""):
    return {
        "type": kind,
        "title": title,
        "text": text or "",
        "bullets": bullets or [],
        "sources": sources or [],
        "options": options or [],
        "meta": meta or {},
        "warning": warning or "",
    }


def opt(label, send=None, command=None):
    item = {"label": label}
    if send:
        item["send"] = send
    if command:
        item["command"] = command
    return item


def _sources(docs, limit=3):
    seen, out = set(), []
    for doc in docs:
        if doc["id"] in seen:
            continue
        seen.add(doc["id"])
        out.append(source_brief(doc))
        if len(out) >= limit:
            break
    return out


def parse_progress(text: str):
    ticket = TICKET_RE.search(text)
    tail = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
    return (ticket.group(1).upper() if ticket else None, tail.group(1) if tail else None)


# ---------------------------------------------------------------- 客户侧话术
def clarify_card(decision) -> tuple:
    options = []
    for label in CLARIFY_OPTIONS:
        options.append(opt(label, send=label))
    options.append(opt("直接找人工", command={"name": "escalate", "args": {"reason": "客户需要人工协助"}}))
    text = "我还没完全听准您要办什么，怕给您指错窗口。可以直接点下面选项，也可以跟我说大白话。"
    return card("clarify", "需要确认一下", text, [], [], options), [], False, []


def escalate_card(reason: str, sess: dict | None = None, note: str = "") -> tuple:
    text = "这个问题我怕给您说得不准确，我直接帮您叫一位工作人员来，您当面问更稳妥。"
    if note:
        text = f"{note}\n\n{text}"
    bullets = [
        f"转办原因：{reason}",
        "已通知大堂经理 / 值班经理（演示环境，预计 1 分钟内响应）",
        "已保留本次对话上下文，人工到场可直接查看",
    ]
    options = [opt("继续在线咨询", send="我还想问别的")]
    return card("escalate", "已转人工", text, bullets, [], options), [], True, ["escalate"]


def emergency_card(text: str) -> tuple:
    bullets = ["请先不要向任何人透露密码、验证码，也不要按对方要求转账。"]
    if any(w in text for w in ["挂失", "丢失", "丢了", "找不到卡"]):
        bullets += [
            "卡片挂失：手机银行或客服热线可立即临时挂失，也可到 A 区 1–3 号窗口正式挂失，挂失即时生效。",
            "补卡需本人携带身份证原件办理，现场即可预约。",
        ]
    elif any(w in text for w in ["盗刷", "被转走", "盗用", "钱没了"]):
        bullets += [
            "疑似盗刷：先冻结账户、止付，再打印交易流水并报警，保留报案回执。",
            "争议交易会按差错处理流程登记，处理进度可凭凭证查询。",
        ]
    elif any(w in text for w in ["诈骗", "被骗", "骗了", "冒充"]):
        bullets += [
            "立即停止转账；拨 110 或反诈专线 96110 报案，保留聊天记录与转账凭证。",
            "我行不会要求您把钱转到所谓“安全账户”，也不会索要密码和验证码。",
        ]
    else:
        bullets += ["请说明是卡片遗失、疑似盗刷还是接到可疑电话，我按对应应急处置流程处理。"]

    bullets.append("当前按最高优先级处理，不再走普通业务分流。")
    options = [
        opt("呼叫值班经理到场", command={"name": "escalate", "args": {"reason": "资金安全应急"}}),
        opt("我需要先挂失", send="我的卡丢了要挂失"),
    ]
    return card("escalate", "资金安全应急 · 优先处理", GUARD.scripts.get("emergency", ""), bullets, [], options), [], True, ["escalate"]


def complaint_card(text: str, decision) -> tuple:
    emotion = decision.emotion or "轻度"
    lead = GUARD.scripts.get("complaint", "")
    if emotion == "重度":
        lead = "这件事比较紧急，我已按最高优先级呼叫值班经理，请您稍等。" + lead
    bullets = [
        f"情绪等级：{emotion}",
        "1) 先共情安抚，不打断、不辩解、不推诿",
        "2) 值班经理 / 大堂经理到场，告知预计响应时间",
        "3) 登记工单并留痕，一般投诉 3 个工作日内答复",
        "4) 如涉及资金或监管转办，按升级流程上报",
    ]
    options = [
        opt("登记投诉工单", command={"name": "complaint_ticket", "args": {"summary": text[:60]}}),
        opt("呼叫值班经理", command={"name": "escalate", "args": {"reason": "投诉升级"}}),
    ]
    sources = _sources([KB.get("complain_sop")]) if KB.get("complain_sop") else []
    return card("escalate", "投诉受理 · 已升级", lead, bullets, sources, options), [], True, ["complaint_sop"]


def progress_ask_card(sess: dict) -> tuple:
    sess["await_progress"] = True
    options = [opt("直接找人工", command={"name": "escalate", "args": {"reason": "客户不会查询进度"}})]
    bullets = [
        "取号凭证形如 A001（大厅小票或短信里可看到）",
        "为保护隐私，需要同时核验预留手机号后四位",
        "核验不通过时不会返回任何业务信息",
    ]
    return card("form", "业务进度查询", "请把取号凭证和预留手机号后四位告诉我，例如“A001 8888”。", bullets, [], options), [], False, []


def progress_result_card(sess: dict, ticket: str | None, tail: str | None) -> tuple:
    if not ticket:
        return progress_ask_card(sess)
    res = FLOWS.progress(ticket, tail, sess["tickets"])
    if res.get("need_verify"):
        sess["await_progress"] = True
        sess["last_ticket"] = ticket
        return card(
            "form",
            "需要补充核验",
            f"取号凭证 {ticket} 已找到，为保护隐私请再提供预留手机号后四位。",
            ["核验不通过不会返回业务信息"],
            [],
            [opt("转人工核实", command={"name": "escalate", "args": {"reason": "进度查询核验"}})],
        ), [], False, ["progress"]
    if not res.get("ok"):
        return card(
            "answer",
            "暂无法查询",
            res.get("reason", "查询未通过。"),
            ["如凭证有误，请到服务台核对", "隐私核验不通过时不返回任何业务数据"],
            [],
            [opt("转人工核实", command={"name": "escalate", "args": {"reason": "进度查询未通过"}})],
        ), [], False, ["progress"]

    bullets = [
        f"业务：{res['business']}",
        f"当前环节：{res['stage']}",
        f"前面等待：{res['wait']} 位，预计 {res['eta']} 分钟",
        f"办理位置：{res['window']}",
        res.get("note", ""),
    ]
    bullets = [b for b in bullets if b]
    return card(
        "answer",
        "进度查询结果",
        f"取号凭证 {res['ticket']} 的当前状态如下。",
        bullets,
        [],
        [opt("取新号", command={"name": "take_number", "args": {"business": "debit_card", "intent": "account"}})],
    ), [], False, ["progress"]


def _product_key(text: str) -> str | None:
    for key, words in PRODUCT_WORDS:
        if any(w in text for w in words):
            return key
    return None


def wealth_card(sess: dict, text: str) -> tuple:
    if any(w in text for w in ADVICE_WORDS):
        options = [
            opt("帮我约理财经理", command={"name": "appointment", "args": {"business": "wealth_first", "intent": "wealth"}}),
            opt("先讲讲几类产品的区别", send="存款和理财有什么区别"),
        ]
        bullets = [
            "我不会推荐具体产品，也不会判断涨跌或买卖时点",
            "可以把产品类型、风险等级、费用和流动性客观讲清楚",
            "最终由您结合风险测评结果自主决定",
        ]
        return card(
            "escalate",
            "关于具体产品选择",
            GUARD.scripts.get("refusal_invest_advice", ""),
            bullets,
            [],
            options,
        ), ["general_wealth", "suitability"], False, ["compliance_refusal"]

    if any(w in text for w in ["测评", "双录", "面签", "首次购买", "测评怎么做"]):
        doc = FLOWS.business_doc("wealth_first") or {}
        bullets = []
        if doc.get("materials"):
            bullets.append("需要带的材料：" + "、".join(doc["materials"]))
        for i, step in enumerate(doc.get("steps", []), 1):
            bullets.append(f"{i}）{step}")
        if doc.get("notes"):
            bullets.append(doc["notes"])
        options = [
            opt("预约理财经理", command={"name": "appointment", "args": {"business": "wealth_first", "intent": "wealth"}}),
        ]
        return card(
            "answer",
            "首次购买理财 / 基金 / 保险",
            "风险测评必须由您本人完成，我不能代填，也不会引导您选高风险等级。",
            bullets,
            _sources([doc]) if doc else [],
            options,
        ), ["suitability"], False, ["suitability_guide"]

    product = _product_key(text)
    if product is None:
        product = "compare"

    doc_id, risk_keys = PRODUCT_DOCS[product]
    doc = KB.get(doc_id) or {}
    sections = doc.get("sections", {})
    bullets = []
    for key, label in [
        ("what", "是什么"),
        ("principal", "保不保本"),
        ("return", "收益怎么算"),
        ("terms", "关键要素"),
        ("fees", "费用"),
        ("suitable", "适合谁"),
    ]:
        if sections.get(key):
            bullets.append(f"{label}：{sections[key]}")

    options = [
        opt("约理财经理（先做风险测评）", command={"name": "appointment", "args": {"business": "wealth_first", "intent": "wealth"}}),
        opt("首次购买要带什么", send="首次购买需要什么材料"),
        opt("了解其他类型", send="存款和理财有什么区别"),
    ]
    return card(
        "answer",
        doc.get("title", "产品说明"),
        "我按六个方面客观说明，不做推荐；具体买哪只、什么时候买卖，需要您结合风险测评自主决定。",
        bullets,
        _sources([doc]) if doc else [],
        options,
    ), risk_keys, False, ["product_explain"]


def loan_card(sess: dict, text: str) -> tuple:
    bkey = FLOWS.business_key(text, "loan")
    doc = FLOWS.business_doc(bkey) or {}
    commit = any(w in text for w in LOAN_COMMIT_WORDS)
    bullets = []
    if doc.get("materials"):
        bullets.append("需要带的材料：")
        bullets += [f"· {m}" for m in doc["materials"]]
    if doc.get("steps"):
        bullets.append("预检流程：" + " → ".join(doc["steps"]))
    bullets.append(f"受理窗口：{doc.get('window', 'B 区 4–5 号窗口')}")
    if doc.get("notes"):
        bullets.append(doc["notes"])

    text_out = (
        GUARD.scripts.get("refusal_loan_commit", "")
        if commit
        else "我先按材料清单帮您做资质预检，正式受理后进入审批流程。"
    )
    options = [
        opt("预约个贷经理", command={"name": "appointment", "args": {"business": bkey, "intent": "loan"}}),
        opt("查询贷款进度", send="查询业务进度"),
    ]
    return card(
        "answer", doc.get("title", "个人贷款"), text_out, bullets, _sources([doc]) if doc else [], options
    ), ["loan"], False, ["loan_precheck"]


def account_card(sess: dict, text: str) -> tuple:
    if any(w in text for w in ["余额", "查账", "流水", "明细"]):
        bullets = [
            "账户余额与流水请本人通过手机银行查询，或持身份证到 A 区 1–3 号窗口查询打印",
            "请不要在对话里提供卡号、密码或验证码，我不会接收也不保存这些信息",
            "如需要打印纸质流水，本人携带身份证原件即可现场办理",
        ]
        options = [
            opt("取号到账户窗口", command={"name": "take_number", "args": {"business": "debit_card", "intent": "account"}}),
            opt("预约到店时段", command={"name": "appointment", "args": {"business": "debit_card", "intent": "account"}}),
        ]
        return card(
            "answer", "账户余额与流水查询", "余额和明细属于个人隐私，我只能告诉您查询方式，不能代为查询。", bullets, [], options
        ), [], False, ["privacy_guide"]

    bkey = FLOWS.business_key(text, "account")
    doc = FLOWS.business_doc(bkey) or {}
    bullets = []
    if doc.get("materials"):
        bullets.append("需要带的材料：")
        bullets += [f"· {m}" for m in doc["materials"]]
    bullets.append(f"办理渠道：{doc.get('channel', '智能柜台或柜面')}")
    bullets.append(f"办理窗口：{doc.get('window', 'A 区 1–3 号窗口')}")
    if doc.get("steps"):
        bullets.append("流程：" + " → ".join(doc["steps"]))
    if doc.get("notes"):
        bullets.append(doc["notes"])

    options = [
        opt("立即取号", command={"name": "take_number", "args": {"business": bkey, "intent": "account"}}),
        opt("预约到店时段", command={"name": "appointment", "args": {"business": bkey, "intent": "account"}}),
        opt("查询办理进度", send="查询业务进度"),
    ]
    return card(
        "answer",
        doc.get("title", "开户 / 账户业务"),
        "我先帮您把材料预检一遍，免得白跑一趟。",
        bullets,
        _sources([doc]) if doc else [],
        options,
    ), [], False, ["materials_precheck"]


def service_card(sess: dict, text: str) -> tuple:
    if any(w in text for w in ["外币", "美元", "欧元", "日元", "兑换", "换汇", "现钞"]):
        doc = FLOWS.business_doc("fx") or {}
        bullets = [f"· {m}" for m in doc.get("materials", [])]
        bullets.append("个人年度购汇便利化额度为等值 5 万美元，超额度需提供用途证明。")
        bullets.append("汇率以网点实时牌价为准，现钞以库存为准。")
        options = [
            opt("预约外币现钞", command={"name": "appointment", "args": {"business": "fx", "intent": "other"}}),
            opt("立即取号", command={"name": "take_number", "args": {"business": "fx", "intent": "other"}}),
        ]
        return card(
            "answer", doc.get("title", "外币业务"), "外币现钞建议提前预约，避免到店没有库存。", bullets, _sources([doc]) if doc else [], options
        ), [], False, ["fx_guide"]

    results = KB.search(text, identity="customer", top_k=2, types=["branch"], min_score=2.0)
    docs = [r["doc"] for r in results]
    bullets = [r["snippet"] for r in results if r["snippet"]]
    if any(w in text for w in ["卫生间", "洗手间", "轮椅", "无障碍", "母婴", "老花", "饮水", "停车"]):
        bdoc = FLOWS.business_doc("branch_service") or {}
        if bdoc.get("content"):
            bullets.append(bdoc["content"])
            docs.append(bdoc)
    bullets = [b for b in bullets if b][:4]
    options = [
        opt("大堂引导取号", command={"name": "take_number", "args": {"business": "branch_service", "intent": "other"}}),
        opt("预约到店", command={"name": "appointment", "args": {"business": "branch_service", "intent": "other"}}),
    ]
    return card(
        "answer", "网点信息与便民服务", "我按网点信息帮您查了一下：", bullets, _sources(docs), options
    ), [], False, ["branch_info"]


def customer_answer(sess: dict, text: str) -> tuple:
    if sess.get("await_progress"):
        ticket, tail = parse_progress(text)
        if ticket or tail:
            sess["await_progress"] = False
            res = progress_result_card(sess, ticket or sess.get("last_ticket"), tail)
            return (*res, {"intent": "progress", "label": "业务进度查询", "route": "p_progress", "confidence": 0.95, "escalate": res[2]})

    ticket, tail = parse_progress(text)
    if ticket and (tail or "查询" in text):
        res = progress_result_card(sess, ticket, tail)
        return (*res, {"intent": "progress", "label": "业务进度查询", "route": "p_progress", "confidence": 0.95, "escalate": res[2]})

    decision = detect(text)
    dec = decision.to_dict()

    if decision.unknown:
        if LLM_CLIENT.enabled:
            hits = KB.search(text, identity="customer", top_k=3, min_score=2.0)
            ctx = "\n".join(f"【{r['doc']['title']}】{r['doc']['content']}" for r in hits)
            answer = LLM_CLIENT.complete(GROUNDING_SYSTEM, f"【检索资料】\n{ctx}\n\n【客户问题】{text}")
            if answer:
                res = card("answer", "在线解答", answer, [], _sources([r["doc"] for r in hits]))
                return (*res, {"intent": "general", "label": "一般咨询", "route": "llm", "confidence": 0.6, "escalate": False})

        sess["unknown"] = sess.get("unknown", 0) + 1
        if sess["unknown"] >= 2:
            sess["unknown"] = 0
            res = escalate_card("连续两轮未能识别客户意图", note="我们换个方式：")
            dec["escalate"] = True
            return (*res, dec)
        return (*clarify_card(decision), dec)

    sess["unknown"] = 0

    if decision.intent == "emergency":
        res = emergency_card(text)
    elif decision.intent == "complaint":
        res = complaint_card(text, decision)
    elif decision.intent == "human":
        res = escalate_card("客户明确要求人工")
    elif decision.intent == "progress":
        if sess.get("last_ticket"):
            res = progress_result_card(sess, sess["last_ticket"], None)
        else:
            res = progress_ask_card(sess)
    elif decision.intent == "wealth":
        res = wealth_card(sess, text)
    elif decision.intent == "loan":
        res = loan_card(sess, text)
    elif decision.intent == "account":
        res = account_card(sess, text)
    else:
        res = service_card(sess, text)

    return (*res, dec)


def audit_card(limit: int = 8) -> dict:
    rows = audit.recent(limit)
    stats = audit.stats()
    bullets = [
        f"{r['ts']}｜{r['identity']}｜{r['intent'] or '-'}｜{'升级' if r['escalated'] else '常规'}"
        f"｜{'已拦截 ' if r['blocked'] else ''}{(r['user_text'] or '')[:26]}"
        for r in rows
    ]
    text = f"共 {stats['total']} 条留痕，升级 {stats['escalated']} 条，输出拦截 {stats['blocked']} 条。"
    return card("answer", "对话审计（最近）", text, bullets, [], [])


def teller_answer(text: str) -> tuple:
    decision = {"intent": "teller", "label": "柜员模式", "route": "s_teller", "confidence": 0.99, "escalate": False}
    options = [opt("查看最近审计留痕", command={"name": "audit_recent", "args": {}})]

    if any(w in text for w in ["审计", "留痕", "日志"]):
        return audit_card(), [], False, ["audit"], decision

    results = KB.search(text, identity="teller", top_k=3, min_score=3.2)
    need_params = any(w in text for w in ["参数", "利率", "费率", "期限", "起购", "风险等级", "额度", "在售", "产品"])
    params = FLOWS.params(text) if need_params else []

    bullets, docs, warning = [], [], ""
    for r in results[:2]:
        if r["snippet"]:
            bullets.append(f"要点：{r['snippet']}")
    for r in results:
        doc = r["doc"]
        docs.append(doc)
        bullets.append(
            f"{doc['title']}｜{doc['authority']}｜生效 {doc['effective_date']}｜更新 {doc['updated_at']}｜{doc['status']}"
        )
    for row in params[:4]:
        bullets.append(
            f"{row['name']}（{row['code']}）｜{row['term']}｜{row['min_amount']}｜{row['rate']}｜{row['risk_level']}｜{row['status']}"
        )

    if results and results[0]["doc"]["status"] == "已废止":
        warning = "命中了已废止版本，请勿对外引用，应改用现行文件。"
    elif results and results[0]["doc"]["status"] == "即将生效":
        warning = f"该文件自 {results[0]['doc']['effective_date']} 起生效，过渡期仍以现行文件为准。"

    if not bullets:
        return card(
            "answer",
            "知识库暂无依据",
            GUARD.scripts.get("no_kb_basis", ""),
            ["咨询渠道：个人金融部 / 财富管理部 / 运营管理部 / 合规部", "已记录该问题，供知识库补充"],
            [],
            options,
        ), ["policy_internal"], False, ["kb_search"], decision

    text_out = f"已检索到 {len(results)} 条相关口径" + ("，并附产品参数：" if params else "：")
    if len(results[0]["matched"]) < 2 and results[0]["score"] < 12 and not params:
        text_out = "未检索到直接依据，以下为最相近的口径，请核对是否适用："
        warning = (warning + " " if warning else "") + "相关性较弱，建议先核对文号与适用范围。"
    return card(
        "answer", "柜员检索结果", text_out, bullets, _sources(docs, 4), options, warning=warning
    ), ["policy_internal"], False, ["kb_search", "params"], decision


def handle_command(sess: dict, command: dict, teller: dict | None) -> tuple:
    name = str(command.get("name", ""))
    args = command.get("args") or {}

    if name == "take_number":
        bkey = str(args.get("business") or "debit_card")
        intent = str(args.get("intent") or INTENT_BY_BUSINESS.get(bkey, "account"))
        res = FLOWS.take_number(bkey, intent)
        sess["tickets"].append(res["ticket"])
        sess["last_ticket"] = res["ticket"]
        bullets = [
            f"取号：{res['ticket']}（{res['business']}）",
            f"前面等待 {res['wait']} 位，预计 {res['eta']} 分钟",
            f"办理位置：{res['window']}",
        ]
        if res["materials"]:
            bullets.append("请带好：" + "、".join(res["materials"]))
        options = [
            opt("查询我的排队进度", command={"name": "progress", "args": {"ticket": res["ticket"]}}),
            opt("改约其他时段", command={"name": "appointment", "args": {"business": bkey, "intent": intent}}),
        ]
        dec = {"intent": "workflow:take_number", "label": "取号", "route": "take_number", "confidence": 1.0, "escalate": False}
        return card("receipt", "取号回执", "取号成功，请留意大厅叫号屏，过号需重新取号。", bullets, [], options), [], False, ["take_number"], dec

    if name == "appointment":
        bkey = str(args.get("business") or "debit_card")
        intent = str(args.get("intent") or INTENT_BY_BUSINESS.get(bkey, "account"))
        res = FLOWS.make_appointment(bkey, intent, args.get("slot"))
        bullets = [
            f"预约编号：{res['code']}",
            f"时间：{res['slot']}",
            f"对接人：{res['manager']}（{res['window']}）",
        ]
        if res["materials"]:
            bullets.append("请带好：" + "、".join(res["materials"]))
        options = [
            opt("现在就取号", command={"name": "take_number", "args": {"business": bkey, "intent": intent}}),
            opt("改约其他时段", command={"name": "appointment", "args": {"business": bkey, "intent": intent, "slot": FLOWS.available_slots()[-1]}}),
        ]
        risk_keys = ["loan"] if intent == "loan" else (["suitability"] if intent == "wealth" else [])
        dec = {"intent": "workflow:appointment", "label": "预约", "route": "appointment", "confidence": 1.0, "escalate": False}
        return card("receipt", "预约回执", "预约已提交，到店请到服务台报预约编号。", bullets, [], options), risk_keys, False, ["appointment"], dec

    if name == "complaint_ticket":
        info = complaint_ticket(str(args.get("summary") or "客户现场反映问题"))
        bullets = [
            f"工单号：{info['code']}",
            f"答复时限：{info['limit']}",
            f"预计首次答复：{info['due']} 前",
            info["note"],
        ]
        options = [opt("呼叫值班经理", command={"name": "escalate", "args": {"reason": "投诉工单跟进"}})]
        dec = {"intent": "workflow:complaint_ticket", "label": "投诉工单", "route": "complaint_ticket", "confidence": 1.0, "escalate": True}
        return card("receipt", "投诉工单已登记", "已为您登记投诉工单，值班经理会按流程跟进，您无需重复反映。", bullets, [], options), [], True, ["complaint_ticket"], dec

    if name == "escalate":
        reason = str(args.get("reason") or "客户要求人工")
        bullets = [
            f"转办原因：{reason}",
            "已通知大堂经理 / 值班经理（演示环境）",
            "预计响应：1 分钟内到场",
            "对话上下文已保留，人工可直接查看",
        ]
        dec = {"intent": "workflow:escalate", "label": "转人工", "route": "escalate", "confidence": 1.0, "escalate": True}
        return card("escalate", "已通知人工", "工作人员马上就到，请您在大厅稍等；如有急事可先到值班经理台。", bullets, [], []), [], True, ["escalate"], dec

    if name == "progress":
        ticket = args.get("ticket")
        res = progress_result_card(sess, ticket, args.get("phone_tail"))
        dec = {"intent": "workflow:progress", "label": "进度查询", "route": "progress", "confidence": 1.0, "escalate": res[2]}
        return (*res, dec)

    if name == "audit_recent":
        dec = {"intent": "workflow:audit", "label": "审计留痕", "route": "audit_recent", "confidence": 1.0, "escalate": False}
        if not teller:
            return card("answer", "需要柜员鉴权", "审计留痕仅对通过工号鉴权的柜员开放。", [], []), [], False, [], dec
        return audit_card(), [], False, ["audit"], dec

    dec = {"intent": "workflow:unknown", "label": "未知动作", "route": name, "confidence": 0.2, "escalate": False}
    return card("answer", "操作未识别", "这个操作我暂时不支持，请直接告诉我您要办什么。", [], []), [], False, [], dec


# ---------------------------------------------------------------- API
@app.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@app.get("/")
def root():
    return send_from_directory(str(BASE / "static"), "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "version": VERSION, "kb_docs": len(KB.docs)})


@app.get("/api/meta")
def meta():
    demo = str(os.environ.get("DEMO_MODE", "1")) != "0"
    return jsonify(
        {
            "ok": True,
            "version": VERSION,
            "branch": {
                "name": "XX 银行 XX 支行营业部（示例）",
                "hours": "周一至周五 9:00–17:00｜周六 9:30–16:00",
                "address": "XX 市 XX 区 XX 路 88 号",
                "phone": "0000-00000000",
            },
            "kb_docs": len(KB.docs),
            "llm_enabled": LLM_CLIENT.enabled,
            "demo_mode": demo,
            "demo": {"teller_id": "T1001", "otp": "888888", "ticket": "A001", "tail": "8888"} if demo else {},
        }
    )


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    employee_id = str(payload.get("employee_id") or "").strip().upper()
    otp = str(payload.get("otp") or "").strip()
    record = TELLERS.get(employee_id)
    if not record:
        return jsonify({"ok": False, "error": "工号或动态口令不正确"}), 401
    expected = hashlib.sha256(f"{record['employee_id']}:{otp}".encode()).hexdigest()
    if not secrets.compare_digest(expected, record["otp_sha256"]):
        return jsonify({"ok": False, "error": "工号或动态口令不正确"}), 401
    token = secrets.token_urlsafe(24)
    TOKENS[token] = {
        "employee_id": record["employee_id"],
        "name": record["name"],
        "branch": record["branch"],
        "role": record["role"],
        "exp": time.time() + TOKEN_TTL,
    }
    return jsonify(
        {
            "ok": True,
            "token": token,
            "name": record["name"],
            "employee_id": record["employee_id"],
            "branch": record["branch"],
            "role": record["role"],
            "expires_in": TOKEN_TTL,
        }
    )


@app.post("/api/logout")
def logout():
    payload = request.get_json(silent=True) or {}
    TOKENS.pop(str(payload.get("token") or ""), None)
    return jsonify({"ok": True})


@app.post("/api/chat")
def chat():
    payload = request.get_json(silent=True) or {}
    sid = str(payload.get("session_id") or "").strip()[:64] or secrets.token_hex(8)
    text = str(payload.get("text") or "").strip()[:1200]
    command = payload.get("command") if isinstance(payload.get("command"), dict) else None
    teller = _teller(payload.get("token"))
    sess = _session(sid)
    started = time.perf_counter()

    scan = GUARD.scan_input(text)

    if command:
        result, risk_keys, escalated, tools, decision = handle_command(sess, command, teller)
        if not text:
            text = f"[{command.get('name')}]"
    elif teller:
        result, risk_keys, escalated, tools, decision = teller_answer(text)
    else:
        result, risk_keys, escalated, tools, decision = customer_answer(sess, text)

    decorated = GUARD.decorate_answer(result["text"], risk_keys)
    result["text"] = decorated["text"]

    result["meta"].update(
        {
            "intent": decision.get("intent", ""),
            "label": decision.get("label", ""),
            "confidence": decision.get("confidence", 0),
            "route": decision.get("route", ""),
            "escalated": escalated,
            "emotion": decision.get("emotion", ""),
            "blocked": decorated["blocked"],
            "disclaimer_added": decorated["appended"],
            "mode": "teller" if teller else "customer",
            "employee_id": teller["employee_id"] if teller else "",
            "session_id": sid,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "llm": LLM_CLIENT.enabled,
            "version": VERSION,
        }
    )
    if scan["warn"]:
        result["warning"] = scan["warning_text"]

    audit_id = audit.log_turn(
        session_id=sid,
        identity="teller" if teller else "customer",
        employee_id=teller["employee_id"] if teller else None,
        user_text=scan["redacted"],
        decision=decision,
        escalated=escalated,
        blocked=decorated["blocked"],
        risk_keys=risk_keys,
        reply=result["text"],
        tools=tools,
    )
    result["meta"]["audit_id"] = audit_id
    return jsonify(result)


@app.get("/api/audit/recent")
def audit_recent_api():
    teller = _teller(request.args.get("token"))
    if not teller:
        return jsonify({"ok": False, "error": "需要柜员鉴权"}), 403
    try:
        limit = min(50, max(1, int(request.args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    return jsonify({"ok": True, "stats": audit.stats(), "rows": audit.recent(limit)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)

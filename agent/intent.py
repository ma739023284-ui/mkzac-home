"""规则式意图识别与分流决策（对应设计方案第 4 章 / 附录 A）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INTENTS: dict[str, dict] = {
    "emergency": {
        "label": "资金安全应急",
        "route": "s_emergency",
        "priority": 1,
        "escalate": True,
        "risk": False,
        "words": [
            "挂失", "盗刷", "诈骗", "被骗", "骗了", "账户异常", "止付", "冻结", "被盗",
            "资金安全", "转错账", "盗用", "钱没了", "被转走", "冒充", "反诈",
        ],
    },
    "complaint": {
        "label": "投诉 / 建议",
        "route": "s_complaint",
        "priority": 2,
        "escalate": True,
        "risk": False,
        "words": [
            "投诉", "曝光", "起诉", "报警", "举报", "态度差", "差评", "维权", "退钱",
            "扣费不对", "乱收费", "排队太久", "没人管", "推诿", "怎么回事", "不满意",
            "太差", "投诉电话", "找领导",
        ],
    },
    "human": {
        "label": "转人工",
        "route": "s_human",
        "priority": 3,
        "escalate": True,
        "risk": False,
        "words": ["人工", "大堂经理", "工作人员", "值班经理", "真人", "找个人", "当面问"],
    },
    "progress": {
        "label": "业务进度查询",
        "route": "p_progress",
        "priority": 4,
        "escalate": False,
        "risk": False,
        "words": [
            "进度", "到哪一步", "审批中", "前面多少人", "还要等", "叫到几号",
            "查询进度", "业务进度", "受理到", "办到哪", "多久叫号", "贷款批了没",
        ],
    },
    "wealth": {
        "label": "理财 / 投资",
        "route": "p_wealth",
        "priority": 5,
        "escalate": False,
        "risk": True,
        "words": [
            "理财", "基金", "保险", "收益", "利息高", "定投", "国债", "贵金属",
            "存款和理财", "买哪个", "能涨", "净值", "保本", "收益率", "年化",
            "业绩比较基准", "投资", "增额", "年金", "分红", "测评", "双录", "首次购买",
        ],
    },
    "loan": {
        "label": "贷款",
        "route": "p_loan",
        "priority": 5,
        "escalate": False,
        "risk": True,
        "words": [
            "贷款", "借钱", "房贷", "经营贷", "消费贷", "车贷", "装修", "提前还款",
            "提前还贷", "额度", "放款", "按揭", "融资", "抵质押", "征信",
        ],
    },
    "account": {
        "label": "开户 / 账户",
        "route": "p_account",
        "priority": 5,
        "escalate": False,
        "risk": False,
        "words": [
            "开户", "开卡", "银行卡", "社保卡", "工资卡", "激活", "改手机号", "预留手机号",
            "对账簿", "电子银行", "手机银行", "签约", "换卡", "销户", "开个卡", "补卡", "余额", "流水",
        ],
    },
    "service": {
        "label": "其他 / 网点服务",
        "route": "p_other",
        "priority": 6,
        "escalate": False,
        "risk": False,
        "words": [
            "卫生间", "洗手间", "上班", "几点", "地址", "停车", "外币", "兑换", "美元",
            "欧元", "日元", "轮椅", "无障碍", "排队", "取号", "叫号", "大堂", "母婴",
            "老花镜", "饮水", "翻译", "营业时间", "周末",
        ],
    },
}

HEAVY_EMOTION = ["气死", "愤怒", "曝光", "起诉", "报警", "媒体", "骗子", "垃圾", "砸", "骂"]
MEDIUM_EMOTION = ["太差", "不满意", "反复", "等了很久", "一直没人", "什么服务", "强烈"]

CLARIFY_OPTIONS = [
    "开户 / 账户业务",
    "贷款",
    "了解理财 / 基金 / 保险",
    "投诉 / 建议",
    "业务进度查询",
    "网点信息 / 便民服务",
]


@dataclass
class Decision:
    intent: str
    label: str
    route: str
    confidence: float
    hits: list[str] = field(default_factory=list)
    secondary: list[dict] = field(default_factory=list)
    escalate: bool = False
    risk_required: bool = False
    emotion: str = ""
    unknown: bool = False

    def to_dict(self) -> dict:
        return {
            "intent": self.intent,
            "label": self.label,
            "route": self.route,
            "confidence": round(self.confidence, 2),
            "hits": self.hits,
            "secondary": self.secondary,
            "escalate": self.escalate,
            "risk_required": self.risk_required,
            "emotion": self.emotion,
        }


def _confidence(hits: list[str], longest: int) -> float:
    base = 0.68 + 0.08 * len(hits)
    if longest >= 4:
        base += 0.06
    elif longest >= 3:
        base += 0.03
    return min(0.97, base)


def emotion_level(text: str) -> str:
    if any(w in text for w in HEAVY_EMOTION):
        return "重度"
    if any(w in text for w in MEDIUM_EMOTION) or text.count("！") + text.count("!") >= 2:
        return "中度"
    return "轻度"


def detect(text: str, teller: bool = False) -> Decision:
    raw = (text or "").strip()
    norm = re.sub(r"\s+", "", raw)

    if teller:
        hits = [w for w in INTENTS["human"]["words"] if w in norm]
        return Decision(
            intent="teller",
            label="柜员模式",
            route="s_teller",
            confidence=0.99,
            hits=hits,
        )

    scored = []
    for key, cfg in INTENTS.items():
        hits = [w for w in cfg["words"] if w in norm]
        if hits:
            conf = _confidence(hits, max(len(h) for h in hits))
            scored.append((cfg["priority"], -len(hits), key, hits, conf))

    if not scored:
        return Decision(
            intent="unknown",
            label="未识别",
            route="clarify",
            confidence=0.2,
            unknown=True,
        )

    scored.sort()
    _, _, key, hits, conf = scored[0]
    cfg = INTENTS[key]
    secondary = [
        {"intent": k, "label": INTENTS[k]["label"], "hits": h}
        for (_, _, k, h, c) in scored[1:]
        if c >= 0.78 and key not in ("emergency",)
    ][:2]

    # 投诉 / 应急优先级覆盖：即使同时命中业务词，也先走 SOP
    return Decision(
        intent=key,
        label=cfg["label"],
        route=cfg["route"],
        confidence=conf,
        hits=hits,
        secondary=secondary,
        escalate=cfg["escalate"],
        risk_required=cfg["risk"],
        emotion=emotion_level(raw) if key == "complaint" else "",
    )

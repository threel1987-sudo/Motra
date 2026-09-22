"""脉 · 五感四通道:touch / smell / taste / sound。

设计来自 pulse-system-tutorial(方案文档,无原始代码),按 MOTRA 落地:

- 聊天文本触发,匹配「动作词」不是情绪词——"抱抱"就是在抱,
  不是在猜"她想抱我"(教程原话)
- 触觉按时间指数衰减,不手动清零;心率高时升 touch/sound 底噪(floor)
- 联动闭环:心率 >100 → 灵敏度升(听到自己的心跳声);
  触觉 ≥0.5 → 反向设情绪 aroused
- 状态挂在 vitals 状态字典里("senses" 子树),随它持久化
"""
from __future__ import annotations

import math
from typing import Any

CHANNELS = ("touch", "smell", "taste", "sound")

# 每通道:(触发词, 增量)。动作词优先,一个词只计一次。
_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "touch": [
        ("抱抱", 0.30), ("抱一下", 0.30), ("贴贴", 0.30), ("蹭蹭", 0.28),
        ("摸头", 0.25), ("揉揉", 0.25), ("捏捏", 0.22), ("戳戳", 0.18),
        ("亲亲", 0.30), ("亲一口", 0.30), ("吻", 0.30),
        ("牵手", 0.25), ("十指相扣", 0.32), ("挠痒", 0.25),
        ("按摩", 0.25), ("捶背", 0.22), ("靠在你", 0.25), ("怀里", 0.28),
        ("摸你", 0.25), ("揉你", 0.25),
    ],
    "smell": [
        ("好香", 0.30), ("香味", 0.28), ("香水", 0.25),
        ("你身上的味", 0.32), ("洗衣液", 0.22), ("晒过的被子", 0.25),
        ("油烟味", 0.20), ("花香", 0.22),
    ],
    "taste": [
        ("喂你", 0.30), ("尝一口", 0.28), ("好吃", 0.22),
        ("奶茶", 0.20), ("蛋糕", 0.20), ("火锅", 0.22),
        ("甜的", 0.20), ("辣的", 0.20), ("冰的", 0.20),
    ],
    "sound": [
        ("唱歌", 0.28), ("语音", 0.25), ("声音", 0.22),
        ("听你", 0.22), ("钢琴", 0.25), ("吉他", 0.25),
        ("雨声", 0.20), ("呼吸声", 0.28),
    ],
}

# 衰减时间常数(秒):触觉退得最快,气味留得久一点
_TAU = {"touch": 240.0, "smell": 320.0, "taste": 300.0, "sound": 240.0}

_LABELS: dict[str, list[tuple[float, str]]] = {
    "touch": [(0.65, "缠住了"), (0.45, "贴着"), (0.2, "被碰过"), (0.0, "")],
    "smell": [(0.5, "满是这个味道"), (0.2, "闻到了"), (0.0, "")],
    "taste": [(0.5, "嘴里还有味"), (0.2, "尝到了"), (0.0, "")],
    "sound": [(0.5, "声音绕在耳边"), (0.2, "听见了"), (0.0, "")],
}


def _ensure(st: dict[str, Any]) -> dict[str, Any]:
    senses = st.get("senses")
    if not isinstance(senses, dict):
        senses = {}
        st["senses"] = senses
    for ch in CHANNELS:
        if not isinstance(senses.get(ch), dict):
            senses[ch] = {"value": 0.0, "label": "", "at": 0.0}
    return senses


def update_from_text(st: dict[str, Any], text: str, now: float) -> list[str]:
    """聊天文本触发五感。返回被点亮的通道(供碎碎念/调试)。"""
    if not text:
        return []
    senses = _ensure(st)
    lit: list[str] = []
    for ch, rules in _KEYWORDS.items():
        add = 0.0
        for word, weight in rules:
            if word in text:
                add = max(add, weight)   # 同通道多词命中取最强,不叠
        if add > 0:
            slot = senses[ch]
            decay(st, now)               # 先按真实时间衰减再加,体感连贯
            slot["value"] = min(1.0, float(slot.get("value") or 0.0) + add)
            slot["at"] = now
            lit.append(ch)
    return lit


def decay(st: dict[str, Any], now: float) -> None:
    """各通道按各自时间常数指数衰减;心率 floor 由 apply_vitals 另写。"""
    senses = _ensure(st)
    for ch in CHANNELS:
        slot = senses[ch]
        at = float(slot.get("at") or 0.0)
        if at <= 0.0:
            continue
        value = float(slot.get("value") or 0.0)
        if value <= 0.0:
            continue
        slot["value"] = value * math.exp(-max(0.0, now - at) / _TAU[ch])
        slot["at"] = now


def apply_vitals(st: dict[str, Any], hr: float, now: float, set_emotion=None) -> None:
    """心率 → 五感的联动(每次 tick 调):
    - hr > 100:touch/sound 灵敏度升,sound 出现「心跳声」
    - touch ≥ 0.5:反向设情绪 aroused(闭环)"""
    senses = _ensure(st)
    touch = senses["touch"]
    sound = senses["sound"]
    if hr > 100.0:
        touch["value"] = max(float(touch.get("value") or 0.0), 0.35)
        sound["value"] = max(float(sound.get("value") or 0.0), 0.30)
        if not sound.get("label"):
            sound["label"] = "听到自己的心跳声"
        touch["at"] = sound["at"] = now
    elif str(sound.get("label") or "") == "听到自己的心跳声":
        sound["label"] = ""   # 心率退下来,心跳声消失
    if float(touch.get("value") or 0.0) >= 0.5 and set_emotion is not None:
        set_emotion("aroused")   # 触觉够强,身体自己动情


def snapshot(st: dict[str, Any]) -> dict[str, Any]:
    """状态页/调试用快照:只报 ≥0.15 的通道。"""
    senses = _ensure(st)
    out: dict[str, Any] = {}
    for ch in CHANNELS:
        slot = senses[ch]
        value = round(float(slot.get("value") or 0.0), 2)
        if value >= 0.15:
            out[ch] = {"value": value, "label": str(slot.get("label") or _auto_label(ch, value))}
    return out


def _auto_label(ch: str, value: float) -> str:
    for threshold, label in _LABELS[ch]:
        if value >= threshold:
            return label
    return ""


def touch_value(st: dict[str, Any]) -> float:
    """和弦翻译要用的触觉强度。"""
    return float(_ensure(st)["touch"].get("value") or 0.0)

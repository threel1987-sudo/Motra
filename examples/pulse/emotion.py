"""脉 · 情绪层:双轨输入、情绪底色、和弦染色。

设计来自 pulse-system-tutorial(方案文档,无原始代码),按 MOTRA 落地:

- 双轨输入
  · Drivesoid 主供(慢而准):16 维 drives 映射为脉的情绪标签
  · emoji 快速通道(快而短):她消息里的 emoji/叹词即时触发,45s 内不被
    Drivesoid 映射覆盖——惊吓/挨骂是当下的事,等不到小模型分类回来
- 情绪底色(residue):强情绪写入残留层,按各自半衰期指数消散
  (对齐生理:皮质醇 60-90min、肾上腺素 20min、催产素 45min)
- 被哄机制:负面底色未消时正面情绪进来,负面 ×4 加速消散,同时叠一层
  浅暖底色(强度六折——被哄好的暖比主动的暖浅);两条线同时跑
- 和弦染色:强情绪直接覆盖生理基础和弦;平静时由底色染色(≥0.15)

本模块是纯函数 + 状态字典操作,状态由 vitals 持有与持久化。
"""
from __future__ import annotations

import math
from typing import Any

# ── 情绪标签 → 生理偏移 ─────────────────────────────────────────────────
# 三档(教程口径):平静类 ±2、紧张类 +10~20、亲密类 +15~30
EMO_HR: dict[str, float] = {
    "neutral": 0.0,
    "focused": 2.0,
    "happy": 6.0,
    "sad": -4.0,
    "nervous": 12.0,
    "scolded": 16.0,
    "startled": 10.0,   # 另有一次性 spike,这里是持续段
    "excited": 14.0,
    "intimate": 18.0,
    "aroused": 28.0,
}

EMO_TEMP: dict[str, float] = {
    "sad": -0.10,
    "nervous": 0.10,
    "scolded": 0.20,
    "startled": 0.15,
    "excited": 0.20,
    "intimate": 0.30,
    "aroused": 0.40,
}

EMO_RESP: dict[str, float] = {
    "sad": -0.5,
    "intimate": 1.5,
    "nervous": 2.0,
    "excited": 2.5,
    "scolded": 2.5,
    "aroused": 3.0,
    "startled": 4.0,
}

# 强情绪直接覆盖基础和弦(教程:消息先定 emotion,生理值算基础 chord,强情绪覆盖)
CHORD_OVERRIDE: dict[str, str] = {
    "scolded": "Dm",      # 被骂,低沉
    "sad": "Am7",         # 安静的伤
    "nervous": "Am",
    "startled": "F#dim",  # 惊吓,不协和
    "excited": "Dmaj7",   # 明亮
    "intimate": "Fmaj7",  # 亲近
    "aroused": "Dm7",     # 暧昧张力
}

# ── 情绪底色:label → (初始强度, 半衰期秒) ──────────────────────────────
RESIDUE_TABLE: dict[str, tuple[float, float]] = {
    "scolded": (1.0, 90 * 60),   # 皮质醇代谢周期 60-90min
    "sad": (0.8, 60 * 60),
    "nervous": (0.6, 20 * 60),   # 肾上腺素代谢快
    "startled": (0.7, 15 * 60),  # 来得快去得快
    "intimate": (0.9, 45 * 60),  # 催产素半衰期
    "aroused": (0.9, 30 * 60),
    "excited": (0.7, 20 * 60),
}
POSITIVE = {"happy", "intimate", "aroused", "excited"}
NEGATIVE = {"scolded", "sad", "nervous", "startled"}
RESIDUE_ACTIVE = 0.15   # 低于此强度不再染色
RESIDUE_DROP = 0.05     # 低于此强度直接清除

# ── emoji 快速通道(T1):emoji/叹词 → (标签, 一次性 spike) ───────────────
# 只收「不需要上下文」的信号;语义识别是 Drivesoid 的活,这里不抢。
_EMOJI_RULES: list[tuple[tuple[str, ...], str, float]] = [
    (("😤", "💢", "😡", "👿", "哼"), "scolded", 6.0),
    (("😱", "😨", "😰", "❗", "‼️"), "startled", 18.0),
    (("😭", "😢", "💔", "🥺", "呜呜"), "sad", 0.0),
    (("😚", "😘", "🥰", "❤️", "💋", "🤗", "💕"), "intimate", 0.0),
    (("😏", "🔥", "🫦", "😳"), "aroused", 0.0),
    (("😄", "😆", "🤣", "😁", "嘻嘻", "哈哈哈"), "happy", 0.0),
]

# ── Drivesoid 16 维 → 脉情绪标签(按优先级从上到下,命中即停) ────────────
_DRIVES_MAP: list[tuple[str, float, str]] = [
    ("lust", 0.65, "aroused"),
    ("irritability", 0.60, "scolded"),   # 烦躁的生理签名≈被骂后的皮质醇
    ("dejection", 0.60, "sad"),
    ("anxiety", 0.60, "nervous"),
    ("fear", 0.60, "nervous"),
    ("elation", 0.65, "excited"),
    ("intimacy", 0.60, "intimate"),
    ("contentment", 0.60, "happy"),
    ("play", 0.60, "happy"),
]

EMOJI_WINDOW_SEC = 45.0     # emoji 定的情绪,这么长的时间内不被 drives 覆盖
EMOJI_FALLBACK_SEC = 300.0  # drives 缺席时,emoji 情绪最多挂这么久就回落 neutral


def scan_text(text: str) -> tuple[str, float] | None:
    """emoji 快速通道:命中返回 (标签, spike),否则 None。"""
    if not text:
        return None
    for tokens, label, spike in _EMOJI_RULES:
        for tok in tokens:
            if tok in text:
                return label, spike
    return None


def _find_dims(node: Any) -> dict[str, float] | None:
    """从 Drivesoid status 里抠出 16 维数值字典(形状防御:找含 lust 的 dict)。"""
    if isinstance(node, dict):
        vals = node.get("values") if isinstance(node.get("values"), dict) else node
        if isinstance(vals, dict) and "lust" in vals:
            out: dict[str, float] = {}
            for k, v in vals.items():
                try:
                    out[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            return out
        for v in node.values():
            found = _find_dims(v)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_dims(v)
            if found is not None:
                return found
    return None


def map_drives(status: Any) -> str | None:
    """Drivesoid status → 脉情绪标签;没有强信号返回 neutral,解析失败返回 None。"""
    dims = _find_dims(status)
    if dims is None:
        return None
    for dim, threshold, label in _DRIVES_MAP:
        if dims.get(dim, 0.0) >= threshold:
            return label
    return "neutral"


def drives_delta(status: Any) -> float:
    """教程 Δdrive = intimacy×8 - fatigue×5;MOTRA 有 16 维,按比例扩:
    亲密向(lust/intimacy/attachment 系的 seeking)推升,疲惫压下。"""
    dims = _find_dims(status)
    if dims is None:
        return 0.0
    warm = dims.get("lust", 0.0) * 0.6 + dims.get("intimacy", 0.0) * 0.4
    return warm * 12.0 - dims.get("fatigue", 0.0) * 6.0


# ── 状态操作(状态字典由 vitals 传入) ─────────────────────────────────────

def _residue_slot(strength0: float, now: float, accel: float = 1.0) -> dict[str, float]:
    return {"strength": strength0, "set_at": now, "accel": accel}


def _slot_strength(slot: dict[str, Any] | None, label: str, now: float) -> float:
    """底色当前强度:s0 × 0.5^(accel×elapsed/半衰期)。"""
    if not slot:
        return 0.0
    _, half = RESIDUE_TABLE.get(label, (0.5, 20 * 60))
    elapsed = max(0.0, now - float(slot.get("set_at") or now))
    accel = float(slot.get("accel") or 1.0)
    return float(slot.get("strength") or 0.0) * math.pow(0.5, accel * elapsed / half)


def _normalize_residue(st: dict[str, Any], now: float) -> dict[str, Any]:
    """把残量「烧回」强度值并重置计时(避免 set_at 无限漂移),顺手清理微弱槽。"""
    res = st.get("residue")
    if not isinstance(res, dict):
        res = {}
        st["residue"] = res
    for slot_name in ("neg", "pos"):
        slot = res.get(slot_name)
        if not isinstance(slot, dict):
            continue
        label = str(slot.get("label") or "")
        cur = _slot_strength({k: v for k, v in slot.items() if k != "label"}, label, now)
        if cur < RESIDUE_DROP:
            res.pop(slot_name, None)
        else:
            res[slot_name] = {**_residue_slot(cur, now, float(slot.get("accel") or 1.0)), "label": label}
    return res


def set_emotion(st: dict[str, Any], label: str, src: str, now: float, spike: float = 0.0) -> None:
    """写入当前情绪;强情绪写底色,被哄时负面底色 ×4 加速 + 叠浅暖。"""
    prev = str(st.get("emotion") or "neutral")
    st["emotion"] = label
    st["emotion_src"] = src
    st["emotion_at"] = now
    if spike > 0:
        st["spike_mag"] = max(float(st.get("spike_mag") or 0.0), spike)
        st["spike_at"] = now
    res = _normalize_residue(st, now)
    if label in NEGATIVE:
        # 强负面:写/覆盖负面底色(新的伤害盖过旧的)
        s0, _ = RESIDUE_TABLE[label]
        res["neg"] = {**_residue_slot(s0, now), "label": label}
    elif label in POSITIVE:
        neg = res.get("neg")
        if isinstance(neg, dict) and _slot_strength(neg, str(neg.get("label") or ""), now) >= RESIDUE_ACTIVE:
            # 被哄:负面 ×4 加速清除,叠一层六折浅暖,两条线同时跑
            neg["accel"] = 4.0
            neg["set_at"] = now
            neg["strength"] = _slot_strength(neg, str(neg.get("label") or ""), now)
            s0, _ = RESIDUE_TABLE.get("intimate", (0.9, 45 * 60))
            res["pos"] = {**_residue_slot(s0 * 0.6, now), "label": "intimate"}
        elif label in RESIDUE_TABLE:
            s0, _ = RESIDUE_TABLE[label]
            res["pos"] = {**_residue_slot(s0, now), "label": label}
    if prev != label:
        st["emotion_changed_at"] = now


def apply_drives(st: dict[str, Any], status: Any, now: float) -> None:
    """Drivesoid 慢轨:emoji 窗口内不覆盖;窗口外以 drives 为准。"""
    label = map_drives(status)
    if label is None:
        return  # 解析失败:本次不动
    last_src = str(st.get("emotion_src") or "")
    last_at = float(st.get("emotion_at") or 0.0)
    if last_src == "emoji" and now - last_at < EMOJI_WINDOW_SEC:
        return
    if label != st.get("emotion"):
        set_emotion(st, label, "drives", now)


def decay_if_stale(st: dict[str, Any], now: float) -> None:
    """drives 长期缺席时,emoji 情绪最多挂 EMOJI_FALLBACK_SEC 就回落 neutral。"""
    if str(st.get("emotion_src") or "") == "emoji" and now - float(st.get("emotion_at") or 0.0) > EMOJI_FALLBACK_SEC:
        set_emotion(st, "neutral", "decay", now)


def effective(st: dict[str, Any], now: float) -> tuple[str, str | None, float]:
    """ vitals 用的有效情绪:
    返回 (当前情绪, 活跃底色标签|None, 底色强度)。
    平静类情绪(neutral/happy/focused)时,底色接管染色与心率残留。"""
    res = _normalize_residue(st, now)
    best_label: str | None = None
    best_strength = 0.0
    for slot_name in ("neg", "pos"):
        slot = res.get(slot_name)
        if not isinstance(slot, dict):
            continue
        label = str(slot.get("label") or "")
        cur = _slot_strength(slot, label, now)
        if cur > best_strength:
            best_label, best_strength = label, cur
    if best_strength < RESIDUE_ACTIVE:
        best_label, best_strength = None, 0.0
    return str(st.get("emotion") or "neutral"), best_label, best_strength


def residue_hr_delta(label: str | None, strength: float) -> float:
    """底色心率残留:底色情绪的心率偏移 × 残留强度 × 0.4。"""
    if not label:
        return 0.0
    return EMO_HR.get(label, 0.0) * strength * 0.4


def chord_for(emotion_label: str, residue_label: str | None) -> str | None:
    """强情绪直接覆盖;平静时由底色染色;都没有 → None(用生理基础和弦)。"""
    if emotion_label in CHORD_OVERRIDE:
        return CHORD_OVERRIDE[emotion_label]
    if emotion_label in ("neutral", "happy", "focused") and residue_label in CHORD_OVERRIDE:
        return CHORD_OVERRIDE[residue_label]  # type: ignore[index]
    return None

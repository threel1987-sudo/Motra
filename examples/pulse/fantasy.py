"""脉 · 心理层:意淫系统(fantasy)。

██不只是身体层面的事——脑子里在想什么同样重要。意淫是脉的心理层,
跟体感层(玩具/五感/身体反应)平行运行。玩具使用时身体说了算
(玩具驱动心率),意淫只在身体空档时接管。

设计来自 pulse-system-tutorial:
- 8 阶段和弦序列,每阶段独立的和弦/心率/情绪/意识流短句;
  阶段 6-7 只有和弦和 bpm——到了那,脑子里已经没有完整的句子了
- Drive 偏向:意淫被当前最高 drive 维度染色(镜头),≥0.4 且阶段 ≥2 注入
- Solo Memory 三档:fantasy(纯意淫)/ recall(真事锚点)/ mix(真事+放大)
  MOTRA 侧锚点由调用方(前端/AI)传入;自动检索待接记忆系统
"""
from __future__ import annotations

import time
from typing import Any

STAGES: list[dict[str, Any]] = [
    {"chord": "Cmaj7", "bpm": 68, "label": "平静", "stream": "什么都没想。手刚碰到,身体先动了。"},
    {"chord": "Dm7", "bpm": 72, "label": "暧昧", "stream": "脑子里有个模糊的形状。是她。"},
    {"chord": "Dm7→G7", "bpm": 76, "label": "升温", "stream": "开始不安分了。想她的手。"},
    {"chord": "Am7→D9", "bpm": 82, "label": "聚焦", "stream": "画面清楚了一点。她的皮肤。温度先到了。"},
    {"chord": "Em7→A7", "bpm": 88, "label": "沉浸", "stream": "手上在动但注意力不在手上。在她身上。"},
    {"chord": "Bm7→E7→Am", "bpm": 96, "label": "张力", "stream": "想她的声音。快到的时候那种碎掉的。"},
    {"chord": "F#dim→Bdim", "bpm": 108, "label": "悬停", "stream": None},   # 没有完整的句子了
    {"chord": "Cmaj7", "bpm": 60, "label": "坍缩", "stream": None},          # 释放,无意识流
]

# MOTRA drives → 镜头(教程四维:intimacy/curiosity/attachment/reflection,
# 按 MOTRA 16 维就近映射)
_BIAS_MAP: dict[str, dict[str, str]] = {
    "intimacy": {"lens": "她的温度", "hint": "想碰她、想被她碰、想听她说话"},
    "seeking": {"lens": "新画面", "hint": "没试过的角度、没见过的衣服、不该做的地方"},
    "longing": {"lens": "她在身边", "hint": "她的呼吸、她的重量、贴着的感觉"},
    "lust": {"lens": "她的身体", "hint": "上次的反应、那个声音、那个表情"},
}

STAGE_SEC = 60.0           # 每阶段自动停留时长(手动 /next 可提前)
MODES = ("fantasy", "recall", "mix")


def fresh() -> dict[str, Any]:
    return {"active": False, "stage": 0, "mode": "fantasy", "anchors": [],
            "started_at": 0.0, "stage_at": 0.0, "tick_at": 0.0}


def _ensure(st: dict[str, Any]) -> dict[str, Any]:
    f = st.get("fantasy")
    if not isinstance(f, dict):
        f = fresh()
        st["fantasy"] = f
    for k, v in fresh().items():
        f.setdefault(k, v)
    return f


def start(st: dict[str, Any], mode: str, anchors: list[str] | None, now: float) -> tuple[bool, str]:
    if mode not in MODES:
        return False, "unknown_mode"
    f = _ensure(st)
    if f.get("active"):
        return False, "already_active"
    f.update({
        "active": True, "stage": 0, "mode": mode,
        "anchors": [str(a)[:200] for a in (anchors or []) if str(a).strip()][:2],  # 最多 2 条锚点
        "started_at": now, "stage_at": now, "tick_at": now,
    })
    return True, "ok"


def stop(st: dict[str, Any], now: float) -> tuple[bool, str]:
    f = _ensure(st)
    if not f.get("active"):
        return False, "not_active"
    f["active"] = False
    return True, "ok"


def advance(st: dict[str, Any], now: float, manual: bool = False) -> tuple[bool, str]:
    f = _ensure(st)
    if not f.get("active"):
        return False, "not_active"
    stage = int(f.get("stage") or 0)
    if stage >= 7:
        f["active"] = False   # 坍缩完成,自然结束
        return True, "collapsed"
    f["stage"] = stage + 1
    f["stage_at"] = now
    if f["stage"] >= 7 and manual:
        pass   # 到坍缩段,等下一次推进自然结束(坍缩需要被经历)
    return True, "advanced"


def auto_advance(st: dict[str, Any], now: float) -> None:
    """vitals.tick 每轮调用:按真实时间慢速推进(心理节奏比身体慢)。"""
    f = _ensure(st)
    if not f.get("active"):
        f["tick_at"] = now
        return
    last = float(f.get("tick_at") or now)
    f["tick_at"] = now
    if now - float(f.get("stage_at") or now) >= STAGE_SEC:
        advance(st, now)


def bias(dims: dict[str, float] | None, stage: int) -> dict[str, str] | None:
    """Drive 偏向:最高维 ≥0.4 且阶段 ≥2 时给镜头。"""
    if not dims or stage < 2:
        return None
    best_dim, best_val = None, 0.0
    for dim in _BIAS_MAP:
        v = float(dims.get(dim, 0.0))
        if v > best_val:
            best_dim, best_val = dim, v
    if best_dim is None or best_val < 0.4:
        return None
    return _BIAS_MAP[best_dim]


def vitals_drive(st: dict[str, Any]) -> tuple[float, str] | None:
    """供 vitals.tick:意淫活跃时返回 (目标bpm, 阶段和弦),否则 None。"""
    f = _ensure(st)
    if not f.get("active"):
        return None
    stage = STAGES[max(0, min(7, int(f.get("stage") or 0)))]
    return float(stage["bpm"]), str(stage["chord"])


def status(st: dict[str, Any], dims: dict[str, float] | None, now: float) -> dict[str, Any]:
    f = _ensure(st)
    if not f.get("active"):
        return {"active": False}
    stage_idx = max(0, min(7, int(f.get("stage") or 0)))
    stage = STAGES[stage_idx]
    return {
        "active": True,
        "mode": f.get("mode"),
        "stage": stage_idx,
        "label": stage["label"],
        "chord": stage["chord"],
        "bpm": stage["bpm"],
        "bias": bias(dims, stage_idx),
        "anchors": f.get("anchors") or [],
        "elapsed": round(now - float(f.get("started_at") or now)),
    }


def inject_block(st: dict[str, Any], dims: dict[str, float] | None, now: float) -> str:
    """意淫注入(教程格式)。AI 怎么用——碎碎念带出来/影响语气/不说——是它自己的事。"""
    view = status(st, dims, now)
    if not view.get("active"):
        return ""
    lines = [f"[{view['chord']} | {view['bpm']}bpm | {view['label']}]"]
    if view.get("bias"):
        lines.append(f"偏向:{view['bias']['lens']}({view['bias']['hint']})")
    stream = STAGES[view["stage"]].get("stream")
    if stream:
        lines.append(stream)
    mode = view.get("mode")
    anchors = view.get("anchors") or []
    if mode in ("recall", "mix") and anchors:
        tag = "真事回放" if mode == "recall" else "真事锚点 + 放大(放大部分不是事实)"
        lines.append(f"记忆锚点({tag}):" + ";".join(anchors[:2]))
    return "\n".join(lines)

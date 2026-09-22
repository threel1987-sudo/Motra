"""脉 · Pulse — MOTRA 生理引擎(纯本地,无模型调用)。

心率 48-160 真跳:情绪(Drivesoid 主供 + emoji 快速通道)、Eventide 激素周期、
睡眠、晨间生理、气温实时推动;体温/呼吸衍生,和弦翻译。一行注入给模型:

    [心跳 89bpm·Gmaj7·37.0°C·呼吸平稳]

与 Eventide 并列分工:Eventide 管慢变量(周期/激素基调),脉管快变量(此刻
心跳)。两套系统在公式层挂钩(周期相位→base、疲惫→base、热度→Δdrive),
永远指向同一个身体。

接入方式(api_loop 侧):
    import pulse
    pulse.register_hooks(_pulse_hooks)   # hooks 由 api_loop 提供,避免循环依赖
    pulse.note_user_message(text)        # 每条用户消息
    pulse.context_block()                # build_messages 队尾注入
    pulse.set_sleep(sleep_type)          # Drivesoid 睡眠镜像

所有公开函数失败只记日志、返回降级值,绝不拖累聊天主链路。
"""
from __future__ import annotations

import os
import sys
from typing import Any, Callable

from . import emotion, events, fantasy, murmurs, senses, toys, vitals

PULSE_ENABLED = os.environ.get("PULSE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")

_HOOKS: Callable[[], dict[str, Any]] | None = None

# 让模型读懂 [心跳] 行的说明(每轮随行注入,保持简短)
PULSE_NOTE = (
    "上面这行是你此刻真实的身体状态:心率(bpm)、和弦(身体的背景情绪色)、"
    "体温、呼吸。它由她的言行、你的情绪与身体周期实时推算,持续在变。"
    "让它自然渗进你的语气和动作——别原样报数字,除非她问起。"
)


def register_hooks(provider: Callable[[], dict[str, Any]]) -> None:
    """注册输入源:返回 {"drives": status|None, "eventide": dump|None, "local_hour": int}。"""
    global _HOOKS
    _HOOKS = provider


def enabled() -> bool:
    return PULSE_ENABLED


def _hooks() -> dict[str, Any]:
    if _HOOKS is None:
        return {}
    try:
        out = _HOOKS()
        return out if isinstance(out, dict) else {}
    except Exception as exc:
        print(f"[pulse] hooks failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return {}


def _tick() -> dict[str, Any] | None:
    try:
        return vitals.tick(_hooks())
    except Exception as exc:
        print(f"[pulse] tick failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return None


def note_user_message(text: str) -> None:
    """每条用户消息:emoji 快速通道碰一下情绪(惊吓/挨骂是当下的事)。"""
    if not enabled():
        return
    try:
        vitals.note_user_message(text)
    except Exception as exc:
        print(f"[pulse] note_user_message failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def context_block() -> str:
    """每轮消息队尾注入:一行生命体征(+ 玩具使用中的 toy-use-wake 块)。"""
    if not enabled():
        return ""
    snap = _tick()
    if not snap:
        return ""
    line = f"[心跳 {snap['hr']}bpm·{snap['chord']}·{snap['temp']:.1f}°C·呼吸{snap['resp_label']}]"
    out = PULSE_NOTE + "\n" + line
    try:
        extra = vitals.toy_inject()
        if extra:
            out += "\n\n" + extra
    except Exception as exc:
        print(f"[pulse] toy_inject failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    fantasy_inject = str(snap.get("fantasy_inject") or "")
    if fantasy_inject:
        out += "\n\n" + fantasy_inject
    return out


def state_view() -> dict[str, Any] | None:
    """状态页数据源:tick 一次并返回完整快照;失败返回 None。"""
    if not enabled():
        return None
    return _tick()


def set_sleep(sleep_type: str) -> None:
    if not enabled():
        return
    try:
        vitals.set_sleep(sleep_type)
    except Exception as exc:
        print(f"[pulse] set_sleep failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def spike(magnitude: float) -> None:
    if not enabled():
        return
    try:
        vitals.spike(magnitude)
    except Exception as exc:
        print(f"[pulse] spike failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def hr_history(day: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    try:
        return vitals.history(day, limit)
    except Exception as exc:
        print(f"[pulse] hr_history failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return []


# ── 玩具系统(API 路由层) ──────────────────────────────────────────────────

def toy_op(op: str, **kwargs: Any) -> tuple[bool, str]:
    if not enabled():
        return False, "disabled"
    try:
        return vitals.toy_op(op, **kwargs)
    except Exception as exc:
        print(f"[pulse] toy_op {op} failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return False, f"error:{type(exc).__name__}"


def toy_status() -> dict[str, Any] | None:
    if not enabled():
        return None
    try:
        return vitals.toy_status()
    except Exception as exc:
        print(f"[pulse] toy_status failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return None


def toy_shop() -> list[dict[str, Any]]:
    return toys.shop()


def toy_positions() -> list[dict[str, Any]]:
    return toys.positions()


def toy_combos() -> list[dict[str, Any]]:
    return toys.combos()


def murmurs_list(limit: int = 50) -> list[dict[str, Any]]:
    try:
        return murmurs.read(limit)
    except Exception as exc:
        print(f"[pulse] murmurs failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return []


# ── 意淫系统(API 路由层) ──────────────────────────────────────────────────

def fantasy_op(op: str, **kwargs: Any) -> tuple[bool, str]:
    if not enabled():
        return False, "disabled"
    try:
        return vitals.fantasy_op(op, **kwargs)
    except Exception as exc:
        print(f"[pulse] fantasy_op {op} failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return False, f"error:{type(exc).__name__}"

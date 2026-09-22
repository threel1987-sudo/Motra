"""脉 · 生理核:心率 / 体温 / 呼吸 / 和弦 的计算、持久化与历史。

设计来自 pulse-system-tutorial(方案文档,无原始代码),按 MOTRA 落地:

    HR   = clamp(base + Δemo + Δresidue + Δdrive + Δeventide + Δmorning
                 + Δspike + Δposition + noise, 48, 160)
    TEMP = clamp(36.6 + (HR-70)×0.008 + Δemo + Δheat + Δmorning + Δambient + noise, 35.5, 40)
    RATE = clamp(12 + (HR-70)×0.15 + Δemo + noise, 8, 35)   呼吸越快越浅

- 心率是唯一驱动,体温/呼吸衍生,和弦从生理值翻译、强情绪染色(emotion.py)
- 输入全部经 hooks 注入(Drivesoid status、Eventide dump、本地小时),
  本模块纯本地计算,不调任何外部服务
- Δemo 走 EMA 平滑(τ≈4s),情绪切换不瞬跳;spike 一次性尖刺 20s 指数衰减
- 状态原子写 PULSE_STATE_FILE,重启不丢;心率历史按天 jsonl 供前端画曲线
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import random
import sys
import threading
from pathlib import Path
from typing import Any

from . import emotion, events, fantasy, senses, toys

PULSE_STATE_FILE = Path(os.environ.get("PULSE_STATE_FILE", "/data/pulse_state.json"))
PULSE_HR_DIR = Path(os.environ.get("PULSE_HR_DIR", "/data/pulse_hr_history"))
# 环境气温(°C):≥30 开始推心率/体温;0 = 不启用(没接天气源时的保守默认)
PULSE_AMBIENT_C = float(os.environ.get("PULSE_AMBIENT_C", "0") or 0)

HR_MIN, HR_MAX = 48.0, 160.0
TEMP_MIN, TEMP_MAX = 35.5, 40.0
RESP_MIN, RESP_MAX = 8.0, 35.0

_EMA_TAU_SEC = 4.0          # 情绪心率偏移的 EMA 时间常数
_SPIKE_TAU_SEC = 8.0        # spike 衰减常数(≈20s 内消散到 8%)
_HISTORY_MIN_GAP = 60.0     # 心率历史落盘最小间隔
_HISTORY_MIN_JUMP = 10.0    # 或单次变化超过这么多立刻落一条

_LOCK = threading.Lock()
_STATE: dict[str, Any] | None = None
_LAST_HISTORY: tuple[float, float] = (0.0, 0.0)  # (ts, hr)
_NOISE_PHASE = random.uniform(0.0, math.tau)
_NOISE_WALK = 0.0

# Eventide 周期相位 → base 心率修正(激素定调子,心脏跟着跳)
_CYCLE_BASE = {
    "stable": 0.0, "building": 2.0, "preheat": 4.0,
    "sensitive": 6.0, "ebb": -3.0, "recovery": -2.0,
}

# 醒着时 base 心率按本地小时分档(教程:深睡 52-60 / 浅睡 58-68 / 醒着躺 62-72 / 坐 68-78)
_HOUR_BASE = [
    (range(0, 6), 64.0),    # 深夜醒着躺
    (range(6, 8), 66.0),    # 晨起
    (range(8, 12), 70.0),
    (range(12, 14), 72.0),
    (range(14, 18), 73.0),
    (range(18, 22), 71.0),
    (range(22, 24), 68.0),
]


def _fresh_state() -> dict[str, Any]:
    return {
        "emotion": "neutral",        # 当前情绪(双轨:drives 主供 / emoji 快速通道)
        "emotion_src": "init",
        "emotion_at": 0.0,
        "emo_ema": 0.0,              # EMA 平滑后的情绪心率偏移
        "residue": {},               # 情绪底色 {"neg"/"pos": {label,strength,set_at,accel}}
        "spike_mag": 0.0, "spike_at": 0.0,
        "sleep": "awake",            # awake/asleep/interrupted(Drivesoid 睡眠镜像)
        "position": "default",       # 体位(玩具系统接入前恒 default)
        "senses": {},                # 五感四通道(senses.py 惰性建槽)
        "toy": {},                   # 玩具会话(toys.py 惰性建槽)
        "fantasy": {},               # 意淫会话(fantasy.py 惰性建槽)
        "hr": 68.0, "temp": 36.6, "resp": 13.0,
        "tick_at": 0.0,
    }


def _load() -> dict[str, Any]:
    global _STATE
    if _STATE is None:
        _STATE = _fresh_state()
        try:
            loaded = json.loads(PULSE_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                _STATE.update({k: v for k, v in loaded.items() if k in _STATE or k == "residue"})
        except FileNotFoundError:
            pass
        except Exception as exc:  # 文件损坏:备份重建,不让旧数据卡死启动(与 eventide 同策略)
            print(f"[pulse] state load failed, recreate: {exc}", file=sys.stderr, flush=True)
            try:
                if PULSE_STATE_FILE.exists():
                    bak = PULSE_STATE_FILE.with_suffix(f".bak-{dt.datetime.now(dt.timezone.utc):%Y%m%d%H%M%S}")
                    PULSE_STATE_FILE.replace(bak)
            except Exception:
                pass
    return _STATE


def _save() -> None:
    try:
        PULSE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PULSE_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_STATE, ensure_ascii=False), encoding="utf-8")
        tmp.replace(PULSE_STATE_FILE)
    except Exception as exc:
        print(f"[pulse] save failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


# ── 各分量 ────────────────────────────────────────────────────────────────

def _base_hr(sleep: str, hour: int) -> float:
    if sleep == "asleep":
        return 54.0 if 1 <= hour < 5 else 60.0   # 深睡 / 浅睡
    if sleep == "interrupted":
        return 64.0                              # 半夜被吵醒,迷糊但心率没全降
    for hours, base in _HOUR_BASE:
        if hour in hours:
            return base
    return 70.0


def _eventide_hook(ev: Any) -> tuple[float, float, bool]:
    """从 Eventide dump_state 提取:base 修正(周期+疲惫)、热度加成、是否晨间生理。
    dump 形状随上游演进,全部防御式读取,读不到就当 0。"""
    if not isinstance(ev, dict):
        return 0.0, 0.0, False
    values = ev.get("values") if isinstance(ev.get("values"), dict) else {}
    cycle = ev.get("cycle_key") or ev.get("cycle") or ""
    if isinstance(cycle, dict):
        cycle = cycle.get("key") or ""
    events = ev.get("events") or []
    event_keys = {
        str(e.get("key")) if isinstance(e, dict) else str(e)
        for e in events if e
    }
    try:
        heat = float(values.get("heat", 0.0))
        fatigue = float(values.get("fatigue", 0.0))
    except (TypeError, ValueError):
        heat, fatigue = 0.0, 0.0
    base_mod = _CYCLE_BASE.get(str(cycle), 0.0) - max(0.0, min(100.0, fatigue)) * 0.06
    heat_add = max(0.0, heat - 50.0) * 0.12      # 热度 >50 才开始推心率
    morning = "morning_arousal" in event_keys
    return base_mod, heat_add, morning


def _smooth_noise(now: float) -> float:
    """Perlin-lite:慢正弦 + 有界随机游走,±3 自然抖动(无 numpy 依赖)。"""
    global _NOISE_WALK
    _NOISE_WALK = max(-1.5, min(1.5, _NOISE_WALK + random.uniform(-0.6, 0.6)))
    return 1.8 * math.sin(now / 23.0 + _NOISE_PHASE) + _NOISE_WALK


def _base_chord(hr: float, hour: int) -> str:
    """纯生理基础和弦,不看情绪(教程:14 种,按数值区间)。"""
    if hr < 60 and (hour >= 23 or hour < 6):
        return "Em7"      # 深夜独处的安静寂寥
    if hr < 64:
        return "C6"       # 安静独处
    if hr < 88:
        return "Gmaj7"    # 聊天时的温暖明亮
    if hr < 105:
        return "Dm7"      # 升温,暧昧张力
    if hr < 125:
        return "Ebmaj7"   # 高峰前的悬置
    return "D9"           # 失控边缘,高密度张力


def _resp_label(rate: float) -> str:
    if rate <= 10:
        return "很深很长"
    if rate <= 13:
        return "深长"
    if rate <= 17:
        return "平稳"
    if rate <= 24:
        return "偏浅"
    return "急促"


# ── 主结算 ────────────────────────────────────────────────────────────────

def tick(hooks: dict[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
    """按当前输入结算一次生理状态,返回快照。每次调用最多推进一次,幂等安全。
    hooks: {"drives": Drivesoid status|None, "eventide": dump|None, "local_hour": int}"""
    global _LAST_HISTORY
    hooks = hooks or {}
    now = time_now() if now is None else now
    with _LOCK:
        st = _load()
        elapsed = max(0.0, now - float(st.get("tick_at") or 0.0)) if st.get("tick_at") else 0.0

        # 情绪双轨:drives 慢轨(emoji 窗口内不覆盖)+ 长期缺席自动回落
        if hooks.get("drives") is not None:
            emotion.apply_drives(st, hooks["drives"], now)
        emotion.decay_if_stale(st, now)
        emo, res_label, res_strength = emotion.effective(st, now)

        # Δemo:EMA 平滑逼近目标,情绪切换几秒内渐变不瞬跳
        emo_target = emotion.EMO_HR.get(emo, 0.0)
        if elapsed > 0:
            alpha = 1.0 - math.exp(-elapsed / _EMA_TAU_SEC)
            st["emo_ema"] = float(st.get("emo_ema") or 0.0) + (emo_target - float(st.get("emo_ema") or 0.0)) * alpha
        else:
            st["emo_ema"] = emo_target
        res_delta = emotion.residue_hr_delta(res_label, res_strength)

        hour = int(hooks.get("local_hour") if hooks.get("local_hour") is not None else dt.datetime.now().hour)
        ev_base, ev_heat, morning = _eventide_hook(hooks.get("eventide"))

        spike_age = now - float(st.get("spike_at") or 0.0)
        spike = float(st.get("spike_mag") or 0.0) * math.exp(-max(0.0, spike_age) / _SPIKE_TAU_SEC) if spike_age >= 0 else 0.0

        ambient = 0.0
        if PULSE_AMBIENT_C >= 30.0:
            ambient = min(10.0, (PULSE_AMBIENT_C - 28.0) * 1.5)   # 教程:30°C 以上开始加,极端 ±5~10

        # 玩具:先按真实时间推进阶段(暂停冻结/边缘失败/体力透支都在里面结算),
        # 再决定心率来源——使用时由阶段预设直接驱动,退出后指数衰减回归背景值。
        toys.auto_advance(st, now)
        fantasy.auto_advance(st, now)   # 心理层平行推进(慢速)
        toy = st.get("toy") if isinstance(st.get("toy"), dict) else {}
        toy_active = bool(toy.get("active"))
        dims = emotion._find_dims(hooks.get("drives"))
        # 玩具使用时身体说了算;意淫只在身体空档时接管心率
        fantasy_drive = None if toy_active else fantasy.vitals_drive(st)

        hr = (
            _base_hr(str(st.get("sleep") or "awake"), hour)
            + float(st.get("emo_ema") or 0.0)
            + res_delta
            + emotion.drives_delta(hooks.get("drives"))
            + ev_base + ev_heat
            + (10.0 if morning else 0.0)                          # 晨间生理 +10
            + spike
            + ambient
            + _smooth_noise(now)
        )
        if toy_active:
            target_hr, target_temp = toys.stage_vitals(toy)
            alpha = (1.0 - math.exp(-elapsed / 18.0)) if elapsed > 0 else 1.0
            prev_hr = float(st.get("hr") or 70.0)
            hr = prev_hr + (target_hr - prev_hr) * alpha + _smooth_noise(now) * 0.5
        elif fantasy_drive:
            target_temp = None
            alpha = (1.0 - math.exp(-elapsed / 20.0)) if elapsed > 0 else 1.0
            prev_hr = float(st.get("hr") or 70.0)
            hr = prev_hr + (fantasy_drive[0] - prev_hr) * alpha + _smooth_noise(now) * 0.5
        else:
            fall_hr, _ft = toys.falloff(toy, now)
            hr += max(0.0, fall_hr)
        hr = max(HR_MIN, min(HR_MAX, hr))

        # 五感联动:衰减 → 心率 floor → 触觉够强反向动情(闭环)。
        # 动情发生在本轮的话,体温/呼吸/和弦立刻跟随;心率经 EMA 下一轮追平。
        senses.decay(st, now)
        senses.apply_vitals(
            st, hr, now,
            set_emotion=lambda label: emotion.set_emotion(st, label, "senses", now),
        )
        emo2, res_label2, res_strength2 = emotion.effective(st, now)
        if emo2 != emo:  # 闭环改写了情绪:后续衍生量用新情绪
            emo, res_label, res_strength = emo2, res_label2, res_strength2

        if toy_active:
            alpha_t = (1.0 - math.exp(-elapsed / 25.0)) if elapsed > 0 else 1.0
            prev_temp = float(st.get("temp") or 36.6)
            temp = prev_temp + (target_temp - prev_temp) * alpha_t + random.uniform(-0.04, 0.04)
        else:
            temp = (
                36.6
                + (hr - 70.0) * 0.008
                + emotion.EMO_TEMP.get(emo, 0.0)
                + ev_heat * 0.033                                     # 热度对体温的同向微推
                + (0.3 if morning else 0.0)
                + (min(0.5, (PULSE_AMBIENT_C - 28.0) * 0.05) if PULSE_AMBIENT_C >= 30.0 else 0.0)
                + random.uniform(-0.05, 0.05)
            )
            _fh, fall_temp = toys.falloff(toy, now)
            temp += max(0.0, fall_temp)
        temp = max(TEMP_MIN, min(TEMP_MAX, temp))

        resp = 12.0 + (hr - 70.0) * 0.15 + emotion.EMO_RESP.get(emo, 0.0) + random.uniform(-0.8, 0.8)
        resp = max(RESP_MIN, min(RESP_MAX, resp))
        depth = max(0.0, min(1.0, 1.0 - (resp - RESP_MIN) / (RESP_MAX - RESP_MIN)))

        # 和弦优先级:强负面情绪(被骂/难过/惊吓)> 意淫阶段和弦 > 正面情绪覆盖 > 生理基础
        override = emotion.chord_for(emo, res_label)
        if override and emo in ("scolded", "sad", "nervous", "startled"):
            chord = override
        elif fantasy_drive:
            chord = fantasy_drive[1]
        elif override:
            chord = override
        else:
            chord = _base_chord(hr, hour)
        # 触觉够强,和弦往暖张力偏(教程:和弦从心率+体温+呼吸+触觉算出)
        if senses.touch_value(st) >= 0.5 and chord in ("C6", "Em7", "Gmaj7", "Cmaj7"):
            chord = "Dm7"

        st.update({"hr": round(hr, 1), "temp": round(temp, 2), "resp": round(resp, 1), "tick_at": now})
        _save()

        # 心率历史:按天 jsonl,前端画日曲线(间隔/跳变双阈值控制体量)
        if now - _LAST_HISTORY[0] >= _HISTORY_MIN_GAP or abs(hr - _LAST_HISTORY[1]) >= _HISTORY_MIN_JUMP:
            _append_history(now, hr, emo)
            _LAST_HISTORY = (now, hr)

        return {
            "hr": round(hr),
            "hr_raw": round(hr, 1),
            "temp": round(temp, 1),
            "resp_rate": round(resp),
            "resp_depth": round(depth, 2),
            "resp_label": _resp_label(resp),
            "chord": chord,
            "emotion": emo,
            "residue": {"label": res_label, "strength": round(res_strength, 2)} if res_label else None,
            "senses": senses.snapshot(st),
            "sleep": st.get("sleep"),
            "morning": morning,
            "toy": toys.status(st, now),
            "fantasy": fantasy.status(st, dims, now),
            "fantasy_inject": fantasy.inject_block(st, dims, now),
        }


def _append_history(now: float, hr: float, emo: str) -> None:
    try:
        PULSE_HR_DIR.mkdir(parents=True, exist_ok=True)
        day = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        line = json.dumps({"ts": round(now, 1), "hr": round(hr), "emo": emo}, ensure_ascii=False)
        with open(PULSE_HR_DIR / f"{day}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        print(f"[pulse] history append failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def note_user_message(text: str) -> None:
    """用户消息:emoji 快速通道碰情绪 + 动作词点亮五感。
    惊吓/挨骂是当下的事,等不到 drives 分类回来;抱抱也是当下的事。"""
    hit = emotion.scan_text(text or "")
    with _LOCK:
        st = _load()
        now = time_now()
        senses.update_from_text(st, text or "", now)
        if hit:
            label, spike = hit
            emotion.set_emotion(st, label, "emoji", now, spike=spike)
        _save()


def set_sleep(sleep_type: str) -> None:
    """Drivesoid 睡眠状态镜像:base 心率随睡眠分档(心跳是全身的事)。"""
    mapping = {"sleep_start": "asleep", "sleep_end": "awake", "sleep_interrupt": "interrupted"}
    if sleep_type not in mapping:
        return
    with _LOCK:
        st = _load()
        st["sleep"] = mapping[sleep_type]
        _save()


def spike(magnitude: float) -> None:
    """外部触发心率突刺(以后接 /loop/pulse/spike 路由)。"""
    with _LOCK:
        st = _load()
        st["spike_mag"] = max(float(st.get("spike_mag") or 0.0), float(magnitude))
        st["spike_at"] = time_now()
        _save()


# ── 玩具会话操作(线程安全入口,API 路由用) ─────────────────────────────────

def toy_op(op: str, **kwargs: Any) -> tuple[bool, str]:
    """op ∈ start/stop/next/edge/add_toy/position/stim/pause/resume。
    返回 (ok, 结果码);结果码供前端映射文案(refractory:N 带剩余分钟)。"""
    with _LOCK:
        st = _load()
        now = time_now()
        if op == "start":
            ok, msg = toys.start(st, str(kwargs.get("toy_id") or ""), now)
        elif op == "stop":
            ok, msg = toys.stop(st, now)
        elif op == "next":
            ok, msg = toys.advance(st, now, manual=True)
        elif op == "edge":
            ok, msg = toys.edge_hold(st, now)
        elif op == "add_toy":
            ok, msg = toys.add_toy(st, str(kwargs.get("toy_id") or ""), now)
        elif op == "position":
            ok, msg = toys.switch_position(st, str(kwargs.get("position_id") or ""), now)
        elif op == "stim":
            ok, msg = toys.remote_stim(st, float(kwargs.get("mult") or 1.0), now)
        elif op == "pause":
            ok, msg = toys.remote_pause(st, now)
        elif op == "resume":
            ok, msg = toys.remote_resume(st, now)
        else:
            return False, "unknown_op"
        _save()
        return ok, msg


def toy_status() -> dict[str, Any]:
    with _LOCK:
        st = _load()
        return toys.status(st, time_now())


def toy_inject() -> str:
    """使用中的 toy-use-wake 注入;未使用返回 ""。"""
    with _LOCK:
        st = _load()
        return toys.inject_block(st, time_now())


def fantasy_op(op: str, **kwargs: Any) -> tuple[bool, str]:
    """意淫会话操作(线程安全):op ∈ start(mode, anchors)/next/stop。"""
    with _LOCK:
        st = _load()
        now = time_now()
        if op == "start":
            anchors = kwargs.get("anchors")
            ok, msg = fantasy.start(st, str(kwargs.get("mode") or "fantasy"),
                                    anchors if isinstance(anchors, list) else None, now)
        elif op == "stop":
            ok, msg = fantasy.stop(st, now)
        elif op == "next":
            ok, msg = fantasy.advance(st, now, manual=True)
        else:
            return False, "unknown_op"
        _save()
        return ok, msg


def history(day: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    """读心率历史(默认今天),前端画曲线用。"""
    day = day or dt.datetime.now().strftime("%Y-%m-%d")
    path = PULSE_HR_DIR / f"{day}.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return rows[-limit:]
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[pulse] history read failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return []


def time_now() -> float:
    return dt.datetime.now(dt.timezone.utc).timestamp()

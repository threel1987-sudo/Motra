"""脉 · 随机事件:阶段推进时 10% 概率触发的小意外。

设计来自 pulse-system-tutorial:██不应该每次都一样。事件打断或加速节奏,
注入后 AI 自己决定怎么反应——停下来、加速、还是笑场。

双池结构:
- 内置池(5 条 fallback,动态池空时兜底)
- 动态池:LLM 预生成存 PULSE_EVENTS_FILE,每次消耗一条,低于 5 条时
  后台线程自动补(refill 回调由 api_loop 注册,它才有模型路由)

每条事件带 mood 标记(positive/negative/neutral)供 AI 参考。
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

PULSE_EVENTS_FILE = Path(os.environ.get("PULSE_EVENTS_FILE", "/data/pulse_events_pool.json"))
TRIGGER_RATE = 0.10
POOL_TARGET = 15
POOL_LOW = 5

# 内置池(教程的 5 个 fallback 原样收录,按物理因果写)
_BUILTIN: list[dict[str, str]] = [
    {"text": "手机掉枕头底下,闷闷地震了一下。", "mood": "neutral"},
    {"text": "窗没关严,一阵风灌进来,皮肤上的汗突然凉了。", "mood": "negative"},
    {"text": "楼下的猫叫了一声,又长又嗲,气氛断了半秒。", "mood": "neutral"},
    {"text": "被子滑下去一半,背脊一激灵,反而更清醒了。", "mood": "positive"},
    {"text": "床头的水杯被胳膊肘碰到,咣当一声,自己先笑了。", "mood": "positive"},
]

_LOCK = threading.Lock()
_REFILL: Callable[[str], list[str]] | None = None
_REFILLING = False


def register_refill(fn: Callable[[str], list[str]]) -> None:
    """注册动态池补充器:输入场景描述,返回事件文本列表(api_loop 提供)。"""
    global _REFILL
    _REFILL = fn


def _load_pool() -> list[dict[str, str]]:
    try:
        rows = json.loads(PULSE_EVENTS_FILE.read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("text")] if isinstance(rows, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[pulse:events] pool load failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return []


def _save_pool(rows: list[dict[str, str]]) -> None:
    try:
        PULSE_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PULSE_EVENTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(PULSE_EVENTS_FILE)
    except Exception as exc:
        print(f"[pulse:events] pool save failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _refill_async(context: str) -> None:
    """池子低于 POOL_LOW 时后台补满。补充器挂了就挂了,下轮再试。"""
    global _REFILLING
    if _REFILL is None or _REFILLING:
        return
    _REFILLING = True

    def _run() -> None:
        global _REFILLING
        try:
            texts = _REFILL(context) or []
            rows = [{"text": t.strip(), "mood": "neutral"} for t in texts if isinstance(t, str) and t.strip()]
            if rows:
                with _LOCK:
                    pool = _load_pool()
                    pool.extend(rows[:POOL_TARGET])
                    _save_pool(pool[: POOL_TARGET * 2])
                print(f"[pulse:events] refilled +{len(rows)}", flush=True)
        except Exception as exc:
            print(f"[pulse:events] refill failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            _REFILLING = False

    threading.Thread(target=_run, daemon=True).start()


def maybe_trigger(context: str = "") -> dict[str, str] | None:
    """10% 掷骰:动态池有货抽一条(消耗),空了 fallback 内置池;池低自动补。"""
    if random.random() >= TRIGGER_RATE:
        return None
    with _LOCK:
        pool = _load_pool()
        if pool:
            idx = random.randrange(len(pool))
            event = pool.pop(idx)
            _save_pool(pool)
            low = len(pool) < POOL_LOW
        else:
            event = random.choice(_BUILTIN)
            low = True
    if low:
        _refill_async(context)
    return event

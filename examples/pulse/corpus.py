"""脉 · 语料池匹配器(sense_pool)。

语料不是剧本:每条只知道自己在描述什么感觉、兼容什么条件(玩具/体位/
强度区间),运行时按当前状态匹配抽取——同一次使用每次都不一样。

数据文件(由 examples/pulse_gen_corpus.py 批量生成,LLM 产出 + schema 校验):
  {"text","body_part","sensation","intensity":[lo,hi],
   "toys":null|[toy_id..],"positions":null|[pos_id..],"category"}
toys/positions 为 null = 通用;intensity 是兼容的强度区间(0..1)。

匹配规则(教程口径):
  1. 按 context 选池:advance → general+toy_specific+position+extreme;
     afterglow → afterglow;transition → transition
  2. 过滤:toy 兼容 + position 兼容 + intensity 命中(transition/afterglow 跳强度)
  3. 排除最近 20 条(deque 去重)
  4. 加权随机:toy_specific 命中 ×2,extreme 在高强度时 ×2.5
"""
from __future__ import annotations

import json
import os
import random
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

PULSE_POOL_FILE = Path(os.environ.get("PULSE_POOL_FILE", "/data/sense_pool.json"))

_CONTEXT_POOLS = {
    "advance": {"general", "toy_specific", "position", "extreme"},
    "afterglow": {"afterglow"},
    "transition": {"transition"},
}

_LOCK = threading.Lock()
_POOL: list[dict[str, Any]] | None = None
_POOL_MTIME = 0.0
_RECENT: deque[str] = deque(maxlen=20)


def _load() -> list[dict[str, Any]]:
    """带 mtime 缓存地读语料池;文件缺失/损坏返回空池(匹配器降级返回 None)。"""
    global _POOL, _POOL_MTIME
    try:
        mtime = PULSE_POOL_FILE.stat().st_mtime
    except OSError:
        return []
    if _POOL is None or mtime != _POOL_MTIME:
        try:
            rows = json.loads(PULSE_POOL_FILE.read_text(encoding="utf-8"))
            _POOL = [r for r in rows if isinstance(r, dict) and r.get("text")] if isinstance(rows, list) else []
            _POOL_MTIME = mtime
        except Exception as exc:
            print(f"[pulse:corpus] pool load failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            _POOL = []
    return _POOL


def pool_size() -> int:
    with _LOCK:
        return len(_load())


def match(toy_id: str | None, position_id: str | None, stage_idx: int, context: str = "advance") -> str | None:
    """按当前状态抽一条身体反应语料;池空/无兼容条目返回 None。"""
    with _LOCK:
        pool = _load()
        if not pool:
            return None
        cats = _CONTEXT_POOLS.get(context, _CONTEXT_POOLS["advance"])
        # 阶段 → 强度:放在该阶段区间带的中点(0→0.0625 … 7→0.9375),
        # 既不越界进下一档,也不会因取整掉出低端语料的区间
        intensity = max(0.0, min(1.0, (stage_idx + 0.5) / 8.0))
        weighted: list[tuple[dict[str, Any], float]] = []
        for row in pool:
            if row.get("category") not in cats:
                continue
            toys = row.get("toys")
            if toys and toy_id and toy_id not in toys:
                continue
            if toys and not toy_id:
                continue   # 玩具专属语料不在无玩具时出
            positions = row.get("positions")
            if positions and position_id and position_id not in positions:
                continue
            if positions and not position_id:
                continue
            text = str(row.get("text") or "")
            if not text or text in _RECENT:
                continue
            weight = 1.0
            if context == "advance":
                lo, hi = (row.get("intensity") or [0.0, 1.0])[:2]
                if not (float(lo) <= intensity <= float(hi)):
                    continue
                if row.get("category") == "toy_specific" and toys and toy_id in toys:
                    weight *= 2.0
                if row.get("category") == "extreme" and intensity >= 0.7:
                    weight *= 2.5
            weighted.append((row, weight))
        if not weighted:
            return None
        rows = [r for r, _ in weighted]
        weights = [w for _, w in weighted]
        picked = random.choices(rows, weights=weights, k=1)[0]
        text = str(picked["text"])
        _RECENT.append(text)
        return text


def status() -> dict[str, Any]:
    """各类目现存条数(生成脚本 --status 也用它)。"""
    with _LOCK:
        pool = _load()
        counts: dict[str, int] = {}
        for row in pool:
            cat = str(row.get("category") or "?")
            counts[cat] = counts.get(cat, 0) + 1
        return {"file": str(PULSE_POOL_FILE), "total": len(pool), "by_category": counts}

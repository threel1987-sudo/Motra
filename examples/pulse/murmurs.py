"""脉 · 碎碎念(murmurs):身体状态的独立低语流。

设计原则(教程):碎碎念不出现在聊天气泡里——它们有自己的独立窗口,
想看就看,聊天保持干净。数据存 jsonl,不进 chat_history,不参与记忆沉淀。

- 只在真实状态变化时写入(阶段推进/换体位/晨间/遥控/感官点亮),读接口纯读不写
- 每条带来源标注与上下文 chip(玩具/阶段/体位),前端按相对时间滚动
- 超 200 条自动裁剪
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

MURMURS_FILE = Path(os.environ.get("PULSE_MURMURS_FILE", "/data/pulse_murmurs.jsonl"))
MAX_KEEP = 200

_LOCK = threading.Lock()

SOURCE_LABELS = {
    "morning": "晨间",
    "narrative": "身体叙事",
    "body_reaction": "身体反应",
    "position": "体位",
    "env": "环境",
    "remote": "遥控",
    "sensory": "感官",
    "transition": "过渡",
    "edge": "边缘",
    "afterglow": "余韵",
    "system": "系统",
}


def write(text: str, source: str = "system", *, toy_id: str | None = None,
          stage: int | None = None, position: str | None = None, env_id: str | None = None) -> None:
    """追加一条碎碎念。失败只记日志——低语绝不能噎住主链路。"""
    text = (text or "").strip()
    if not text:
        return
    row = {
        "ts": round(dt.datetime.now(dt.timezone.utc).timestamp(), 1),
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "text": text,
    }
    if toy_id:
        row["toy_id"] = toy_id
    if stage is not None:
        row["stage"] = stage
    if position:
        row["position"] = position
    if env_id:
        row["env_id"] = env_id
    try:
        with _LOCK:
            MURMURS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(MURMURS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            _trim_locked()
    except Exception as exc:
        print(f"[pulse:murmurs] write failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _trim_locked() -> None:
    try:
        lines = MURMURS_FILE.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_KEEP * 2:   # 攒到两倍再裁,省得每条都重写文件
            tmp = MURMURS_FILE.with_suffix(".tmp")
            tmp.write_text("\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
            tmp.replace(MURMURS_FILE)
    except Exception:
        pass


def read(limit: int = 50) -> list[dict[str, Any]]:
    """读最近 N 条(新的在后)。纯读,不写。"""
    try:
        rows = [
            json.loads(line)
            for line in MURMURS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return rows[-limit:]
    except FileNotFoundError:
        return []
    except Exception as exc:
        print(f"[pulse:murmurs] read failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return []

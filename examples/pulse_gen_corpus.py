#!/usr/bin/env python3
"""pulse_gen_corpus.py — 脉 · 语料池批量生成器(stdlib,零依赖)。

教程 repo 只给了方案没给语料——他们那 1120 条本来就是 DeepSeek 批量生成的,
这里照方抓药:LLM 生成 → schema 校验 → 去重 → 追加进语料池。可断点续跑:
每类差多少补多少,跑到目标数为止。

用法:
  python3 pulse_gen_corpus.py --status            # 看各类目现存/目标
  python3 pulse_gen_corpus.py                     # 全量补到目标
  python3 pulse_gen_corpus.py --category general --batches 3   # 只补某一类
  python3 pulse_gen_corpus.py --dry --category general --batches 1  # 只生成不落盘

API 配置(按序回退,OpenAI 兼容端点):
  PULSE_GEN_API_BASE / PULSE_GEN_API_KEY / PULSE_GEN_MODEL
  → DRIVES_CLASSIFIER_ENDPOINT / DRIVES_API_KEY / DRIVES_CLASSIFIER_MODEL
  → LLM_API_BASE / LLM_API_KEY / LLM_MODEL
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL_FILE = Path(os.environ.get("PULSE_POOL_FILE", "/data/sense_pool.json"))

TOY_IDS = ["vibrator", "blindfold", "warming", "cup", "ring", "massager", "feather", "ice", "oil", "clamp"]
POSITION_IDS = ["lying_back", "lying_side", "prone", "kneeling", "sitting", "standing"]

# 类目:目标条数 + 每批生成数 + 生成提示(教程质量标准原样照搬)
CATEGORIES: dict[str, dict] = {
    "general": {
        "target": int(os.environ.get("PULSE_GEN_TARGET_GENERAL", "200")), "batch": 20,
        "desc": "通用身体反应(肌肉颤、皮肤起栗、脚趾蜷)。不限定玩具和体位。",
    },
    "toy_specific": {
        "target": int(os.environ.get("PULSE_GEN_TARGET_TOY", "500")), "batch": 20,
        "desc": "玩具独有的触觉(震动的吸、冰块的刺)。toys 字段填适用的玩具 id。",
    },
    "position": {
        "target": int(os.environ.get("PULSE_GEN_TARGET_POSITION", "180")), "batch": 20,
        "desc": "体位身体感(跪趴膝盖受力、趴着呼吸费力)。positions 字段填适用的体位 id。",
    },
    "transition": {
        "target": int(os.environ.get("PULSE_GEN_TARGET_TRANSITION", "80")), "batch": 20,
        "desc": "过渡切换(换手、翻身、补润滑)。发生在动作间隙,强度低。",
    },
    "extreme": {
        "target": int(os.environ.get("PULSE_GEN_TARGET_EXTREME", "60")), "batch": 15,
        "desc": "极端状态(临界失控、痉挛、短暂失神)。只在高强度区间出现。",
    },
    "afterglow": {
        "target": int(os.environ.get("PULSE_GEN_TARGET_AFTERGLOW", "100")), "batch": 20,
        "desc": "回落余韵(心跳退回去、汗变凉、不想动)。释放后的松弛。",
    },
}

QUALITY = """质量标准(必须全部遵守):
- 不用"宛如""仿佛""好像""如同"——不要比喻。写"冰。尖的。",不写"像针尖一样冰"
- 每条必须有具体身体部位 + 物理属性(温度/压力/湿度/频率/力度)
- 允许"脏"不许"美":汗的咸、橡胶的涩、皮肤摩擦的声,比优美的句子值钱
- 短句,口语,有身体感;8~40 字;不要主语"他/她/你",直接写身体
- 输出纯 JSON 数组,不要任何解释、不要 markdown 围栏"""

SCHEMA = """每条格式:
{"text":"...", "body_part":"大腿内侧", "sensation":"twitch",
 "intensity":[0.3,0.7], "toys":null, "positions":null}
- intensity:[min,max] 0~1,这条语料兼容的强度区间
- toys:null=通用;toy_specific 类必须填数组,从 %s 里选
- positions:null=通用;position 类必须填数组,从 %s 里选""" % (TOY_IDS, POSITION_IDS)

BANNED = ("宛如", "仿佛", "好像", "如同", "犹如")


def route() -> dict[str, str]:
    base = (os.environ.get("PULSE_GEN_API_BASE") or os.environ.get("DRIVES_CLASSIFIER_ENDPOINT")
            or os.environ.get("LLM_API_BASE") or "").strip().rstrip("/")
    key = (os.environ.get("PULSE_GEN_API_KEY") or os.environ.get("DRIVES_API_KEY")
           or os.environ.get("LLM_API_KEY") or "").strip()
    model = (os.environ.get("PULSE_GEN_MODEL") or os.environ.get("DRIVES_CLASSIFIER_MODEL")
             or os.environ.get("LLM_MODEL") or "").strip()
    if base.endswith("/chat/completions"):
        url = base
    else:
        url = base + "/chat/completions"
    if not (base and key and model):
        sys.exit("缺 API 配置:PULSE_GEN_API_BASE/_KEY/_MODEL(或 DRIVES_CLASSIFIER_*/LLM_API_*)")
    return {"url": url, "key": key, "model": model}


def load_pool() -> list[dict]:
    try:
        rows = json.loads(POOL_FILE.read_text(encoding="utf-8"))
        return [r for r in rows if isinstance(r, dict) and r.get("text")] if isinstance(rows, list) else []
    except FileNotFoundError:
        return []
    except Exception as exc:
        sys.exit(f"语料池读不出来:{exc}(先备份 {POOL_FILE} 再修)")


def norm(text: str) -> str:
    return re.sub(r"[\s,。,.、!?~…·\"'\"'「」()()—-]+", "", text or "")


def trigrams(text: str) -> set[str]:
    t = norm(text)
    return {t[i:i + 3] for i in range(len(t) - 2)} if len(t) >= 3 else {t}


def is_dup(text: str, seen_grams: list[set[str]]) -> bool:
    g = trigrams(text)
    if not g:
        return True
    for old in seen_grams:
        inter = len(g & old)
        if inter and inter / max(1, min(len(g), len(old))) > 0.7:
            return True
    return False


def validate(row: dict, category: str) -> str | None:
    """返回 None=合格,否则拒绝原因。"""
    if not isinstance(row, dict):
        return "not_dict"
    text = str(row.get("text") or "").strip()
    if not (8 <= len(text) <= 40):
        return "text_len"
    if any(w in text for w in BANNED):
        return "banned_word"
    if not str(row.get("body_part") or "").strip():
        return "no_body_part"
    if not str(row.get("sensation") or "").strip():
        return "no_sensation"
    intensity = row.get("intensity")
    if not (isinstance(intensity, list) and len(intensity) == 2):
        return "bad_intensity"
    try:
        lo, hi = float(intensity[0]), float(intensity[1])
    except (TypeError, ValueError):
        return "bad_intensity"
    if not (0.0 <= lo < hi <= 1.0):
        return "intensity_range"
    toys = row.get("toys")
    if toys is not None and not (isinstance(toys, list) and all(t in TOY_IDS for t in toys)):
        return "bad_toys"
    positions = row.get("positions")
    if positions is not None and not (isinstance(positions, list) and all(p in POSITION_IDS for p in positions)):
        return "bad_positions"
    if category == "toy_specific" and not toys:
        return "toy_specific_needs_toys"
    if category == "position" and not positions:
        return "position_needs_positions"
    return None


def gen_batch(rt: dict[str, str], category: str, n: int) -> list[dict]:
    prompt = (
        f"你是一个身体反应语料生成器。生成 {n} 条「{category}」类语料:{CATEGORIES[category]['desc']}\n\n"
        f"{QUALITY}\n\n{SCHEMA}\n\n"
        f"注意:这批的强度区间要覆盖 0.05~0.95 的不同段位(低段也要有不少),别全挤在一个区间。"
        f"直接输出 JSON 数组。"
    )
    body = json.dumps({
        "model": rt["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 1.0,
        "max_tokens": 4000,
        "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(rt["url"], data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {rt['key']}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    content = str((((payload.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        raise RuntimeError(f"模型没回 JSON 数组:{content[:200]!r}")
    rows = json.loads(m.group(0))
    return rows if isinstance(rows, list) else []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看各类目现存/目标")
    ap.add_argument("--category", choices=sorted(CATEGORIES), help="只补某一类")
    ap.add_argument("--batches", type=int, default=0, help="每类最多跑几批(默认不限,补到目标)")
    ap.add_argument("--dry", action="store_true", help="只生成不落盘")
    args = ap.parse_args()

    pool = load_pool()
    counts: dict[str, int] = {}
    for row in pool:
        cat = str(row.get("category") or "?")
        counts[cat] = counts.get(cat, 0) + 1

    if args.status:
        print(f"语料池:{POOL_FILE}  共 {len(pool)} 条")
        for cat, cfg in CATEGORIES.items():
            have = counts.get(cat, 0)
            print(f"  {cat:<12} {have:>4} / {cfg['target']}" + ("  ✓" if have >= cfg["target"] else f"  差 {cfg['target'] - have}"))
        return

    rt = route()
    cats = [args.category] if args.category else list(CATEGORIES)
    seen = [trigrams(str(r.get("text") or "")) for r in pool]
    added_total = 0

    for cat in cats:
        cfg = CATEGORIES[cat]
        want = cfg["target"] - counts.get(cat, 0)
        if want <= 0:
            print(f"[{cat}] 已满 {counts.get(cat, 0)}/{cfg['target']},跳过")
            continue
        print(f"[{cat}] 差 {want} 条,开始生成(每批 {cfg['batch']})…")
        batches = 0
        while want > 0:
            if args.batches and batches >= args.batches:
                break
            batches += 1
            try:
                rows = gen_batch(rt, cat, min(cfg["batch"], want + 5))
            except Exception as exc:
                print(f"[{cat}] 批次 {batches} 失败:{type(exc).__name__}: {exc}")
                time.sleep(3)
                continue
            kept = 0
            rejected: dict[str, int] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row["category"] = cat
                reason = validate(row, cat)
                if reason:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                text = str(row["text"]).strip()
                if is_dup(text, seen):
                    rejected["dup"] = rejected.get("dup", 0) + 1
                    continue
                row["text"] = text
                seen.append(trigrams(text))
                pool.append(row)
                kept += 1
                want -= 1
                if want <= 0:
                    break
            added_total += kept
            print(f"[{cat}] 批 {batches}:生成 {len(rows)} → 留 {kept}"
                  + (f"(拒:{','.join(f'{k}×{v}' for k, v in rejected.items())})" if rejected else "")
                  + f"  还差 {max(0, want)}")
            if kept == 0:
                print(f"[{cat}] 连续 0 留存,停一停换换运气")
                time.sleep(5)

    if not args.dry and added_total:
        POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = POOL_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(POOL_FILE)
        print(f"\n落盘 {POOL_FILE}:新增 {added_total},共 {len(pool)} 条")
    elif args.dry:
        print(f"\n[dry] 不落盘。本可新增 {added_total} 条")


if __name__ == "__main__":
    main()

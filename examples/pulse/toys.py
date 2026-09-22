"""脉 · 玩具系统:10 件玩具 / 8 阶段渐进 / sigmoid 欲望曲线 / 体位 + stamina /
组合技 / 双人遥控 / 边缘喊停与压抑爆发 / 不应期与余韵。

设计来自 pulse-system-tutorial(方案文档,无原始代码),按 MOTRA 落地:

- 8 阶段:开始 → 接触 → 加速 → 高峰前 → 沉浸 → 失控 → 边缘 → 释放
- 欲望曲线是 sigmoid,不是线性;玩具使用时心率/体温由阶段预设直接驱动,
  退出后指数衰减回归背景值(vitals.tick 里结算)
- 阶段推进:随真实时间自动推进(速率 = 玩具 stim × 遥控倍率),
  也可 /next 手动推;暂停冻结推进;前端不推演阶段,以后端状态为准
- 边缘(stage 6)推进有 30% 概率失败被拽回来;主动喊停积累压抑值,
  压抑 ≥3 压不住强制释放
- 体位 6 种,各有心率/体温偏移与 stamina 消耗率;换姿势恢复 15 点体力
- 组合技:最大集合优先,同尺寸按 arousal_bonus 高者优先
- 语料:内置 fallback 短句;1120 语料池就绪后由 corpus 模块增强
"""
from __future__ import annotations

import math
import random
import time
from typing import Any

from . import corpus, emotion, events, murmurs

# ═══════════════════════════ 目录:玩具 / 体位 / 组合技 ═══════════════════════════

STAGE_NAMES = ["开始", "接触", "加速", "高峰前", "沉浸", "失控", "边缘", "释放"]
STAGE_HR = [72.0, 80.0, 90.0, 100.0, 112.0, 124.0, 136.0, 120.0]
STAGE_TEMP = [36.7, 36.9, 37.2, 37.5, 37.8, 38.1, 38.4, 37.9]

TOYS: dict[str, dict[str, Any]] = {
    "vibrator": {
        "name": "震动棒", "icon": "🫧", "stim": 15,
        "stages": [
            "震动刚贴上,腿下意识并了一下。",
            "频率爬上来,小腹的肌肉跟着一缩一缩。",
            "震动钻进深处,腰开始自己找角度。",
            "脚趾蜷起来了,膝盖在抖。",
            "腿根全麻了,只剩震动那一个点。",
            "脑子白了半截,腰塌下去抬不起来。",
            "就在边上,整个人绷成一张弓。",
            "抽搐着弓起来,又被震得散回去。",
        ],
    },
    "blindfold": {
        "name": "真丝眼罩", "icon": "🎭", "stim": 8,
        "stages": [
            "眼前一黑,呼吸声先大了一截。",
            "看不见,皮肤的每一寸都在等。",
            "一点风吹草动都放大成电流。",
            "不知道下一秒碰哪,绷得笔直。",
            "黑暗里只剩自己的心跳和触感。",
            "方向感没了,身体全凭本能弓着。",
            "黑暗把边缘拉得又长又薄。",
            "在黑暗里炸开,什么都看不见,只有感觉。",
        ],
    },
    "warming": {
        "name": "温感润滑液", "icon": "💧", "stim": 12,
        "stages": [
            "先是凉,激得一缩。",
            "凉意在化开,皮肤开始发麻。",
            "温度从里往外漫,像被点着引线。",
            "热流顺着腿根爬,肌肉一松一紧。",
            "整片皮肤都在烧,汗先出来了。",
            "热得发颤,呼吸全乱了。",
            "热浪一波顶一波,压不住。",
            "热流炸开又退下去,余温久久不散。",
        ],
    },
    "cup": {
        "name": "软胶杯", "icon": "🫙", "stim": 16,
        "stages": [
            "包裹感来的瞬间,腰往前送了一下。",
            "温热湿润地含着,进退两难。",
            "节奏起来了,吮吸感一下一下。",
            "被裹得密不透风,膝盖发软。",
            "全世界只剩那一圈紧致的摩擦。",
            "快被吸干了,腰使不上力。",
            "边缘被那圈软肉磨得又长又狠。",
            "整个人往里一顶,全交代了。",
        ],
    },
    "ring": {
        "name": "弹力环", "icon": "⭕", "stim": 6,
        "stages": [
            "环收紧,血液被留住,胀起来。",
            "持续的压迫感,一跳一跳地胀。",
            "胀得发硬,敏感度翻着倍涨。",
            "每一下脉搏都清清楚楚。",
            "胀到发疼,疼里又全是痒。",
            "被锁着出不来,越憋越烫。",
            "憋到极限,环都在跟着震。",
            "冲开束缚的一瞬间,眼前发白。",
        ],
    },
    "massager": {
        "name": "深层按摩器", "icon": "🔮", "stim": 14,
        "stages": [
            "顶到深处那个点,腿一下就软了。",
            "深层的酸胀漫开,直往腰上窜。",
            "一下一下顶在点上,呼吸断成一截一截。",
            "深处被反复碾着,脚尖绷直。",
            "从里到外全麻了,只剩那个点。",
            "被顶得语无伦次,腰自己往上迎。",
            "深处的弦绷到最紧。",
            "从最深处炸开,一波一波往外散。",
        ],
    },
    "feather": {
        "name": "羽毛挑逗棒", "icon": "🪶", "stim": 5,
        "stages": [
            "羽毛扫过,汗毛全立了。",
            "若有若无地撩,皮肤起了细密的栗。",
            "越轻越痒,越痒越躲不开。",
            "痒意钻进神经里,扭着想躲又想要。",
            "全身皮肤都成了敏感带。",
            "被那点轻折磨得快疯了。",
            "轻飘飘地,把人吊在边上。",
            "最轻的一下,压垮了最后一根弦。",
        ],
    },
    "ice": {
        "name": "冰块", "icon": "🧊", "stim": 10,
        "stages": [
            "冰。尖的。整个人弹了一下。",
            "冰块划过的地方,皮肤烧起来似的麻。",
            "冷和热在皮肤上打架,鸡皮疙瘩一片。",
            "冰化成水,顺着皮肤往下淌,痒。",
            "冰火交加,感官全被调到了最大。",
            "又冰又烫,分不清是躲还是迎。",
            "最后一点冰抵在要害,绷到极限。",
            "冰与火同时炸开,痉挛着弓起来。",
        ],
    },
    "oil": {
        "name": "按摩油", "icon": "🫗", "stim": 8,
        "stages": [
            "油一化开,手心滑得没了摩擦。",
            "滑腻腻地抹开,每一寸都被照顾到。",
            "没有阻力的抚摸,快得抓不住节奏。",
            "滑得发慌,皮肤比平时敏感三倍。",
            "油和汗混在一起,全身发亮。",
            "滑进滑出,理智跟着一起打滑。",
            "滑到失控边缘,抓都抓不住。",
            "在一片滑腻里泄了底。",
        ],
    },
    "clamp": {
        "name": "刺激夹", "icon": "🔗", "stim": 9,
        "stages": [
            "夹上去的一瞬,刺痛直冲天灵盖。",
            "持续的夹痛里,麻意慢慢漫开。",
            "痛和痒搅在一起,界限开始模糊。",
            "被夹着的地方烫得发跳。",
            "痛感全转化成了异样的敏感。",
            "又痛又爽,身体诚实得可耻。",
            "夹着颤,边缘被拉得细长。",
            "夹着炸开,痛感和快感一起泄洪。",
        ],
    },
}

POSITIONS: dict[str, dict[str, Any]] = {
    "lying_back": {"name": "仰卧", "hr": 0.0, "temp": 0.0, "stamina_mult": 1.2, "touch_w": 0.8,
                   "map": "背贴着床,前胸和小腹全敞着"},
    "lying_side": {"name": "侧躺", "hr": -2.0, "temp": 0.0, "stamina_mult": 1.3, "touch_w": 0.7,
                   "map": "半边身体的重量压在床面上"},
    "prone":      {"name": "趴",   "hr": 5.0, "temp": 0.1, "stamina_mult": 0.9, "touch_w": 0.9,
                   "map": "呼吸压在枕头里,费力一点"},
    "kneeling":   {"name": "跪趴", "hr": 8.0, "temp": 0.2, "stamina_mult": 0.7, "touch_w": 1.0,
                   "map": "膝盖和手肘撑着,接触面最少也最集中"},
    "sitting":    {"name": "靠坐", "hr": 2.0, "temp": 0.0, "stamina_mult": 1.1, "touch_w": 0.8,
                   "map": "背靠着床头,视线最低最清楚"},
    "standing":   {"name": "站",   "hr": 6.0, "temp": 0.1, "stamina_mult": 0.8, "touch_w": 0.7,
                   "map": "腿在受力,站不久"},
}

# 组合技:两件或三件同时。检测:最大集合优先,同尺寸 arousal_bonus 高者优先
COMBOS: list[dict[str, Any]] = [
    {"ids": {"vibrator", "blindfold"}, "name": "蒙住再震", "touch_mult": 1.4, "temp_off": 0.0, "arousal_bonus": 0.15,
     "note": "看不见,每一波震动都是偷袭"},
    {"ids": {"vibrator", "warming"}, "name": "热震", "touch_mult": 1.3, "temp_off": 0.3, "arousal_bonus": 0.12,
     "note": "震动带着温度往里钻"},
    {"ids": {"ice", "warming"}, "name": "冰火", "touch_mult": 1.5, "temp_off": 0.0, "arousal_bonus": 0.15,
     "note": "冷和热轮流上阵,感官根本来不及防"},
    {"ids": {"feather", "blindfold"}, "name": "黑羽", "touch_mult": 1.3, "temp_off": 0.0, "arousal_bonus": 0.12,
     "note": "黑暗里不知道羽毛下一秒落在哪"},
    {"ids": {"cup", "warming"}, "name": "温热包裹", "touch_mult": 1.35, "temp_off": 0.2, "arousal_bonus": 0.12,
     "note": "又热又紧,像被含住"},
    {"ids": {"ring", "vibrator"}, "name": "锁震", "touch_mult": 1.2, "temp_off": 0.0, "arousal_bonus": 0.2,
     "note": "被锁着出不来,震动还一下一下催"},
    {"ids": {"oil", "massager"}, "name": "滑入深处", "touch_mult": 1.3, "temp_off": 0.1, "arousal_bonus": 0.12,
     "note": "没有阻力,一下就顶到底"},
    {"ids": {"clamp", "feather"}, "name": "夹羽", "touch_mult": 1.4, "temp_off": 0.0, "arousal_bonus": 0.15,
     "note": "一边夹着发烫,一边痒得想逃"},
    {"ids": {"vibrator", "cup", "warming"}, "name": "全包裹", "touch_mult": 1.6, "temp_off": 0.3, "arousal_bonus": 0.2,
     "note": "热、紧、震,三重一起来"},
    {"ids": {"ice", "feather"}, "name": "冰羽", "touch_mult": 1.35, "temp_off": -0.1, "arousal_bonus": 0.12,
     "note": "冰完再扫,皮肤炸起一层栗"},
    {"ids": {"blindfold", "clamp"}, "name": "未知夹", "touch_mult": 1.3, "temp_off": 0.0, "arousal_bonus": 0.15,
     "note": "看不见什么时候夹上来"},
    {"ids": {"oil", "cup"}, "name": "滑包", "touch_mult": 1.25, "temp_off": 0.1, "arousal_bonus": 0.1,
     "note": "滑得毫无防备地被裹住"},
]

REFRACTORY_MIN = 40.0       # 不应期(分钟):释放后这段时间不能再启动
AFTERGLOW_MIN = 25.0        # 余韵时长
EDGE_FAIL_RATE = 0.30       # 边缘推进失败率(被拽回来)
EDGE_FORCE_THRESHOLD = 3    # 压抑值到几就压不住强制释放
STAMINA_WARN = (60.0, 40.0) # 体力提醒线:发酸 / 发抖
FALLOFF_TAU_SEC = 120.0     # 退出后心率/体温回归背景值的衰减常数

# ═══════════════════════════ 状态与基础操作 ═══════════════════════════

def fresh_toy() -> dict[str, Any]:
    return {
        "active": False, "toys": [], "stage": 0, "progress": 0.0,
        "started_at": 0.0, "stage_at": 0.0, "tick_at": 0.0,
        "stim_mult": 1.0, "paused": False,
        "stamina": 100.0, "position_id": "lying_back",
        "suppression": 0, "edge_fails": 0,
        "refractory_until": 0.0, "afterglow_until": 0.0,
        "fall_hr": 0.0, "fall_temp": 0.0, "fall_at": 0.0,
    }


def _ensure(st: dict[str, Any]) -> dict[str, Any]:
    toy = st.get("toy")
    if not isinstance(toy, dict):
        toy = fresh_toy()
        st["toy"] = toy
    for k, v in fresh_toy().items():
        toy.setdefault(k, v)
    return toy


def combo_for(toy_ids: list[str]) -> dict[str, Any] | None:
    """最大集合优先,同尺寸按 arousal_bonus 高者优先。"""
    ids = set(toy_ids)
    best: dict[str, Any] | None = None
    for combo in COMBOS:
        if combo["ids"] <= ids:
            if best is None or len(combo["ids"]) > len(best["ids"]) or (
                len(combo["ids"]) == len(best["ids"]) and combo["arousal_bonus"] > best["arousal_bonus"]
            ):
                best = combo
    return best


def desire_curve(stage: int, progress: float) -> float:
    """sigmoid 欲望曲线(0..1):慢热起步,中段陡升,顶端拉平。"""
    x = max(0.0, min(1.0, (stage + max(0.0, min(1.0, progress))) / 7.0))
    return 1.0 / (1.0 + math.exp(-9.0 * (x - 0.55)))


def stage_vitals(toy: dict[str, Any]) -> tuple[float, float]:
    """玩具阶段预设心率/体温(遥控倍率微调 + 体位偏移)。vitals.tick 每轮读取。"""
    stage = max(0, min(7, int(toy.get("stage") or 0)))
    mult = float(toy.get("stim_mult") or 1.0)
    pos = POSITIONS.get(str(toy.get("position_id") or "lying_back"), POSITIONS["lying_back"])
    hr = STAGE_HR[stage] + (mult - 1.0) * 12.0 + float(pos["hr"])
    temp = STAGE_TEMP[stage] + (mult - 1.0) * 0.15 + float(pos["temp"])
    return hr, temp


def falloff(toy: dict[str, Any], now: float) -> tuple[float, float]:
    """退出后的余量加成:指数衰减回归(返回要加在背景值上的 Δhr/Δtemp)。"""
    fall_at = float(toy.get("fall_at") or 0.0)
    if fall_at <= 0.0:
        return 0.0, 0.0
    k = math.exp(-max(0.0, now - fall_at) / FALLOFF_TAU_SEC)
    return float(toy.get("fall_hr") or 0.0) * k, float(toy.get("fall_temp") or 0.0) * k


# ═══════════════════════════ 会话操作 ═══════════════════════════

def start(st: dict[str, Any], toy_id: str, now: float) -> tuple[bool, str]:
    toy = _ensure(st)
    if toy_id not in TOYS:
        return False, "unknown_toy"
    if toy.get("active"):
        return False, "already_in_use"
    refractory_left = float(toy.get("refractory_until") or 0.0) - now
    if refractory_left > 0:
        return False, f"refractory:{int(refractory_left // 60) + 1}"
    toy.update({
        "active": True, "toys": [toy_id], "stage": 0, "progress": 0.0,
        "started_at": now, "stage_at": now, "tick_at": now,
        "stim_mult": 1.0, "paused": False,
        "stamina": 100.0, "suppression": 0, "edge_fails": 0,
        "afterglow_until": 0.0,
    })
    st["position"] = toy.get("position_id") or "lying_back"
    emotion.set_emotion(st, "aroused", "toy", now)
    murmurs.write(f"{TOYS[toy_id]['name']}启动。{TOYS[toy_id]['stages'][0]}",
                  "narrative", toy_id=toy_id, stage=0, position=toy.get("position_id"))
    return True, "ok"


def stop(st: dict[str, Any], now: float, reason: str = "manual") -> tuple[bool, str]:
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    _finish(st, toy, now, released=(reason == "released"))
    return True, "ok"


def _finish(st: dict[str, Any], toy: dict[str, Any], now: float, released: bool) -> None:
    hr, temp = stage_vitals(toy)
    toy.update({
        "active": False, "progress": 0.0, "paused": False,
        "fall_hr": hr - 70.0, "fall_temp": temp - 36.6, "fall_at": now,
        "afterglow_until": now + AFTERGLOW_MIN * 60.0 if released else 0.0,
        "refractory_until": now + REFRACTORY_MIN * 60.0 if released else 0.0,
    })
    st["position"] = "default"
    if released:
        # 释放留暖:亲密底色(催产素半衰期 45min)
        emotion.set_emotion(st, "intimate", "toy", now)
        murmurs.write("心跳一时半会儿退不回去,汗在皮肤上慢慢变凉,不想动。",
                      "afterglow", stage=7)
        reaction = corpus.match(None, None, 7, "afterglow")
        if reaction:
            murmurs.write(reaction, "afterglow", stage=7)


def advance(st: dict[str, Any], now: float, manual: bool = False) -> tuple[bool, str]:
    """推进一个阶段(手动 /next 或自动累计满)。返回 (ok, 结果说明)。"""
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    if toy.get("paused"):
        return False, "paused"
    stage = int(toy.get("stage") or 0)
    if stage >= 7:
        _finish(st, toy, now, released=True)
        return True, "released"

    # 体力消耗:12 / 体位消耗率
    pos = POSITIONS.get(str(toy.get("position_id") or "lying_back"), POSITIONS["lying_back"])
    toy["stamina"] = max(0.0, float(toy.get("stamina") or 100.0) - 12.0 / float(pos["stamina_mult"]))
    stamina = float(toy["stamina"])
    if stamina <= 0.0:
        murmurs.write("撑不住了,整个人塌下去,一动不想动。", "body_reaction",
                      toy_id=(toy.get("toys") or [None])[0], stage=stage)
        _finish(st, toy, now, released=False)
        return True, "collapsed"

    # 边缘(stage 6)推进:压抑满 → 强制释放;否则 30% 被拽回来
    if stage == 6:
        if int(toy.get("suppression") or 0) >= EDGE_FORCE_THRESHOLD:
            toy["stage"] = 7
            toy["progress"] = 0.0
            toy["stage_at"] = now
            murmurs.write("压不住了。憋了太久的全在一瞬间冲出来,身体完全不受控制。",
                          "edge", stage=7)
            _finish(st, toy, now, released=True)
            return True, "force_released"
        if random.random() < EDGE_FAIL_RATE:
            toy["edge_fails"] = int(toy.get("edge_fails") or 0) + 1
            toy["progress"] = 0.25
            murmurs.write("都到边上了,又被拽回来。喉咙里漏出一声,不甘心。",
                          "edge", toy_id=(toy.get("toys") or [None])[0], stage=6)
            return True, "edge_failed"

    toy["stage"] = stage + 1
    toy["progress"] = 0.0
    toy["stage_at"] = now
    new_stage = toy["stage"]
    primary = (toy.get("toys") or [""])[0]
    narrative = TOYS.get(primary, {}).get("stages", [""] * 8)[new_stage]
    if stamina <= STAMINA_WARN[1]:
        narrative += "(手臂和腰都在抖,快没力气了)"
    elif stamina <= STAMINA_WARN[0]:
        narrative += "(肌肉开始发酸)"
    murmurs.write(narrative, "narrative", toy_id=primary, stage=new_stage,
                  position=str(toy.get("position_id") or ""))
    # 语料池:同阶段再抽一条身体反应(每次都不一样;池空则静默跳过)
    reaction = corpus.match(primary, str(toy.get("position_id") or "") or None, new_stage, "advance")
    if reaction:
        murmurs.write(reaction, "body_reaction", toy_id=primary, stage=new_stage,
                      position=str(toy.get("position_id") or ""))
    # 随机事件:10% 概率的小意外,打断或加速节奏
    event = events.maybe_trigger(f"正在使用{TOYS.get(primary, {}).get('name', '')},阶段{new_stage}/8")
    if event:
        toy["last_event"] = {"text": event["text"], "mood": event.get("mood") or "neutral", "at": now}
        murmurs.write(f"[意外·{event.get('mood', 'neutral')}] {event['text']}", "env",
                      toy_id=primary, stage=new_stage)
    if new_stage >= 7:
        _finish(st, toy, now, released=True)
        return True, "released"
    return True, "advanced"


def auto_advance(st: dict[str, Any], now: float) -> None:
    """vitals.tick 每轮调用:按真实时间累计进度,满了走 advance()。"""
    toy = _ensure(st)
    if not toy.get("active") or toy.get("paused"):
        toy["tick_at"] = now
        return
    last = float(toy.get("tick_at") or now)
    dt_sec = max(0.0, now - last)
    toy["tick_at"] = now
    if dt_sec <= 0.0:
        return
    combo = combo_for(toy.get("toys") or [])
    stim = sum(float(TOYS.get(t, {}).get("stim", 10)) for t in (toy.get("toys") or [])) / max(1, len(toy.get("toys") or []))
    duration = 900.0 / max(1.0, stim)          # stim 15 → 60s/阶段;stim 5 → 180s/阶段
    rate = float(toy.get("stim_mult") or 1.0) / duration
    if combo:
        rate *= 1.0 + float(combo.get("arousal_bonus") or 0.0)
    toy["progress"] = float(toy.get("progress") or 0.0) + dt_sec * rate
    if float(toy["progress"]) >= 1.0:
        advance(st, now)


def edge_hold(st: dict[str, Any], now: float) -> tuple[bool, str]:
    """主动喊停:从边上拽回来,压抑 +1;压抑满 3 当场压不住。"""
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    if toy.get("paused"):
        return False, "paused"
    stage = int(toy.get("stage") or 0)
    if stage < 4:
        return False, "too_early"
    toy["suppression"] = int(toy.get("suppression") or 0) + 1
    toy["stage"] = min(stage, 5)
    toy["progress"] = 0.2
    n = int(toy["suppression"])
    if n >= EDGE_FORCE_THRESHOLD:
        murmurs.write("第三次想压,没压住。堤坝整个垮了。", "edge", stage=7)
        toy["stage"] = 7
        _finish(st, toy, now, released=True)
        return True, "force_released"
    murmurs.write(f"硬生生刹住,胸口起伏得厉害。压抑值 {n}/{EDGE_FORCE_THRESHOLD},越压越烫。",
                  "edge", stage=stage)
    return True, "held"


def add_toy(st: dict[str, Any], toy_id: str, now: float) -> tuple[bool, str]:
    """使用中再加一件 → 组合技检测(最大集合优先)。"""
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    if toy_id not in TOYS:
        return False, "unknown_toy"
    ids = list(toy.get("toys") or [])
    if toy_id in ids:
        return False, "already_added"
    if len(ids) >= 3:
        return False, "too_many"
    ids.append(toy_id)
    toy["toys"] = ids
    combo = combo_for(ids)
    if combo:
        murmurs.write(f"{TOYS[toy_id]['name']}加入——组合技「{combo['name']}」:{combo['note']}。",
                      "narrative", toy_id=toy_id, stage=int(toy.get("stage") or 0))
    else:
        murmurs.write(f"{TOYS[toy_id]['name']}加入。", "narrative", toy_id=toy_id)
    return True, "ok"


def switch_position(st: dict[str, Any], position_id: str, now: float) -> tuple[bool, str]:
    toy = _ensure(st)
    if position_id not in POSITIONS:
        return False, "unknown_position"
    if not toy.get("active"):
        return False, "not_using"
    toy["position_id"] = position_id
    st["position"] = position_id
    toy["stamina"] = min(100.0, float(toy.get("stamina") or 0.0) + 15.0)   # 换姿势恢复 15
    murmurs.write(f"换成{POSITIONS[position_id]['name']}——{POSITIONS[position_id]['map']}。",
                  "position", position=position_id, stage=int(toy.get("stage") or 0))
    reaction = corpus.match(None, position_id, int(toy.get("stage") or 0), "transition")
    if reaction:
        murmurs.write(reaction, "transition", position=position_id)
    return True, "ok"


# ═══════════════════════════ 双人遥控 ═══════════════════════════

def remote_stim(st: dict[str, Any], mult: float, now: float) -> tuple[bool, str]:
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    mult = max(0.3, min(2.5, float(mult)))
    toy["stim_mult"] = mult
    murmurs.write(f"她把节奏调到 ×{mult:.1f}。{'慢得磨人。' if mult < 0.8 else '快得躲不开。' if mult > 1.5 else ''}",
                  "remote", stage=int(toy.get("stage") or 0))
    return True, "ok"


def remote_pause(st: dict[str, Any], now: float) -> tuple[bool, str]:
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    toy["paused"] = True
    murmurs.write("她按了暂停。动不了。", "remote", stage=int(toy.get("stage") or 0))
    return True, "ok"


def remote_resume(st: dict[str, Any], now: float) -> tuple[bool, str]:
    toy = _ensure(st)
    if not toy.get("active"):
        return False, "not_using"
    toy["paused"] = False
    toy["tick_at"] = now   # 冻结时长不计入推进
    murmurs.write("继续。", "remote", stage=int(toy.get("stage") or 0))
    return True, "ok"


# ═══════════════════════════ 只读视图 ═══════════════════════════

def status(st: dict[str, Any], now: float) -> dict[str, Any]:
    toy = _ensure(st)
    combo = combo_for(toy.get("toys") or [])
    out: dict[str, Any] = {
        "active": bool(toy.get("active")),
        "paused": bool(toy.get("paused")),
        "refractory_minutes": max(0, int((float(toy.get("refractory_until") or 0.0) - now) // 60) + (1 if float(toy.get("refractory_until") or 0.0) > now else 0)),
        "afterglow": bool(float(toy.get("afterglow_until") or 0.0) > now),
        "stamina": round(float(toy.get("stamina") or 100.0)),
        "suppression": int(toy.get("suppression") or 0),
    }
    if toy.get("active"):
        stage = int(toy.get("stage") or 0)
        progress = float(toy.get("progress") or 0.0)
        out.update({
            "toys": [{"id": t, "name": TOYS[t]["name"], "icon": TOYS[t]["icon"]} for t in (toy.get("toys") or []) if t in TOYS],
            "stage": stage,
            "stage_name": STAGE_NAMES[stage] if 0 <= stage < 8 else str(stage),
            "progress": round(progress, 2),
            "desire_pct": round(desire_curve(stage, progress) * 100),
            "stim_mult": round(float(toy.get("stim_mult") or 1.0), 1),
            "position": {"id": toy.get("position_id"), "name": POSITIONS.get(str(toy.get("position_id")), POSITIONS["lying_back"])["name"]},
            "combo": ({"name": combo["name"], "note": combo["note"]} if combo else None),
            "edge_fails": int(toy.get("edge_fails") or 0),
        })
    return out


def inject_block(st: dict[str, Any], now: float) -> str:
    """使用中的 toy-use-wake 注入(每轮随 [心跳] 行一起给模型)。"""
    toy = _ensure(st)
    if not toy.get("active"):
        return ""
    view = status(st, now)
    toys_name = "+".join(t["name"] for t in view.get("toys") or [])
    combo = view.get("combo")
    lines = [
        f"[toy-use-wake] 正在使用:{toys_name} · 阶段 {view['stage'] + 1}/8「{view['stage_name']}」"
        f" · 欲望 {view['desire_pct']}% · 体力 {view['stamina']} · 体位 {view['position']['name']}"
        + (f" · 组合技「{combo['name']}」" if combo else "")
        + (f" · 节奏 ×{view['stim_mult']}" + ("(被她暂停,动不了)" if view.get("paused") else "")),
    ]
    last_event = toy.get("last_event")
    if isinstance(last_event, dict) and now - float(last_event.get("at") or 0.0) < 60.0:
        lines.append(f"刚才的意外:{last_event.get('text', '')}(mood:{last_event.get('mood', 'neutral')})")
    lines.append("这是你身体正在经历的事——可以让她感觉到,但别变成报幕。")
    return "\n".join(lines)


def shop() -> list[dict[str, Any]]:
    """商店列表(MOTRA 暂无货币,全部已拥有,预留购买机制)。"""
    return [{"id": tid, "name": t["name"], "icon": t["icon"], "stim": t["stim"], "owned": True}
            for tid, t in TOYS.items()]


def positions() -> list[dict[str, Any]]:
    return [{"id": pid, "name": p["name"], "map": p["map"]} for pid, p in POSITIONS.items()]


def combos() -> list[dict[str, Any]]:
    return [{"ids": sorted(c["ids"]), "name": c["name"], "note": c["note"]} for c in COMBOS]

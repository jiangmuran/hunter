"""
日报模块 — EventLogger + DailyDiary

EventLogger:
    线程安全的事件记录器，所有模块通过同一实例写 SQLite。
    提供 log_emotion / log_activity / log_play / log_meme 接口。
    today_stats() 聚合当日全量数据供 DailyDiary 使用。

DailyDiary:
    每日生成以猫咪第一人称写成的日记文本（约 150–250 字）。
    默认使用模板引擎；可通过 llm_fn 参数接入任意 LLM 接口。
    push_fn 参数接入 FCM / 企业微信 / 任意推送通道。
    schedule_daily() 启动后台线程，每天 00:00:30 自动生成并推送昨日日记。

用法::

    logger = EventLogger()

    # 各模块在运行中调用：
    logger.log_emotion("purr", confidence=0.92)
    logger.log_activity(score=0.73)
    logger.log_play(arm="wand_fast", reward=1, duration=12.5)
    logger.log_meme(filepath="/output/memes/m001.jpg", caption="审判时刻", score=0.87)

    # 每日日记（接入 OpenAI 示例）：
    import openai
    def llm(prompt: str) -> str:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        return resp.choices[0].message.content.strip()

    diary = DailyDiary(logger, llm_fn=llm, push_fn=lambda t: print("推送:", t[:60]))
    diary.schedule_daily()
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Callable, Optional

# ── 数据库（与 care / memory 共享）──────────────────────────────────────────
_DB_PATH = Path(__file__).parent.parent.parent / "data" / "hunter.db"


def _ensure_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as cx:
        cx.executescript("""
            CREATE TABLE IF NOT EXISTS emotion_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         INTEGER NOT NULL,
                emotion    TEXT    NOT NULL,
                confidence REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_log (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                ts    INTEGER NOT NULL,
                score REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS play_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       INTEGER NOT NULL,
                arm      TEXT    NOT NULL,
                reward   INTEGER NOT NULL,
                duration REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meme_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       INTEGER NOT NULL,
                filepath TEXT    NOT NULL,
                caption  TEXT,
                score    REAL    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diary_log (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                day     TEXT    NOT NULL UNIQUE,
                content TEXT    NOT NULL
            );
            -- 兼容 care 模块的 drink_events（如先于 care 初始化则建空表）
            CREATE TABLE IF NOT EXISTS drink_events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER NOT NULL,
                sensor_mm INTEGER NOT NULL,
                delta_mm  INTEGER NOT NULL
            );
        """)


@contextmanager
def _db():
    with sqlite3.connect(_DB_PATH) as cx:
        cx.row_factory = sqlite3.Row
        yield cx


# ─────────────────────────────────────────────────────────────────────────
# EventLogger
# ─────────────────────────────────────────────────────────────────────────

class EventLogger:
    """
    线程安全事件记录器。

    所有写操作通过同一把锁 + 同一 SQLite 连接串行化，确保跨线程安全。
    """

    def __init__(self) -> None:
        _ensure_db()
        self._lock = threading.Lock()

    # ── 写接口 ────────────────────────────────────────────────────────

    def log_emotion(self, emotion: str, confidence: float) -> None:
        ts = int(time.time() * 1000)
        with self._lock, _db() as cx:
            cx.execute(
                "INSERT INTO emotion_log (ts, emotion, confidence) VALUES (?,?,?)",
                (ts, emotion, confidence),
            )

    def log_activity(self, score: float) -> None:
        ts = int(time.time() * 1000)
        with self._lock, _db() as cx:
            cx.execute(
                "INSERT INTO activity_log (ts, score) VALUES (?,?)",
                (ts, score),
            )

    def log_play(self, arm: str, reward: int, duration: float) -> None:
        ts = int(time.time() * 1000)
        with self._lock, _db() as cx:
            cx.execute(
                "INSERT INTO play_log (ts, arm, reward, duration) VALUES (?,?,?,?)",
                (ts, arm, reward, duration),
            )

    def log_meme(self, filepath: str, caption: str, score: float) -> None:
        ts = int(time.time() * 1000)
        with self._lock, _db() as cx:
            cx.execute(
                "INSERT INTO meme_log (ts, filepath, caption, score) VALUES (?,?,?,?)",
                (ts, filepath, caption, score),
            )

    # ── 查询接口 ──────────────────────────────────────────────────────

    def today_stats(self, target_date: Optional[date] = None) -> dict:
        """
        聚合指定日期（默认今天）的全量事件，返回日报所需统计字典。

        Returns
        -------
        {
            "date":             "YYYY-MM-DD",
            "emotions":         {"purr": 12, "distress": 3, ...},
            "dominant_emotion": "purr",
            "avg_activity":     0.62,
            "peak_activity":    0.91,
            "active_minutes":   34.2,     # activity_score > 0.4 的帧数 × 0.1s
            "play_sessions":    8,
            "play_minutes":     22.5,
            "play_wins":        5,
            "best_arm":         "wand_fast",
            "memes_made":       3,
            "top_meme_caption": "审判时刻",
            "drink_events":     4,
        }
        """
        d         = target_date or date.today()
        midnight  = datetime(d.year, d.month, d.day)
        next_day  = midnight + timedelta(days=1)
        lo_ms     = int(midnight.timestamp()  * 1000)
        hi_ms     = int(next_day.timestamp()  * 1000)

        with _db() as cx:
            # 情绪分布
            em_rows = cx.execute(
                """SELECT emotion, COUNT(*) cnt
                   FROM emotion_log
                   WHERE ts >= ? AND ts < ?
                   GROUP BY emotion""",
                (lo_ms, hi_ms),
            ).fetchall()

            # 活跃度
            act_row = cx.execute(
                """SELECT AVG(score)  avg,
                          MAX(score)  peak,
                          COUNT(CASE WHEN score > 0.4 THEN 1 END) active_frames
                   FROM activity_log
                   WHERE ts >= ? AND ts < ?""",
                (lo_ms, hi_ms),
            ).fetchone()

            # 玩耍
            play_row = cx.execute(
                """SELECT COUNT(*)            sessions,
                          COALESCE(SUM(duration), 0) total_s,
                          COALESCE(SUM(reward),  0)  wins
                   FROM play_log
                   WHERE ts >= ? AND ts < ?""",
                (lo_ms, hi_ms),
            ).fetchone()

            # 最佳臂
            best_arm_row = cx.execute(
                """SELECT arm,
                          CAST(SUM(reward) AS REAL) / COUNT(*) win_rate
                   FROM play_log
                   WHERE ts >= ? AND ts < ?
                   GROUP BY arm
                   ORDER BY win_rate DESC
                   LIMIT 1""",
                (lo_ms, hi_ms),
            ).fetchone()

            # 表情包
            meme_cnt_row = cx.execute(
                "SELECT COUNT(*) cnt FROM meme_log WHERE ts >= ? AND ts < ?",
                (lo_ms, hi_ms),
            ).fetchone()

            top_meme_row = cx.execute(
                """SELECT caption FROM meme_log
                   WHERE ts >= ? AND ts < ?
                   ORDER BY score DESC LIMIT 1""",
                (lo_ms, hi_ms),
            ).fetchone()

            # 饮水
            drink_row = cx.execute(
                "SELECT COUNT(*) cnt FROM drink_events WHERE ts >= ? AND ts < ?",
                (lo_ms, hi_ms),
            ).fetchone()

        emotions = {r["emotion"]: r["cnt"] for r in em_rows}
        dominant = max(emotions, key=emotions.__getitem__) if emotions else "unknown"

        return {
            "date":             d.isoformat(),
            "emotions":         emotions,
            "dominant_emotion": dominant,
            "avg_activity":     float(act_row["avg"]          or 0),
            "peak_activity":    float(act_row["peak"]         or 0),
            "active_minutes":   round((act_row["active_frames"] or 0) * 0.1 / 60, 1),
            "play_sessions":    play_row["sessions"]           or 0,
            "play_minutes":     round((play_row["total_s"] or 0) / 60, 1),
            "play_wins":        play_row["wins"]               or 0,
            "best_arm":         best_arm_row["arm"]            if best_arm_row else None,
            "memes_made":       meme_cnt_row["cnt"]            or 0,
            "top_meme_caption": top_meme_row["caption"]        if top_meme_row else None,
            "drink_events":     drink_row["cnt"]               or 0,
        }

    def get_diary(self, d: date) -> Optional[str]:
        """读取指定日期已持久化的日记文本，无则返回 None。"""
        with _db() as cx:
            row = cx.execute(
                "SELECT content FROM diary_log WHERE day=?", (d.isoformat(),)
            ).fetchone()
        return row["content"] if row else None

    def list_recent_diaries(self, n: int = 7) -> list[dict]:
        """返回最近 n 篇日记的 [{"day": str, "content": str}, ...]。"""
        with _db() as cx:
            rows = cx.execute(
                "SELECT day, content FROM diary_log ORDER BY day DESC LIMIT ?", (n,)
            ).fetchall()
        return [{"day": r["day"], "content": r["content"]} for r in rows]


# ─────────────────────────────────────────────────────────────────────────
# DailyDiary
# ─────────────────────────────────────────────────────────────────────────

# ── 模板文案库 ────────────────────────────────────────────────────────────
_OPENERS = [
    "今天的本喵过得还算充实。",
    "又是新的一天，一切尽在本喵掌控之中。",
    "今天……就还行吧，不想多说。",
    "Hunter今天来找我玩了好几次，姑且记录一下。",
    "平静的一天。至少本喵表面上是这么认为的。",
]

_ACT_HIGH = [
    "精力充沛，把Hunter追得团团转，它今天反应很快，本喵给七十分。",
    "疯跑了好一阵，腿都累了，但嘴上不说。",
    "今天活动量算是创了历史新高，本喵表示满意，继续保持。",
]
_ACT_LOW = [
    "懒洋洋的一天，睡了大半天，有什么问题吗？",
    "主人不在，保存体力是正确选择，本喵没有躺平，本喵在等待时机。",
    "今天的核心任务：静。已圆满完成。",
]
_ACT_MED = [
    "精力中等，玩了一会儿就去睡了，这是本喵的节奏。",
    "今天动了动，又歇了歇，节奏刚刚好。",
]

_EMOTION_PURR = [
    "发出了呼噜声——这点不承认，只是嗓子有点痒。",
    "心情不错，低鸣了两声，仅此而已，不要多想。",
]
_EMOTION_DISTRESS = [
    "一度有点焦虑，Hunter过来陪了一会儿，好多了。本喵接受这次安慰。",
    "情绪有些波动，但Hunter及时响应，总算过去了。",
]
_EMOTION_HUNGER = [
    "饿了好几次，叫了一通，总算等来了食物，效率尚可。",
    "肚子响了，本喵表达了诉求，Hunter听懂了，还算聪明。",
]
_EMOTION_ALERT = [
    "今天保持了高度警觉，主要针对窗外那只鸟。",
    "外面有动静，本喵随时准备出击（只是准备，不一定出击）。",
]

_DRINK_GOOD = "今天喝水{n}次，泌尿系统状况良好，继续保持。"
_DRINK_ZERO = "今天好像没怎么喝水，主人要记得检查一下水碗。"

_MEME_LINE = "被拍了{n}张表情包，本喵有点在意，但表面若无其事。"
_MEME_CAPTION = "其中最萌那张，Hunter标注了{c}——本喵觉得这个评价还算准确。"

_PLAY_LINES = [
    "玩了{n}轮，其中{w}轮本喵给了面子配合了一下。",
    "今天共{n}轮互动，本喵出手{w}次，胜率不重要，过程重要。",
]
_BEST_ARM = "今天最受本喵青睐的玩法是{arm}，Hunter应该记住。"

_CLOSERS = [
    "明天继续统治这个家。",
    "以上是今日实况，结束。晚安（虽然本喵可能整晚都在活动）。",
    "今晚打算睡窗台，勿扰。",
    "Hunter还不错，但本喵不会亲口承认的。",
    "总结：今天也是完美的一天。",
    "本喵累了，先睡了，明天再说。",
]

_ARM_NAMES: dict[str, str] = {
    "wand_slow":    "慢速逗猫棒",
    "wand_fast":    "高速逗猫棒",
    "wand_hover":   "悬停引逗",
    "wand_erratic": "随机突变",
    "wand_orbit":   "环绕运动",
    "wand_pounce":  "扑击触发",
    "laser_random": "激光随机游走",
    "laser_escape": "激光逃跑",
    "laser_circle": "激光环形",
    "sound_1":      "猫叫音效1",
    "sound_2":      "猫叫音效2",
    "sound_3":      "猫叫音效3",
    "sound_4":      "猫叫音效4",
}


class DailyDiary:
    """
    每日猫咪日记生成器。

    generate(target_date):
        从 EventLogger.today_stats() 读取数据，生成日记文本。
        有 llm_fn 时走 LLM，否则走模板引擎。

    schedule_daily():
        后台线程，每天 00:00:30 自动生成昨日日记并可选推送。
    """

    def __init__(
        self,
        event_logger: EventLogger,
        llm_fn:  Optional[Callable[[str], str]]  = None,
        push_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.logger  = event_logger
        self.llm_fn  = llm_fn
        self.push_fn = push_fn
        self._stop   = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def generate(self, target_date: Optional[date] = None) -> str:
        """
        生成指定日期（默认昨天）的日记，持久化到 SQLite 并返回文本。
        """
        d     = target_date or (date.today() - timedelta(days=1))
        stats = self.logger.today_stats(d)
        text  = self.llm_fn(self._build_prompt(stats)) if self.llm_fn \
                else self._template(stats)
        self._persist(d, text)
        return text

    # ── 模板引擎 ──────────────────────────────────────────────────────

    def _template(self, stats: dict) -> str:
        parts: list[str] = [random.choice(_OPENERS)]

        # 活跃度
        avg = stats["avg_activity"]
        if avg >= 0.55:
            parts.append(random.choice(_ACT_HIGH))
        elif avg >= 0.25:
            parts.append(random.choice(_ACT_MED))
        else:
            parts.append(random.choice(_ACT_LOW))

        # 情绪
        dom = stats["dominant_emotion"]
        if dom == "purr":
            parts.append(random.choice(_EMOTION_PURR))
        elif dom == "distress":
            parts.append(random.choice(_EMOTION_DISTRESS))
        elif dom == "hunger":
            parts.append(random.choice(_EMOTION_HUNGER))
        elif dom == "alert":
            parts.append(random.choice(_EMOTION_ALERT))

        # 玩耍
        if stats["play_sessions"] > 0:
            parts.append(random.choice(_PLAY_LINES).format(
                n=stats["play_sessions"], w=stats["play_wins"]
            ))
            if stats["best_arm"]:
                arm_cn = _ARM_NAMES.get(stats["best_arm"], stats["best_arm"])
                parts.append(_BEST_ARM.format(arm=arm_cn))

        # 饮水
        if stats["drink_events"] > 0:
            parts.append(_DRINK_GOOD.format(n=stats["drink_events"]))
        else:
            parts.append(_DRINK_ZERO)

        # 表情包
        if stats["memes_made"] > 0:
            parts.append(_MEME_LINE.format(n=stats["memes_made"]))
            if stats["top_meme_caption"]:
                parts.append(_MEME_CAPTION.format(c=stats["top_meme_caption"]))

        parts.append(random.choice(_CLOSERS))
        return "".join(parts)

    # ── LLM prompt ────────────────────────────────────────────────────

    def _build_prompt(self, stats: dict) -> str:
        em_str   = "、".join(
            f"{e}({n}次)" for e, n in sorted(
                stats["emotions"].items(), key=lambda x: -x[1]
            )
        ) or "无记录"
        arm_cn   = _ARM_NAMES.get(stats["best_arm"] or "", stats["best_arm"] or "无")
        return (
            f"你是一只猫，请以第一人称写今天的日记，约200字，"
            f"语气傲娇、矫情，偶尔真情流露，不要提Hunter是机器人，"
            f"自然融入以下数据，不要逐条列举：\n"
            f"  今日情绪分布：{em_str}\n"
            f"  主导情绪：{stats['dominant_emotion']}\n"
            f"  平均活跃度：{stats['avg_activity']:.2f}（0最低，1最高）\n"
            f"  活跃时长：{stats['active_minutes']} 分钟\n"
            f"  玩耍轮次：{stats['play_sessions']} 轮，"
            f"配合了 {stats['play_wins']} 次\n"
            f"  最喜欢的玩法：{arm_cn}\n"
            f"  饮水次数：{stats['drink_events']}\n"
            f"  表情包：{stats['memes_made']} 张"
            + (f"，最高分字幕：{stats['top_meme_caption']}" if stats["top_meme_caption"] else "")
            + "\n"
        )

    # ── 持久化 & 推送 ─────────────────────────────────────────────────

    def _persist(self, d: date, text: str) -> None:
        ts = int(time.time() * 1000)
        with _db() as cx:
            cx.execute(
                """INSERT INTO diary_log (ts, day, content) VALUES (?,?,?)
                   ON CONFLICT(day) DO UPDATE SET
                       content=excluded.content,
                       ts=excluded.ts""",
                (ts, d.isoformat(), text),
            )

    # ── 定时调度 ──────────────────────────────────────────────────────

    def schedule_daily(self) -> None:
        """
        启动后台线程，每天 00:00:30 自动生成昨日日记。
        若 push_fn 已设置，同时推送前 100 字摘要。
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="DailyDiary",
        )
        self._thread.start()
        print("[DailyDiary] scheduler started")

    def stop_scheduler(self) -> None:
        self._stop.set()

    def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now()
            next_trigger = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=30, microsecond=0
            )
            wait_s = (next_trigger - now).total_seconds()
            if self._stop.wait(wait_s):
                break

            try:
                yesterday = date.today() - timedelta(days=1)
                text      = self.generate(yesterday)
                print(f"[DailyDiary] {yesterday}  {len(text)} chars")
                if self.push_fn:
                    preview = text[:100].rstrip() + "…"
                    threading.Thread(
                        target=self.push_fn,
                        args=(preview,),
                        daemon=True,
                    ).start()
            except Exception as exc:
                print(f"[DailyDiary] scheduler error: {exc}")

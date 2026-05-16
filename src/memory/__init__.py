"""
记忆模块 — MemoryBox (Beta-Bandit 个性偏好学习)

每个行为臂维护 Beta(α, β) 分布：
    α = 成功次数 + 1（均匀先验）
    β = 失败次数 + 1
    E[reward] = α / (α + β)

Thompson Sampling：
    从每个臂的 Beta 分布采一个随机样本，选取最大值的臂。
    天然平衡 exploit（高期望臂）与 explore（高不确定臂）。

持久化：
    所有臂状态写入 SQLite data/hunter.db，服务重启后自动恢复。
    每次 update() 同时追加一条 bandit_history 记录供日报查询。

ACTION_ARMS 对应 hunt 模块的动作库；可随 hunt 模块扩展同步添加。

用法::

    box = MemoryBox()

    # 主循环选臂
    arm = box.sample()          # e.g. "wand_fast"

    # 执行动作后给奖励
    box.update(arm, reward=1)   # 1 = 猫咪参与，0 = 无反应

    # 查询偏好
    for arm, er in box.top_preferences(3):
        print(f"{arm}: {er:.1%}")

    # 每周统计
    summary = box.weekly_summary()
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 数据库（与 care / report 共享）──────────────────────────────────────────
_DB_PATH = Path(__file__).parent.parent.parent / "data" / "hunter.db"

# ── 行为臂：与 hunt 模块动作库对齐 ──────────────────────────────────────────
ACTION_ARMS: list[str] = [
    # 逗猫棒模式
    "wand_slow",       # 慢速扫掠，适合懒猫 / 老猫
    "wand_fast",       # 高速抖动，适合精力旺盛期
    "wand_hover",      # 定点悬停 + 小幅抖，引诱潜伏
    "wand_erratic",    # 随机方向突变，最高不可预测性
    "wand_orbit",      # 绕猫位置圆周运动
    "wand_pounce",     # 突然下落触发扑击本能
    # 激光模式
    "laser_random",    # 随机游走
    "laser_escape",    # 快速逃跑路径，引发追逐
    "laser_circle",    # 环形轨迹
    # 猫叫音效
    "sound_1",         # 猫叫音效 1
    "sound_2",         # 猫叫音效 2
    "sound_3",         # 猫叫音效 3
    "sound_4",         # 猫叫音效 4
]


def _ensure_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_DB_PATH) as cx:
        cx.executescript("""
            CREATE TABLE IF NOT EXISTS bandit_state (
                arm          TEXT PRIMARY KEY,
                alpha        REAL    NOT NULL DEFAULT 1.0,
                beta         REAL    NOT NULL DEFAULT 1.0,
                total_pulls  INTEGER NOT NULL DEFAULT 0,
                total_wins   INTEGER NOT NULL DEFAULT 0,
                updated_at   INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS bandit_history (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                arm     TEXT    NOT NULL,
                reward  INTEGER NOT NULL   -- 0 or 1
            );
        """)


@contextmanager
def _db():
    with sqlite3.connect(_DB_PATH) as cx:
        cx.row_factory = sqlite3.Row
        yield cx


class MemoryBox:
    """
    Beta-Bandit 猫咪个性偏好学习系统。

    线程安全：_lock 保护内存状态，每次 update 同步写 SQLite。
    """

    def __init__(self) -> None:
        _ensure_db()
        self._lock  = threading.Lock()
        self._state: dict[str, dict] = {}
        self._load()

    # ── 持久化 ────────────────────────────────────────────────────────

    def _load(self) -> None:
        with _db() as cx:
            rows = cx.execute("SELECT * FROM bandit_state").fetchall()
        loaded = {r["arm"]: dict(r) for r in rows}

        for arm in ACTION_ARMS:
            if arm in loaded:
                self._state[arm] = loaded[arm]
            else:
                self._state[arm] = {
                    "arm": arm, "alpha": 1.0, "beta": 1.0,
                    "total_pulls": 0, "total_wins": 0, "updated_at": 0,
                }
                self._upsert_arm(arm)

    def _upsert_arm(self, arm: str) -> None:
        s  = self._state[arm]
        ts = int(time.time() * 1000)
        with _db() as cx:
            cx.execute("""
                INSERT INTO bandit_state
                    (arm, alpha, beta, total_pulls, total_wins, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(arm) DO UPDATE SET
                    alpha=excluded.alpha,
                    beta=excluded.beta,
                    total_pulls=excluded.total_pulls,
                    total_wins=excluded.total_wins,
                    updated_at=excluded.updated_at
            """, (arm, s["alpha"], s["beta"],
                  s["total_pulls"], s["total_wins"], ts))

    # ── 核心接口 ──────────────────────────────────────────────────────

    def sample(self) -> str:
        """
        Thompson Sampling：从每个臂的 Beta 分布采样，返回最大值臂名称。
        线程安全（只读 _state，无写操作）。
        """
        with self._lock:
            scores = {
                arm: random.betavariate(s["alpha"], s["beta"])
                for arm, s in self._state.items()
            }
        return max(scores, key=scores.__getitem__)

    def update(self, arm: str, reward: int) -> None:
        """
        贝叶斯更新，同时追加历史记录。

        Parameters
        ----------
        arm    : ACTION_ARMS 中的臂名称
        reward : 1 = 猫咪参与（扑/追/注视），0 = 无反应 / 走开
        """
        if arm not in self._state:
            raise ValueError(f"[MemoryBox] unknown arm: {arm!r}")
        if reward not in (0, 1):
            raise ValueError("[MemoryBox] reward must be 0 or 1")

        with self._lock:
            s = self._state[arm]
            if reward == 1:
                s["alpha"]      += 1.0
                s["total_wins"] += 1
            else:
                s["beta"] += 1.0
            s["total_pulls"] += 1
            self._upsert_arm(arm)

        ts = int(time.time() * 1000)
        with _db() as cx:
            cx.execute(
                "INSERT INTO bandit_history (ts, arm, reward) VALUES (?,?,?)",
                (ts, arm, reward),
            )

        s = self._state[arm]
        er = s["alpha"] / (s["alpha"] + s["beta"])
        print(f"[MemoryBox] {arm:<16}  α={s['alpha']:.1f}  β={s['beta']:.1f}"
              f"  E[r]={er:.1%}  pulls={s['total_pulls']}")

    # ── 查询接口 ──────────────────────────────────────────────────────

    def expected_reward(self, arm: str) -> float:
        """返回臂的期望奖励 α/(α+β)。"""
        if arm not in self._state:
            raise ValueError(f"[MemoryBox] unknown arm: {arm!r}")
        s = self._state[arm]
        return s["alpha"] / (s["alpha"] + s["beta"])

    def top_preferences(self, n: int = 3) -> list[tuple[str, float]]:
        """
        返回期望奖励最高的 n 个臂。

        Returns
        -------
        list of (arm_name, expected_reward)，降序排列
        """
        with self._lock:
            ranked = sorted(
                self._state.items(),
                key=lambda kv: kv[1]["alpha"] / (kv[1]["alpha"] + kv[1]["beta"]),
                reverse=True,
            )
        return [
            (arm, s["alpha"] / (s["alpha"] + s["beta"]))
            for arm, s in ranked[:n]
        ]

    def weekly_summary(self) -> dict:
        """
        返回最近 7 天的按臂聚合统计，以及当前 top-3 偏好。

        Returns
        -------
        {
            "top_preferences": [(arm, er), ...],
            "arm_stats": [
                {"arm": str, "pulls": int, "wins": int, "win_rate": float}, ...
            ]
        }
        """
        week_ago_ms = int((time.time() - 7 * 86400) * 1000)
        with _db() as cx:
            rows = cx.execute(
                """SELECT arm,
                          COUNT(*)     AS pulls,
                          SUM(reward)  AS wins
                   FROM bandit_history
                   WHERE ts >= ?
                   GROUP BY arm""",
                (week_ago_ms,),
            ).fetchall()

        stats = []
        for r in rows:
            pulls = r["pulls"] or 0
            wins  = r["wins"]  or 0
            stats.append({
                "arm":      r["arm"],
                "pulls":    pulls,
                "wins":     wins,
                "win_rate": wins / pulls if pulls else 0.0,
            })
        stats.sort(key=lambda x: x["win_rate"], reverse=True)

        return {
            "top_preferences": self.top_preferences(3),
            "arm_stats":       stats,
        }

    def confidence_interval(self, arm: str, credible: float = 0.95) -> tuple[float, float]:
        """
        返回臂期望奖励的贝叶斯可信区间（Beta 分布分位数）。

        Parameters
        ----------
        credible : 可信水平，默认 95%

        Returns
        -------
        (lower, upper)
        """
        import math

        s    = self._state[arm]
        a, b = s["alpha"], s["beta"]
        tail = (1 - credible) / 2

        def _ibeta_approx(p: float, a: float, b: float) -> float:
            # Wilson–Hilferty 正态近似，足够精度
            x  = a / (a + b)
            v  = a * b / ((a + b) ** 2 * (a + b + 1))
            sd = math.sqrt(v)
            z  = {0.025: -1.96, 0.975: 1.96}.get(p, 0.0)
            return max(0.0, min(1.0, x + z * sd))

        return (_ibeta_approx(tail, a, b), _ibeta_approx(1 - tail, a, b))

    def reset(self) -> None:
        """重置所有臂为均匀先验（谨慎使用，会清空所有学习记忆）。"""
        with self._lock:
            for arm in ACTION_ARMS:
                self._state[arm] = {
                    "arm": arm, "alpha": 1.0, "beta": 1.0,
                    "total_pulls": 0, "total_wins": 0, "updated_at": 0,
                }
                self._upsert_arm(arm)
        print("[MemoryBox] reset to uniform prior")

"""kanban_client.py — 引擎侧 kanban DB 直连客户端（2026-08-16 缺陷修复新增）。

为什么需要直连 DB：
  1. `hermes kanban list --json` 不暴露 idempotency_key 字段 → 无法按引擎卡键扫描残留卡
  2. kanban CLI 无"重开 done 卡"命令（done → ready 需直接 UPDATE）
  3. 引擎收尾（flow done）需归档本 flow 全部残留卡

只允许操作 idempotency_key LIKE 'engine:{flow}:%' 的卡（引擎自持范围），
绝不触碰其他卡。所有写操作带 busy_timeout 容忍 dispatcher 并发写。

安全边界：
  - 只读查询：SELECT 任意引擎卡
  - 写操作：仅 reopen_done_card / archive_flow_cards（限引擎键范围）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# 引擎卡前缀
ENGINE_KEY_PREFIX = "engine:"

# 收尾归档目标状态（flow done 后仍存活的引擎卡 = 残留）
ORPHAN_STATUSES = ("ready", "running", "todo")


def resolve_db_path() -> Path:
    """解析 kanban DB 路径（与 hermes_cli.kanban_db 解析链一致）。

    优先级：
      1. HERMES_KANBAN_DB env（worker/dispatcher 环境显式指定）
      2. HERMES_KANBAN_HOME env + HERMES_KANBAN_BOARD env → <root>/kanban/boards/<board>/kanban.db
      3. HERMES_KANBAN_HOME env + <root>/kanban/current 文件 → boards/<slug>/kanban.db
      4. HERMES_KANBAN_HOME env → <root>/kanban.db（default board）
      5. 默认 root（Windows %LOCALAPPDATA%\\hermes / POSIX ~/.hermes）+ 同上 board 解析
    """
    env_db = os.environ.get("HERMES_KANBAN_DB", "").strip()
    if env_db:
        return Path(env_db)

    root = _resolve_root()
    board = _resolve_board(root)
    if board and board != "default":
        p = root / "kanban" / "boards" / board / "kanban.db"
        if p.exists():
            return p
    return root / "kanban.db"


def _resolve_root() -> Path:
    env_home = os.environ.get("HERMES_KANBAN_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser()
    env_hermes = os.environ.get("HERMES_HOME", "").strip()
    if env_hermes:
        p = Path(env_hermes).expanduser()
        # profile 模式：<root>/profiles/<name> → root 是祖父目录
        if p.parent.name == "profiles":
            return p.parent.parent
        return p
    if sys.platform == "win32":
        la = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(la) if la else Path.home() / "AppData" / "Local"
        return base / "hermes"
    return Path.home() / ".hermes"


def _resolve_board(root: Path) -> str | None:
    env_board = os.environ.get("HERMES_KANBAN_BOARD", "").strip()
    if env_board:
        return env_board
    cur = root / "kanban" / "current"
    if cur.exists():
        try:
            slug = cur.read_text(encoding="utf-8").strip()
            if slug:
                return slug
        except OSError:
            pass
    return "default"


def _connect() -> sqlite3.Connection:
    path = resolve_db_path()
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _engine_key_exact(flow: str, step: str) -> str:
    """新幂等键：engine:{flow}:{step}（2026-08-16 缺陷修复：去 iteration）。"""
    return f"{ENGINE_KEY_PREFIX}{flow}:{step}"


def _engine_key_prefix(flow: str) -> str:
    return f"{ENGINE_KEY_PREFIX}{flow}:"


def find_flow_cards(flow: str, statuses: tuple[str, ...] = ORPHAN_STATUSES,
                    conn: sqlite3.Connection | None = None) -> list[dict]:
    """查本 flow 指定状态的引擎卡（idempotency_key LIKE 'engine:{flow}:%'）。

    兼容旧键（engine:{flow}:{step}:{iteration}）与新键（engine:{flow}:{step}）。
    """
    close = conn is None
    c = conn or _connect()
    try:
        prefix = _engine_key_prefix(flow)
        rows = c.execute(
            f"SELECT id, status, title, idempotency_key, created_at, completed_at "
            f"FROM tasks WHERE idempotency_key LIKE ? AND status IN ({','.join('?' * len(statuses))}) "
            f"ORDER BY created_at DESC",
            (prefix + "%", *statuses),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if close:
            c.close()


def find_step_cards(flow: str, step: str,
                    conn: sqlite3.Connection | None = None) -> list[dict]:
    """查某 flow 某 step 的引擎卡（兼容新键 engine:{flow}:{step} 与旧键 ...:{iteration}）。"""
    close = conn is None
    c = conn or _connect()
    try:
        exact = _engine_key_exact(flow, step)
        like = exact + ":%"
        rows = c.execute(
            "SELECT id, status, title, idempotency_key, created_at, completed_at "
            "FROM tasks WHERE (idempotency_key = ? OR idempotency_key LIKE ?) "
            "AND status != 'archived' ORDER BY created_at DESC",
            (exact, like),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if close:
            c.close()


def reopen_done_card(task_id: str, new_title: str | None = None,
                     reason: str = "engine re-dispatch",
                     mode: str | None = None,
                     fix_hint: str | None = None) -> bool:
    """重开 done 卡（done → ready），让 dispatcher 重新 spawn worker。

    用于 verify FAIL 打回后同一 step 的再次 dispatch：复用同一张卡（幂等键去
    iteration 后天然命中），但必须把 done 重置为 ready 才能重新执行。

    F5（enginefix2 000-brief #5）：mode/fix_hint 可选参数——打回重做时注入
    增量/全量模式标记，写入 engine_reopened 审计事件 payload（worker/审计可
    追溯）。mode="incremental" = 增量修复；缺省 None = 旧行为（向后兼容）。

    顺带清理 complete 残留（completed_at）与旧 run 指针（current_run_id 置空，
    claim_task 会在 claim 时新建 run）。保留 history/result 供审计。
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT status FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not row or row["status"] != "done":
            return False
        now = int(time.time())
        with conn:
            conn.execute(
                "UPDATE tasks SET status='ready', completed_at=NULL, "
                "claim_lock=NULL, claim_expires=NULL, worker_pid=NULL, "
                "current_run_id=NULL"
                + (", title=?" if new_title else "")
                + " WHERE id=? AND status='done'",
                (*( (new_title,) if new_title else () ), task_id),
            )
            # 审计事件：引擎重开
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (task_id, "engine_reopened",
                 json.dumps({**({"mode": mode, "fix_hint": fix_hint}
                                if mode or fix_hint else {}),
                             "reason": reason}, ensure_ascii=False), now),
            )
            # respawn guard 豁免事件：hermes kanban 的 respawn guard 只认
            # status/promoted/unblocked/reclaimed 四种事件为“显式重排”，
            # 自造的 engine_reopened 不在其列，会把 verify FAIL 打回的重做
            # 误判为重复派工拦截满 1h 窗口。补写 status 事件命中豁免名单。
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES (?, 'status', ?, ?)",
                (task_id,
                 json.dumps({"from": "done", "to": "ready",
                             "reason": reason}, ensure_ascii=False), now),
            )
        return True
    finally:
        conn.close()


def archive_flow_cards(flow: str, dry_run: bool = False) -> list[dict]:
    """收尾归档：flow done 后，归档本 flow 全部残留卡（ready/running/todo）。

    仅归档 idempotency_key LIKE 'engine:{flow}:%' 的卡。running 卡一并归档
    （flow 已终态，残留 running 必为幽灵卡；归档会清理 claim/run 指针）。
    返回归档的卡列表（dry_run 只查不写）。
    """
    cards = find_flow_cards(flow)
    if not cards or dry_run:
        return cards
    conn = _connect()
    try:
        now = int(time.time())
        with conn:
            for c in cards:
                conn.execute(
                    "UPDATE tasks SET status='archived', "
                    "claim_lock=NULL, claim_expires=NULL, worker_pid=NULL, "
                    "current_run_id=NULL WHERE id=? AND status != 'archived'",
                    (c["id"],),
                )
                conn.execute(
                    "INSERT INTO task_events (task_id, kind, payload, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (c["id"], "engine_archived_orphan",
                     json.dumps({"flow": flow, "reason": "flow completed 收尾清理"},
                                ensure_ascii=False), now),
                )
                # 若残留 running 有未结束 run，一并收尾（对齐 kanban archive_task 语义）
                conn.execute(
                    "UPDATE task_runs SET status='reclaimed', outcome='reclaimed', "
                    "summary=COALESCE(summary, 'flow completed 引擎收尾'), "
                    "ended_at=?, claim_lock=NULL, claim_expires=NULL, worker_pid=NULL "
                    "WHERE task_id=? AND ended_at IS NULL",
                    (now, c["id"]),
                )
        return cards
    finally:
        conn.close()


def resolve_step_card(flow: str, step: str) -> dict | None:
    """查某 flow 某 step 当前可复用卡（非 archived，最新优先）。

    返回 None = 无卡（应新建）；返回 dict = 已有卡（含 status，调用方决定复用/重开）。
    """
    cards = find_step_cards(flow, step)
    return cards[0] if cards else None


def get_block_info(task_id: str) -> dict | None:
    """查卡最新 blocked 事件的 reason + kind（只读，符合安全边界）。

    用于 verify-wait blocked→escalate 机制（002 设计 §1.4，2026-08-21）：
    验收卡 blocked 时引擎需要把阻塞原因呈现给 user。
    返回 {"reason": str, "kind": str} 或 None（无 blocked 事件）。

    2026-08-21 F1 收尾（capacity 缺口）：dispatcher 因连续失败自动置 blocked
    （跑飞/capacity）不产生 kind='blocked' 事件，原因躺在 tasks 表
    last_failure_error/block_kind 字段——查不到事件时回退读 tasks 表，
    否则引擎 escalate 会显示"未知原因"。
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload FROM task_events "
            "WHERE task_id=? AND kind='blocked' "
            "ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row:
            p = json.loads(row["payload"] or "{}")
            return {"reason": p.get("reason", ""), "kind": p.get("kind", "")}
        # ── F1 收尾：无 blocked 事件 → 回退读 tasks 表（capacity/跑飞自动 blocked）──
        t = conn.execute(
            "SELECT status, block_kind, consecutive_failures, last_failure_error "
            "FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if t and t["status"] == "blocked":
            parts = []
            if t["consecutive_failures"]:
                parts.append(f"连续失败 {t['consecutive_failures']} 次")
            if t["last_failure_error"]:
                parts.append(str(t["last_failure_error"]))
            reason = "；".join(parts) if parts else "验收卡被 dispatcher 置 blocked（无 blocked 事件）"
            return {"reason": reason[:300], "kind": t["block_kind"] or "capacity"}
        return None
    finally:
        conn.close()

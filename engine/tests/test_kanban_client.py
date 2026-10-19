"""test_kanban_client.py — 引擎 kanban 直连客户端测试（t_62463005 缺陷修复）。

覆盖：
- 幂等键新格式（engine:{flow}:{step}，去 iteration）
- find_step_cards 兼容新旧键
- reopen_done_card（done → ready 复用同一张卡）
- archive_flow_cards（flow done 收尾归档残留卡）
- engine._do_done 触发收尾

隔离：所有测试用临时 sqlite DB（monkeypatch HERMES_KANBAN_DB env），
绝不触碰真实 kanban DB。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import kanban_client  # noqa: E402
import engine as eng  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT,
  assignee TEXT, status TEXT NOT NULL, priority INTEGER DEFAULT 0,
  created_by TEXT, created_at INTEGER NOT NULL, started_at INTEGER,
  completed_at INTEGER, workspace_kind TEXT DEFAULT 'scratch',
  workspace_path TEXT, branch_name TEXT, project_id TEXT,
  claim_lock TEXT, claim_expires INTEGER, tenant TEXT, result TEXT,
  idempotency_key TEXT, consecutive_failures INTEGER DEFAULT 0,
  worker_pid INTEGER, last_failure_error TEXT,
  max_runtime_seconds INTEGER, last_heartbeat_at INTEGER,
  current_run_id INTEGER, workflow_template_id TEXT,
  current_step_key TEXT, skills TEXT, model_override TEXT,
  max_retries INTEGER, goal_mode INTEGER DEFAULT 0,
  goal_max_turns INTEGER, session_id TEXT, block_kind TEXT,
  block_recurrences INTEGER DEFAULT 0, provider_override TEXT,
  reasoning_effort TEXT
);
CREATE TABLE IF NOT EXISTS task_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
  run_id INTEGER, kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
  profile TEXT, step_key TEXT, status TEXT, claim_lock TEXT,
  claim_expires INTEGER, worker_pid INTEGER, max_runtime_seconds INTEGER,
  last_heartbeat_at INTEGER,
  started_at INTEGER, ended_at INTEGER, outcome TEXT,
  summary TEXT, error TEXT, metadata TEXT
);
"""


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """临时 kanban DB，通过 HERMES_KANBAN_DB env 指向。"""
    db = tmp_path / "kanban.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    return db


def _card(con, tid: str, key: str, status: str = "ready",
          title: str = "t", now: int = 1000):
    con.execute(
        "INSERT INTO tasks (id, title, status, created_at, idempotency_key, "
        "assignee, workspace_kind) VALUES (?,?,?,?,?,?, 'scratch')",
        (tid, title, status, now, key, "hermes"),
    )


# ---- 幂等键格式 ----

def test_idempotency_key_no_iteration():
    """新幂等键 = engine:{flow}:{step}（去 iteration）。"""
    from executor_dispatch import _build_cmd
    cmd = _build_cmd("tank-battle-game", "implement", 12)
    key = cmd[cmd.index("--idempotency-key") + 1]
    assert key == "engine:tank-battle-game:implement"
    assert ":12" not in key  # iteration 不再参与
    # title 保留 iter 标注
    title = cmd[cmd.index("--idempotency-key") - 3]
    assert "(iter 12)" in title


def test_find_step_cards_new_and_old_key(tmp_db):
    """find_step_cards 兼容新键（engine:{flow}:{step}）与旧键（...:{iteration}）。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_old", "engine:flow-x:implement:3", status="done")
    _card(con, "t_new", "engine:flow-x:implement", status="ready")
    con.commit()
    con.close()
    cards = kanban_client.find_step_cards("flow-x", "implement")
    ids = [c["id"] for c in cards]
    assert "t_old" in ids and "t_new" in ids
    # archived 卡不参与复用
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_arch", "engine:flow-x:implement:1", status="archived")
    con.commit()
    con.close()
    cards2 = kanban_client.find_step_cards("flow-x", "implement")
    assert all(c["id"] != "t_arch" for c in cards2)


# ---- 复用决策 ----

def test_resolve_step_card_none(tmp_db):
    """无卡 → None（应新建）。"""
    assert kanban_client.resolve_step_card("flow-x", "plan") is None


def test_resolve_step_card_newest(tmp_db):
    """多张同 step 卡 → 返回最新（created_at DESC）。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_old", "engine:flow-x:implement:1", status="done", now=1000)
    _card(con, "t_mid", "engine:flow-x:implement:2", status="done", now=2000)
    _card(con, "t_new", "engine:flow-x:implement", status="ready", now=3000)
    con.commit()
    con.close()
    card = kanban_client.resolve_step_card("flow-x", "implement")
    assert card["id"] == "t_new"


# ---- 重开 done 卡 ----

def test_reopen_done_card(tmp_db):
    """done → ready：复用同一张卡（verify FAIL 打回重做场景）。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_impl", "engine:flow-x:implement", status="done",
          title="[引擎:flow-x] implement (iter 1)")
    con.execute("UPDATE tasks SET completed_at=2000, current_run_id=55, "
                "claim_lock='L', claim_expires=9999, worker_pid=1234 "
                "WHERE id='t_impl'")
    con.commit()
    con.close()

    ok = kanban_client.reopen_done_card("t_impl", new_title="[引擎:flow-x] implement (iter 2)")
    assert ok

    con = sqlite3.connect(str(tmp_db))
    row = con.execute("SELECT status, completed_at, current_run_id, claim_lock, "
                      "worker_pid, title FROM tasks WHERE id='t_impl'").fetchone()
    assert row[0] == "ready"
    assert row[1] is None        # completed_at 清空
    assert row[2] is None        # current_run_id 清空（claim 时新建）
    assert row[3] is None        # claim_lock 清空
    assert row[4] is None        # worker_pid 清空
    assert row[5] == "[引擎:flow-x] implement (iter 2)"  # title 更新为新 iter
    ev = con.execute("SELECT kind FROM task_events WHERE task_id='t_impl'").fetchone()
    assert ev[0] == "engine_reopened"
    con.close()


def test_reopen_only_done(tmp_db):
    """非 done 卡拒绝重开（running 卡不重开，避免打断在跑 worker）。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_run", "engine:flow-x:implement", status="running")
    con.commit()
    con.close()
    assert kanban_client.reopen_done_card("t_run") is False


# ---- 收尾归档 ----

def test_archive_flow_cards(tmp_db):
    """flow done 收尾：归档本 flow 全部残留卡（ready/running/todo）。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_r1", "engine:flow-x:implement:1", status="ready")
    _card(con, "t_r2", "engine:flow-x:implement", status="running")
    _card(con, "t_t", "engine:flow-x:plan", status="todo")
    _card(con, "t_done", "engine:flow-x:charter", status="done")  # done 不动
    _card(con, "t_other", "engine:other-flow:implement", status="ready")  # 他 flow 不动
    # running 卡留一个未结束 run（验证 run 收尾）
    con.execute("INSERT INTO task_runs (task_id, profile, status, started_at) "
                "VALUES ('t_r2', 'hermes', 'running', 1000)")
    con.commit()
    con.close()

    archived = kanban_client.archive_flow_cards("flow-x")
    ids = sorted(c["id"] for c in archived)
    assert ids == ["t_r1", "t_r2", "t_t"]

    con = sqlite3.connect(str(tmp_db))
    for tid, want in [("t_r1", "archived"), ("t_r2", "archived"), ("t_t", "archived"),
                      ("t_done", "done"), ("t_other", "ready")]:
        got = con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()[0]
        assert got == want, f"{tid}: {got} != {want}"
    # running 残留 run 收尾为 reclaimed
    run = con.execute("SELECT status, outcome FROM task_runs WHERE task_id='t_r2'").fetchone()
    assert run[0] == "reclaimed" and run[1] == "reclaimed"
    # 归档事件记录
    evs = [r[0] for r in con.execute(
        "SELECT kind FROM task_events WHERE kind='engine_archived_orphan'")]
    assert len(evs) == 3
    con.close()


def test_archive_flow_cards_dry_run(tmp_db):
    """dry_run 只查不改。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_r1", "engine:flow-x:implement", status="ready")
    con.commit()
    con.close()
    cards = kanban_client.archive_flow_cards("flow-x", dry_run=True)
    assert [c["id"] for c in cards] == ["t_r1"]
    con = sqlite3.connect(str(tmp_db))
    got = con.execute("SELECT status FROM tasks WHERE id='t_r1'").fetchone()[0]
    assert got == "ready"  # 未归档
    con.close()


# ---- get_block_info（F1 blocked 原因查询，002 §1.4）----

def test_get_block_info(tmp_db):
    """查最新 blocked 事件的 reason + kind；多条取最新。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_blk", "engine:flow-x:verify", status="blocked")
    con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES ('t_blk', 'blocked', ?, 1000)",
                (json.dumps({"reason": "旧原因", "kind": "dependency"}, ensure_ascii=False),))
    con.execute("INSERT INTO task_events (task_id, kind, payload, created_at) "
                "VALUES ('t_blk', 'blocked', ?, 2000)",
                (json.dumps({"reason": "请 hermes 拍板", "kind": "needs_input"}, ensure_ascii=False),))
    con.commit()
    con.close()
    info = kanban_client.get_block_info("t_blk")
    assert info == {"reason": "请 hermes 拍板", "kind": "needs_input"}  # 取最新


def test_get_block_info_none(tmp_db):
    """无 blocked 事件 → None（卡正常/事件缺失均不 crash）。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_ok", "engine:flow-x:verify", status="running")
    con.commit()
    con.close()
    assert kanban_client.get_block_info("t_ok") is None


def test_get_block_info_capacity_fallback(tmp_db):
    """F1 收尾：无 blocked 事件但卡 status=blocked（跑飞/capacity 自动置，
    不产生 blocked 事件）→ 回退读 tasks 表 last_failure_error/block_kind，
    不让引擎 escalate 显示"未知原因"。"""
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_cap", "engine:flow-x:verify", status="blocked")
    con.execute(
        "UPDATE tasks SET consecutive_failures=2, "
        "last_failure_error='Iteration budget exhausted (60/60)', "
        "block_kind='capacity' WHERE id='t_cap'"
    )
    con.commit()
    con.close()
    info = kanban_client.get_block_info("t_cap")
    assert info is not None
    assert info["kind"] == "capacity"
    assert "Iteration budget exhausted" in info["reason"]
    assert "连续失败 2 次" in info["reason"]


# ---- engine 收尾集成 ----

def test_engine_done_archives_orphans(tmp_db, tmp_path, monkeypatch):
    """engine 到 done：状态置 completed + 收尾归档本 flow 残留卡。"""
    # 隔离引擎目录（flows/instances 指向临时目录）
    eng.FLOWS_DIR = tmp_path / "flows"
    eng.INSTANCES_DIR = tmp_path / "instances"
    eng.FLOWS_DIR.mkdir(exist_ok=True)
    eng.INSTANCES_DIR.mkdir(exist_ok=True)

    import yaml
    flow_fp = eng.FLOWS_DIR / "flow-x.yaml"
    flow_fp.write_text(yaml.safe_dump({
        "name": "flow-x",
        "steps": [
            {"id": "implement", "type": "dispatch", "next": "done"},
            {"id": "done", "type": "done"},
        ],
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # 真实 DB（临时）预置残留卡
    con = sqlite3.connect(str(tmp_db))
    _card(con, "t_ghost", "engine:flow-x:implement", status="ready")
    con.commit()
    con.close()

    e = eng.FlowEngine("flow-x")
    e.run()  # implement（无 executor → 记录意图）
    out = e.run()  # done → 收尾
    assert "流程完成" in out
    st = e._load_state()
    assert st["status"] == "completed"

    con = sqlite3.connect(str(tmp_db))
    got = con.execute("SELECT status FROM tasks WHERE id='t_ghost'").fetchone()[0]
    assert got == "archived"
    con.close()

    # 恢复
    eng.FLOWS_DIR = Path(__file__).resolve().parent.parent / "flows"
    eng.INSTANCES_DIR = Path(__file__).resolve().parent.parent / "instances"

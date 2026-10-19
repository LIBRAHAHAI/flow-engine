"""test_card_done.py — wait card_done 锚点单测（t_a1f33cfc，验收标准 4）。

覆盖：上游卡 done=通过 / 在途=等待 / 接入点无上游+实例收官=历史通过 /
      显式卡 id done=通过 / 显式卡未 done=等待 / artifact 优先于 card_done /
      校验器（wait 需 artifact 或 card_done 其一）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
import yaml

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import engine as eng  # noqa: E402
import validate_template as vt  # noqa: E402

FLOWS = ENGINE_DIR / "flows"
INSTANCES = ENGINE_DIR / "instances"


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离环境（对齐 test_engine.py tmp_env 约定）。"""
    eng.FLOWS_DIR = tmp_path / "flows"
    eng.INSTANCES_DIR = tmp_path / "instances"
    eng.FLOWS_DIR.mkdir(exist_ok=True)
    eng.INSTANCES_DIR.mkdir(exist_ok=True)
    tmp_db = tmp_path / "kanban.db"
    sqlite3.connect(str(tmp_db)).close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_db))
    yield tmp_path
    eng.FLOWS_DIR = FLOWS
    eng.INSTANCES_DIR = INSTANCES


STEPS = [
    {"id": "charter", "type": "dispatch", "description": "派 charter",
     "executor": "executor_dispatch.py", "next": "review"},
    {"id": "review", "type": "wait", "description": "双审屏障",
     "card_done": ["@upstream"], "next": "plan"},
    {"id": "plan", "type": "dispatch", "description": "派 plan",
     "executor": "executor_dispatch.py", "next": "done"},
    {"id": "done", "type": "done", "description": "完成"},
]


def _write_flow(name: str, steps: list[dict]):
    fp = eng.FLOWS_DIR / f"{name}.yaml"
    fp.write_text(yaml.safe_dump({"name": name, "steps": steps},
                                 allow_unicode=True, sort_keys=False), encoding="utf-8")
    return fp


def _write_instance(name: str, state: dict):
    fp = eng.INSTANCES_DIR / f"{name}.json"
    fp.write_text(json.dumps({"flow": name, "step": None, "iteration": 0,
                              "history": [], "status": "idle", **state},
                             ensure_ascii=False), encoding="utf-8")
    return fp


def _fake_resolve(monkeypatch, status):
    def fake(flow, step):
        return {"id": "t_fake", "status": status} if status else None
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card", fake)


# ---- card_done 引擎行为 ----

def test_card_done_upstream_done_passes(tmp_env, monkeypatch):
    """上游卡 done → wait 通过，推进到 plan。"""
    _write_flow("f1", STEPS)
    _write_instance("f1", {"step": "review", "status": "running"})
    _fake_resolve(monkeypatch, "done")
    out = eng.FlowEngine("f1").run()
    assert "card_done 锚点" in out and "已 done" in out
    assert eng.FlowEngine("f1")._load_state()["step"] == "plan"


def test_card_done_upstream_running_waits(tmp_env, monkeypatch):
    """上游卡在途 → 保持等待（停在本步）。"""
    _write_flow("f1", STEPS)
    _write_instance("f1", {"step": "review", "status": "running"})
    _fake_resolve(monkeypatch, "ready")
    out = eng.FlowEngine("f1").run()
    assert "等待" in out
    assert eng.FlowEngine("f1")._load_state()["step"] == "review"


def test_card_done_no_upstream_completed_instance(tmp_env, monkeypatch):
    """接入点无上游 + 实例已收官 → 引擎对终态实例直接短路（run 层）。

    收官实例的 run() 首行即返回『流程已终态』，wait 永不执行——这正是
    032/flow-engine-build 等收官 flow 的真实运行态：card_done 仅消校验错。
    """
    steps = [dict(s) for s in STEPS]
    steps[1] = {"id": "review", "type": "wait", "description": "评审接入点",
                "card_done": ["@upstream"], "next": "plan"}
    steps = steps[1:]  # 去掉 charter，review 为首步（无上游）
    _write_flow("f1", steps)
    _write_instance("f1", {"step": "review", "status": "completed"})
    _fake_resolve(monkeypatch, None)
    out = eng.FlowEngine("f1").run()
    assert "已终态" in out

    # 反向：running 状态 + 无上游 + 卡不在 → 正常等待（不误判收官通过）
    _write_instance("f1", {"step": "review", "status": "running"})
    out2 = eng.FlowEngine("f1").run()
    assert "等待 card_done" in out2


def test_card_done_explicit_id_done(tmp_env, monkeypatch):
    """显式卡 id done → 通过。"""
    steps = [dict(s) for s in STEPS]
    steps[1]["card_done"] = ["t_explicit"]
    _write_flow("f1", steps)
    _write_instance("f1", {"step": "review", "status": "running"})
    conn = sqlite3.connect(str(tmp_env / "kanban.db"))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t_explicit', 'done')")
    conn.commit(); conn.close()
    out = eng.FlowEngine("f1").run()
    assert "t_explicit" in out and "已 done" in out
    assert eng.FlowEngine("f1")._load_state()["step"] == "plan"


def test_card_done_explicit_id_not_done(tmp_env, monkeypatch):
    """显式卡未 done → 等待。"""
    steps = [dict(s) for s in STEPS]
    steps[1]["card_done"] = ["t_pending"]
    _write_flow("f1", steps)
    _write_instance("f1", {"step": "review", "status": "running"})
    conn = sqlite3.connect(str(tmp_env / "kanban.db"))
    conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('t_pending', 'ready')")
    conn.commit(); conn.close()
    out = eng.FlowEngine("f1").run()
    assert "等待" in out
    assert eng.FlowEngine("f1")._load_state()["step"] == "review"


def test_artifact_priority_over_card_done(tmp_env, monkeypatch):
    """artifact + card_done 并存：artifact 齐全 → artifact 语义，不触发卡检查。"""
    art = tmp_env / "art.md"
    art.write_text("ok", encoding="utf-8")
    steps = [dict(s) for s in STEPS]
    steps[1] = {"id": "review", "type": "wait", "description": "双审",
                "artifact": str(art), "card_done": ["@upstream"], "next": "plan"}
    _write_flow("f1", steps)
    _write_instance("f1", {"step": "review", "status": "running"})
    called = []
    def fake(flow, step):
        called.append(1)
        return {"id": "t_fake", "status": "ready"}
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card", fake)
    out = eng.FlowEngine("f1").run()
    assert "产物已出现" in out
    assert not called


# ---- 校验器 ----

def _vt(tmp_path: Path, name: str, wait_fields: str):
    fp = tmp_path / name
    fp.write_text(f"""
name: v{name}
steps:
  - id: a
    type: dispatch
    next: w
  - id: w
    type: wait
{wait_fields}
    next: d
  - id: d
    type: done
""", encoding="utf-8")
    return vt.validate_file(fp)


def test_validate_wait_no_anchor_errors(tmp_path):
    errs = _vt(tmp_path, "bad.yaml", "")
    assert any("card_done" in e for e in errs)


def test_validate_wait_with_card_done_ok(tmp_path):
    assert _vt(tmp_path, "good.yaml", '    card_done: ["@upstream"]') == []


def test_validate_wait_with_artifact_ok(tmp_path):
    assert _vt(tmp_path, "good2.yaml", '    artifact: "x.md"') == []

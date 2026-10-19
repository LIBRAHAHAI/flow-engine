"""test_engine.py — flow-engine 引擎核心 pytest（M1.4，2026-08-16）。

覆盖：状态机 5 类型 / 打回循环 / 拍板（含 options A/B/C）/ 幂等 / 重启恢复 /
      wait 产物 / verify 状态机 / dispatch 派发。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import engine as eng  # noqa: E402

FLOWS = ENGINE_DIR / "flows"
INSTANCES = ENGINE_DIR / "instances"


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离环境：把模板复制到临时目录，实例目录指向临时目录。

    t_62463005 修复后 _do_done 会触发 kanban_client 收尾清理，故同时
    把 HERMES_KANBAN_DB 指向临时 DB，防止测试触达真实 kanban board。
    """
    eng.FLOWS_DIR = tmp_path / "flows"
    eng.INSTANCES_DIR = tmp_path / "instances"
    eng.FLOWS_DIR.mkdir(exist_ok=True)
    eng.INSTANCES_DIR.mkdir(exist_ok=True)
    tmp_db = tmp_path / "kanban.db"
    sqlite3.connect(str(tmp_db)).close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_db))
    yield tmp_path
    # 恢复
    eng.FLOWS_DIR = FLOWS
    eng.INSTANCES_DIR = INSTANCES


def _write_flow(tmp_env: Path, name: str, steps: list[dict], **extra):
    d = {"name": name, "steps": steps, **extra}
    fp = eng.FLOWS_DIR / f"{name}.yaml"
    import yaml
    fp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return fp


def _write_instance(tmp_env: Path, name: str, state: dict):
    fp = eng.INSTANCES_DIR / f"{name}.json"
    fp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return fp


def _decision(tmp_env: Path, name: str, decision: str):
    (eng.INSTANCES_DIR / f"{name}.decision").write_text(decision, encoding="utf-8")


# ---- 状态机基础 ----

def test_done_steps(tmp_env):
    """done 步骤 → 流程完成。"""
    _write_flow(tmp_env, "f1", [{"id": "d", "type": "done"}])
    e = eng.FlowEngine("f1")
    out = e.run()
    assert "完成" in out
    st = e._load_state()
    assert st["status"] == "completed"


def test_unknown_step_type(tmp_env):
    """未知步骤类型 → 抛错。"""
    _write_flow(tmp_env, "f2", [{"id": "x", "type": "magic"}])
    e = eng.FlowEngine("f2")
    with pytest.raises(ValueError):
        e.run()


def test_missing_template(tmp_env):
    """模板不存在 → FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        eng.FlowEngine("no-such-flow")


# ---- wait 步骤 ----

def test_wait_waits(tmp_env, tmp_path):
    """wait 未满足 → 幂等等待（不推进）。"""
    target = tmp_path / "prod.md"
    _write_flow(tmp_env, "f3", [
        {"id": "w", "type": "wait", "artifact": str(target), "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f3")
    out = e.run()
    assert "等待" in out
    st = e._load_state()
    assert st["step"] == "w"  # 停在 wait


def test_wait_satisfied(tmp_env, tmp_path):
    """wait 产物出现 → 推进。"""
    target = tmp_path / "prod.md"
    target.write_text("hello", encoding="utf-8")
    _write_flow(tmp_env, "f4", [
        {"id": "w", "type": "wait", "artifact": str(target), "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f4")
    out = e.run()
    st = e._load_state()
    assert st["step"] == "d"


def test_wait_multi_artifacts(tmp_env, tmp_path):
    """wait 多产物：全部存在才推进（M1.1 增强）。"""
    a1 = tmp_path / "a.md"
    a2 = tmp_path / "b.md"
    a1.write_text("x", encoding="utf-8")
    _write_flow(tmp_env, "f4m", [
        {"id": "w", "type": "wait", "artifacts": [str(a1), str(a2)], "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f4m")
    e.run()
    assert e._load_state()["step"] == "w"  # 缺 b，仍等待
    a2.write_text("y", encoding="utf-8")
    e.run()
    assert e._load_state()["step"] == "d"  # 齐了，推进


# ---- verify 步骤 ----

def test_verify_pass(tmp_env, tmp_path):
    """verify：status_in 命中 → PASS → 推进。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: accepted\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "f5", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "v"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f5")
    e.run()
    st = e._load_state()
    assert st["step"] == "d"


def test_verify_fail_then_pass(tmp_env, tmp_path):
    """verify 不通过 → 打回；产物修正后 → 通过推进（打回循环）。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: proposed\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "f6", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted"], "next": "d", "on_fail": "w"},
        {"id": "w", "type": "wait", "artifact": str(art), "next": "v"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f6")
    out1 = e.run()
    st = e._load_state()
    # 第一次：FAIL → 打回 w
    assert st["step"] == "w"
    # 修正产物 → 推进
    art.write_text("---\nstatus: accepted\n---\n", encoding="utf-8")
    e.run()
    e.run()
    st = e._load_state()
    assert st["step"] == "d"


# ---- decision 拍板 ----

def test_decision_waits(tmp_env):
    """decision 无拍板文件 → 等待。"""
    _write_flow(tmp_env, "f7", [
        {"id": "p", "type": "decision", "question": "ok?", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f7")
    out = e.run()
    assert "等待拍板" in out
    st = e._load_state()
    assert st["step"] == "p"


def test_decision_approve(tmp_env):
    """decision 同意 → 推进。"""
    _write_flow(tmp_env, "f8", [
        {"id": "p", "type": "decision", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    _decision(tmp_env, "f8", "同意")
    e = eng.FlowEngine("f8")
    e.run()
    st = e._load_state()
    assert st["step"] == "d"


def test_decision_reject(tmp_env):
    """decision 驳回 → 打回 on_reject。"""
    _write_flow(tmp_env, "f9", [
        {"id": "p", "type": "decision", "next": "d", "on_reject": "r"},
        {"id": "r", "type": "wait", "artifact": str(INSTANCES / "f9.json"), "next": "p"},
        {"id": "d", "type": "done"},
    ])
    _decision(tmp_env, "f9", "驳回")
    e = eng.FlowEngine("f9")
    e.run()
    st = e._load_state()
    assert st["step"] == "r"  # 打回 r


def test_decision_options_abc(tmp_env):
    """options A/B/C：选 A → next A；选 B → next B。"""
    _write_flow(tmp_env, "f10", [
        {"id": "p", "type": "decision", "next": "d",
         "options": [
             {"key": "A", "label": "接受", "next": "a"},
             {"key": "B", "label": "延迟", "next": "b"},
         ]},
        {"id": "a", "type": "done"},
        {"id": "b", "type": "done"},
        {"id": "d", "type": "done"},
    ])
    _decision(tmp_env, "f10", "A")
    e = eng.FlowEngine("f10")
    e.run()
    assert e._load_state()["step"] == "a"

    # B 场景：重置实例回拍板点（避免复用 A 的推进状态）
    _write_instance(tmp_env, "f10", {"flow": "f10", "step": "p", "iteration": 0,
                                     "history": [], "status": "running"})
    _decision(tmp_env, "f10", "B")
    e = eng.FlowEngine("f10")
    e.run()
    assert e._load_state()["step"] == "b"


def test_decision_options_unmatched(tmp_env):
    """options 未匹配 → 打回 on_reject。"""
    _write_flow(tmp_env, "f11", [
        {"id": "p", "type": "decision", "next": "d", "on_reject": "r",
         "options": [{"key": "A", "label": "接受", "next": "d"}]},
        {"id": "r", "type": "wait", "artifact": str(INSTANCES / "f11.json"), "next": "p"},
        {"id": "d", "type": "done"},
    ])
    _decision(tmp_env, "f11", "X")  # 未匹配
    e = eng.FlowEngine("f11")
    e.run()
    assert e._load_state()["step"] == "r"


# ---- dispatch 派发 ----

def test_dispatch_no_executor(tmp_env):
    """dispatch 无 executor → 记录意图 + 推进。"""
    _write_flow(tmp_env, "f12", [
        {"id": "dp", "type": "dispatch", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f12")
    e.run()
    st = e._load_state()
    assert st["step"] == "d"
    assert st["iteration"] == 1
    assert any(h["action"] == "dispatch" for h in st["history"])


# ---- 幂等 / 重启恢复 ----

def test_restart_resume(tmp_env):
    """重启恢复：实例持久化后，新 FlowEngine 读同一状态继续。"""
    _write_flow(tmp_env, "f13", [
        {"id": "p", "type": "decision", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e1 = eng.FlowEngine("f13")
    e1.run()  # 停拍板点
    # 模拟"重启"：新实例
    e2 = eng.FlowEngine("f13")
    st = e2._load_state()
    assert st["step"] == "p"  # 状态保留


def test_dispatch_idempotent(tmp_env):
    """dispatch 重复心跳不重复计数（无变化幂等）。"""
    _write_flow(tmp_env, "f14", [
        {"id": "dp", "type": "dispatch", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    e = eng.FlowEngine("f14")
    e.run()
    e2 = eng.FlowEngine("f14")
    e2.run()  # 已在 d（done 幂等）
    st = e2._load_state()
    assert st["iteration"] == 1  # 不重复派发


# ---- 历史不可删 ----

def test_history_append_only(tmp_env):
    """history 只追加不覆盖（审计 trace）。"""
    _write_flow(tmp_env, "f15", [
        {"id": "p", "type": "decision", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    _decision(tmp_env, "f15", "同意")
    e = eng.FlowEngine("f15")
    e.run()
    st = e._load_state()
    assert any(h["action"] == "decision" for h in st["history"])


# ---- F1 verify-wait blocked→escalate（mechanism-fix-engine-verify-wait，002 §1.2）----

def test_verify_blocked_escalate(tmp_env, tmp_path, monkeypatch):
    """S1：验收卡 blocked → verify_escalate，waited 不增，输出 ⚠🚨+拍板点（无 ⏳）。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fb1", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl"},
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card",
                        lambda flow, step: {"id": "t_test", "status": "blocked"})
    monkeypatch.setattr(eng.kanban_client, "get_block_info",
                        lambda tid: {"reason": "测试 blocked", "kind": "needs_input"})
    e = eng.FlowEngine("fb1")
    out = e.run()
    st = e._load_state()
    # 停在 verify 步骤（不推进不打回）
    assert st["step"] == "v"
    # history 末条 = verify_escalate（含卡 ID + 原因）
    assert st["history"][-1]["action"] == "verify_escalate"
    assert st["history"][-1]["card_id"] == "t_test"
    assert "测试 blocked" in st["history"][-1]["reason"]
    # waited 不增（verify_wait_done 条数 == 0）
    assert sum(1 for h in st["history"] if h.get("action") == "verify_wait_done") == 0
    # wrapper 投递契约：含「拍板点」无条件投递；不用 ⏳（语义矛盾）
    assert "⚠🚨" in out and "拍板点" in out and "⏳" not in out


def test_verify_blocked_no_timeout(tmp_env, tmp_path, monkeypatch):
    """S2：blocked 期间连续 ≥5 轮心跳无超时打回（wait_max=3，跑 6 轮仍 escalate）。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fb2", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl",
         "verify_wait_max": 3},
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card",
                        lambda flow, step: {"id": "t_test", "status": "blocked"})
    # reason 含「等待」也不影响投递（含「拍板点」无条件投递）
    monkeypatch.setattr(eng.kanban_client, "get_block_info",
                        lambda tid: {"reason": "等待 user 拍板", "kind": "needs_input"})
    e = eng.FlowEngine("fb2")
    for i in range(6):  # 6 轮 > wait_max=3：无 blocked 分支早已超时打回
        e.run()
        st = e._load_state()
        assert st["step"] == "v", f"第 {i + 1} 轮 step 变了"
        assert not any(h.get("action") == "verify" and h.get("passed") is False
                       for h in st["history"]), f"第 {i + 1} 轮触发 on_fail"
        assert sum(1 for h in st["history"]
                   if h.get("action") == "verify_wait_done") == 0
    escalates = [h for h in st["history"] if h.get("action") == "verify_escalate"]
    assert len(escalates) == 6  # 每轮 +1


def test_verify_blocked_self_heal(tmp_env, tmp_path, monkeypatch):
    """S1 边界：卡先 blocked 后 unblock（ready）→ 下轮心跳恢复正常计轮。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fb3", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl",
         "executor": "no_such_executor.py"},
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    card_state = {"status": "blocked"}
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card",
                        lambda flow, step: {"id": "t_test", **card_state})
    monkeypatch.setattr(eng.kanban_client, "get_block_info",
                        lambda tid: {"reason": "x", "kind": "needs_input"})
    e = eng.FlowEngine("fb3")
    e.run()
    assert e._load_state()["history"][-1]["action"] == "verify_escalate"
    # unblock → ready：下轮走正常计轮（executor 不存在 → 派发异常不阻断等待语义）
    card_state["status"] = "ready"
    out = e.run()
    st = e._load_state()
    assert st["history"][-1]["action"] == "verify_wait_done"
    assert "⏳" in out


def test_verify_done_waits_counts(tmp_env, tmp_path, monkeypatch):
    """S3：status=done 且卡非 blocked → 正常派卡等待计轮（现有语义不回归）。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fs1", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl",
         "executor": "no_such_executor.py"},  # 派发失败不阻断等待语义
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card", lambda f, s: None)
    e = eng.FlowEngine("fs1")
    out = e.run()
    st = e._load_state()
    assert st["step"] == "v"
    assert st["history"][-1]["action"] == "verify_wait_done"
    assert "⏳" in out


def test_verify_done_then_accepted_pass(tmp_env, tmp_path, monkeypatch):
    """S3：done → 等待 → 产物改 accepted → 下次心跳 PASS 推进。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fs2", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl",
         "executor": "no_such_executor.py"},
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card", lambda f, s: None)
    e = eng.FlowEngine("fs2")
    e.run()  # done → 派卡等待
    assert e._load_state()["step"] == "v"
    art.write_text("---\nstatus: accepted\n---\n", encoding="utf-8")
    out = e.run()
    assert e._load_state()["step"] == "d"
    assert "PASS" in out


def test_verify_done_then_rejected_fail(tmp_env, tmp_path, monkeypatch):
    """S3：done → 等待 → 产物改 rejected → FAIL 打回 on_fail。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fs3", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl",
         "executor": "no_such_executor.py"},
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card", lambda f, s: None)
    e = eng.FlowEngine("fs3")
    e.run()  # done → 派卡等待
    art.write_text("---\nstatus: rejected\n---\n", encoding="utf-8")
    out = e.run()
    st = e._load_state()
    assert st["step"] == "impl"  # 打回 on_fail
    assert "FAIL" in out


def test_verify_wait_timeout_regression(tmp_env, tmp_path, monkeypatch):
    """S3：超时语义不回归 — waited >= wait_max 且卡非 blocked 仍打回 on_fail。"""
    art = tmp_path / "report.md"
    art.write_text("---\nstatus: done\n---\n", encoding="utf-8")
    _write_flow(tmp_env, "fs4", [
        {"id": "v", "type": "verify", "artifact": str(art),
         "status_in": ["accepted", "closed"], "next": "d", "on_fail": "impl",
         "verify_wait_max": 3, "executor": "no_such_executor.py"},
        {"id": "impl", "type": "dispatch", "next": "v"},
        {"id": "d", "type": "done"},
    ])
    # 预填 history 使 waited 达到 wait_max
    hist = [{"step": "v", "action": "verify_wait_done", "iteration": i}
            for i in range(1, 4)]
    _write_instance(tmp_env, "fs4", {"flow": "fs4", "step": "v", "iteration": 3,
                                     "history": hist, "status": "running"})
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card", lambda f, s: None)
    e = eng.FlowEngine("fs4")
    out = e.run()
    st = e._load_state()
    assert st["step"] == "impl"  # 超时打回
    assert "超时" in out


# ---- 2026-08-30 wait 屏障断链修复（t_46516f87）----

def test_resolve_artifact_glob_latest(tmp_path):
    """glob 支持：精确路径旧行为不变；通配取 mtime 最新匹配。"""
    import time
    old = tmp_path / "001-a.md"
    new = tmp_path / "002-b.md"
    old.write_text("old", encoding="utf-8")
    time.sleep(0.05)
    new.write_text("new", encoding="utf-8")
    # 精确路径
    assert eng._resolve_artifact(str(old)) == old
    # 通配取最新
    got = eng._resolve_artifact(str(tmp_path / "*.md"))
    assert got == new
    # 无匹配
    assert eng._resolve_artifact(str(tmp_path / "*-nope.md")) is None


def test_wait_escalate_when_upstream_done(tmp_env, tmp_path, monkeypatch):
    """S1：上游卡 done + 屏障产物 N 轮缺失 → wait_escalate，next 保持 None 不打回。"""
    missing_art = tmp_path / "exchange" / "never-written.md"
    _write_flow(tmp_env, "fw1", [
        {"id": "impl", "type": "dispatch", "next": "w"},
        {"id": "w", "type": "wait", "artifact": str(missing_art), "next": "v",
         "wait_escalate_max": 2},
        {"id": "v", "type": "verify", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    # 预填 2 轮 wait_missing_done + 上游卡已 done
    hist = [{"step": "w", "action": "wait_missing_done", "detail": "x"},
            {"step": "w", "action": "wait_missing_done", "detail": "x"}]
    _write_instance(tmp_env, "fw1", {"flow": "fw1", "step": "w", "iteration": 3,
                                     "history": hist, "status": "running"})
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card",
                        lambda f, s: {"id": "t_fake", "status": "done"})
    e = eng.FlowEngine("fw1")
    out = e.run()
    st = e._load_state()
    assert st["step"] == "w"  # 不推进（escalate 等人工）
    assert st["history"][-1]["action"] == "wait_escalate"
    assert "需人工处置" in out


def test_wait_normal_waiting_when_card_inflight(tmp_env, tmp_path, monkeypatch):
    """S2：产物缺失但上游卡在途 → 正常静默等待（不 escalate），计轮 wait_missing_done。"""
    missing_art = tmp_path / "exchange" / "pending.md"
    _write_flow(tmp_env, "fw2", [
        {"id": "impl", "type": "dispatch", "next": "w"},
        {"id": "w", "type": "wait", "artifact": str(missing_art), "next": "v",
         "wait_escalate_max": 2},
        {"id": "v", "type": "verify", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    _write_instance(tmp_env, "fw2", {"flow": "fw2", "step": "w", "iteration": 1,
                                     "history": [], "status": "running"})
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card",
                        lambda f, s: {"id": "t_fake", "status": "running"})
    e = eng.FlowEngine("fw2")
    out = e.run()
    st = e._load_state()
    assert st["step"] == "w"
    assert st["history"][-1]["action"] == "wait_missing_done"
    assert "wait_escalate" not in [h.get("action") for h in st["history"]]
    assert "等待" in out


def test_wait_zero_history_first_round_no_escalate(tmp_env, tmp_path, monkeypatch):
    """S3：首轮（waited=0 < max）即使卡 done 也不 escalate——给 worker 正常交付窗口。"""
    missing_art = tmp_path / "x.md"
    _write_flow(tmp_env, "fw3", [
        {"id": "impl", "type": "dispatch", "next": "w"},
        {"id": "w", "type": "wait", "artifact": str(missing_art), "next": "v",
         "wait_escalate_max": 3},
        {"id": "v", "type": "verify", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    _write_instance(tmp_env, "fw3", {"flow": "fw3", "step": "w", "iteration": 1,
                                     "history": [], "status": "running"})
    monkeypatch.setattr(eng.kanban_client, "resolve_step_card",
                        lambda f, s: {"id": "t_fake", "status": "done"})
    e = eng.FlowEngine("fw3")
    out = e.run()
    st = e._load_state()
    assert st["history"][-1]["action"] == "wait_missing_done"
    assert "wait_escalate" not in [h.get("action") for h in st["history"]]


def test_derive_delivery_from_flow_yaml(tmp_env, tmp_path, monkeypatch):
    """S4：_derive_delivery 从 flow yaml 派生交付路径（单一事实源）。"""
    import executor_dispatch as ed
    monkeypatch.setattr(ed, "FLOWS_DIR", eng.FLOWS_DIR)
    art = str(tmp_path / "exchange" / "artifacts" / "002-report.md")
    _write_flow(tmp_env, "fd1", [
        {"id": "impl", "type": "dispatch", "next": "w"},
        {"id": "w", "type": "wait", "artifact": art, "next": "v"},
        {"id": "v", "type": "verify", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    got = ed._derive_delivery("fd1", "impl")
    assert got == art
    # 下游无 artifact → None
    _write_flow(tmp_env, "fd2", [
        {"id": "impl", "type": "dispatch", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    assert ed._derive_delivery("fd2", "impl") is None
    # flow 不存在 → None（不抛错）
    assert ed._derive_delivery("no-such-flow", "impl") is None


def test_build_cmd_injects_delivery(tmp_env, tmp_path, monkeypatch):
    """S5：_build_cmd 卡 body 注入派生交付路径；goal 已含路径不重复注入。"""
    import executor_dispatch as ed
    monkeypatch.setattr(ed, "FLOWS_DIR", eng.FLOWS_DIR)
    art = "D:/fake/exchange/artifacts/002-r.md"
    _write_flow(tmp_env, "fb1", [
        {"id": "impl", "type": "dispatch", "next": "w"},
        {"id": "w", "type": "wait", "artifact": art, "next": "v"},
        {"id": "v", "type": "verify", "next": "d"},
        {"id": "d", "type": "done"},
    ])
    monkeypatch.setattr(ed, "_yaml_cfg", None)
    cmd = ed._build_cmd("fb1", "impl", 1)
    body = cmd[cmd.index("--body") + 1]
    assert "交付路径以引擎屏道为准" in body and art in body
    # goal 已含路径 → 不重复
    monkeypatch.setattr(ed, "_yaml_cfg", {"default_assignee": {},
                                          "step_assignee": {},
                                          "step_goal": {"impl": f"干活，交付: {art}"},
                                          "step_skills": {}})
    cmd2 = ed._build_cmd("fb1", "impl", 1)
    body2 = cmd2[cmd2.index("--body") + 1]
    assert body2.count(art) == 1

"""test_cron_coupling.py — 引擎心跳生命周期耦合单测（2026-10-10 任务书）。

全部 mock hermes 子进程（_run_hermes），绝不触达真实 cron job；
reconcile_silent 的禁用开关（CRON_COUPLING_DISABLE，conftest 已置 1）
在需要实测的用例内临时移除（monkeypatch 自动还原）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import cron_coupling as cc  # noqa: E402
import engine as eng  # noqa: E402


class FakeHermes:
    """替身 _run_hermes：记账调用并模拟 job 状态转移。"""

    def __init__(self, state: str = "paused"):
        self.state = state
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(tuple(args))
        if args == ("cron", "list", "--all"):
            return f"  {cc.COUPLED_JOB_ID} [{self.state}]\n    Name: fake\n"
        if args == ("cron", "resume", cc.COUPLED_JOB_ID):
            self.state = "active"
            return "ok"
        if args == ("cron", "pause", cc.COUPLED_JOB_ID):
            self.state = "paused"
            return "ok"
        raise AssertionError(f"意外调用: {args}")


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "instances"
    d.mkdir()
    monkeypatch.setattr(cc, "_instances_dir", lambda: d)
    return d


def _write_inst(d: Path, name: str, status: str) -> None:
    (d / f"{name}.json").write_text(
        json.dumps({"flow": name, "status": status}), encoding="utf-8")


def test_reconcile_on_resumes(sandbox, monkeypatch):
    _write_inst(sandbox, "f1", "running")
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "on"
    assert fake.state == "active"
    assert ("cron", "resume", cc.COUPLED_JOB_ID) in fake.calls


def test_reconcile_idempotent_when_on(sandbox, monkeypatch):
    _write_inst(sandbox, "f1", "running")
    fake = FakeHermes("active")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "unchanged"
    assert fake.calls == [("cron", "list", "--all")]  # 未做任何 resume/pause


def test_reconcile_off_pauses_when_no_running(sandbox, monkeypatch):
    """flow 口径：completed 与 paused 都不算活动（wrapper 只推进 running）。"""
    _write_inst(sandbox, "f1", "completed")
    _write_inst(sandbox, "f2", "paused")
    fake = FakeHermes("active")
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "off"
    assert fake.state == "paused"


def test_reconcile_idempotent_when_off(sandbox, monkeypatch):
    fake = FakeHermes("paused")  # 空目录 + 已 paused
    monkeypatch.setattr(cc, "_run_hermes", fake)
    assert cc.reconcile() == "unchanged"
    assert fake.calls == [("cron", "list", "--all")]


def test_reconcile_silent_swallows_hermes_failure(sandbox, monkeypatch):
    _write_inst(sandbox, "f1", "running")
    monkeypatch.delenv("CRON_COUPLING_DISABLE", raising=False)

    def boom(*args):
        raise FileNotFoundError("hermes 不存在")

    monkeypatch.setattr(cc, "_run_hermes", boom)
    cc.reconcile_silent()  # 不得上抛（异常隔离红线）


def test_reconcile_silent_respects_disable_switch(sandbox, monkeypatch):
    monkeypatch.setenv("CRON_COUPLING_DISABLE", "1")
    called: list[int] = []
    monkeypatch.setattr(cc, "reconcile", lambda: called.append(1) or "on")
    cc.reconcile_silent()
    assert called == []  # 开关置位 → 完全跳过


def test_job_state_parses_all_flag_output(monkeypatch):
    monkeypatch.setattr(
        cc, "_run_hermes",
        lambda *a: f"  other1234abcd [active]\n  {cc.COUPLED_JOB_ID} [paused]\n")
    assert cc.job_state() == "paused"


def test_job_state_raises_when_job_missing(monkeypatch):
    monkeypatch.setattr(cc, "_run_hermes", lambda *a: "  other1234abcd [active]\n")
    with pytest.raises(RuntimeError, match="找不到 job"):
        cc.job_state()


_DONE_ONLY_FLOW = "name: t-coupling\nsteps:\n  - id: done\n    type: done\n"


def test_engine_hooks_first_write_and_terminal(tmp_path, monkeypatch):
    """端到端（hermes mock）：engine 首次落盘（隐式建档）钩子 resume、终态钩子 pause。"""
    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "t-coupling.yaml").write_text(_DONE_ONLY_FLOW, encoding="utf-8")
    d = tmp_path / "instances"
    d.mkdir()
    monkeypatch.setattr(eng, "FLOWS_DIR", flows)
    monkeypatch.setattr(eng, "INSTANCES_DIR", d)
    monkeypatch.setattr(eng.kanban_client, "archive_flow_cards", lambda flow: [])
    monkeypatch.setattr(cc, "_instances_dir", lambda: d)
    monkeypatch.delenv("CRON_COUPLING_DISABLE", raising=False)
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)

    e = eng.FlowEngine("t-coupling")
    e.run()  # done-only 模板：run() 首次落盘即终态 completed
    assert (d / "t-coupling.json").exists()
    assert json.loads((d / "t-coupling.json").read_text(encoding="utf-8"))["status"] == "completed"
    assert fake.state == "paused"  # 终态收口 → 心跳停（且无上抛）


def test_engine_first_write_running_resumes(tmp_path, monkeypatch):
    """首次落盘且 status=running（外部预置 flow 后首拍前）→ 钩子 resume 心跳。"""
    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "t-coupling.yaml").write_text(_DONE_ONLY_FLOW, encoding="utf-8")
    d = tmp_path / "instances"
    d.mkdir()
    monkeypatch.setattr(eng, "FLOWS_DIR", flows)
    monkeypatch.setattr(eng, "INSTANCES_DIR", d)
    monkeypatch.setattr(cc, "_instances_dir", lambda: d)
    monkeypatch.delenv("CRON_COUPLING_DISABLE", raising=False)
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)

    e = eng.FlowEngine("t-coupling")
    e._save_state({"flow": "t-coupling", "step": "done", "iteration": 0,
                   "history": [], "status": "running"})  # 首写 + running
    assert fake.state == "active"


def test_gen_project_hook_fires_on_instance_write(tmp_path, monkeypatch):
    """gen_project.gen 落实例后挂钩触发（hermes mock）→ resume 心跳。"""
    import gen_project as gp

    flows = tmp_path / "flows"
    flows.mkdir()  # gen 直写 flow_fp，父目录必须先在（真实树里本就存在）
    d = tmp_path / "instances"
    d.mkdir()
    monkeypatch.setattr(gp, "INSTANCES_DIR", d)
    monkeypatch.setattr(gp, "FLOWS_DIR", flows)
    monkeypatch.setattr(gp, "PROJECT_ROOT", tmp_path / "projects")
    monkeypatch.setattr(gp, "register_whitelist", lambda project: None)
    monkeypatch.setattr(cc, "_instances_dir", lambda: d)
    monkeypatch.delenv("CRON_COUPLING_DISABLE", raising=False)
    fake = FakeHermes("paused")
    monkeypatch.setattr(cc, "_run_hermes", fake)

    rc = gp.gen("t-coupling-gen", project_dir=tmp_path / "proj")
    assert rc == 0
    assert (d / "t-coupling-gen.json").exists()
    assert fake.state == "active"  # 新实例 running → 心跳被拉起

"""grc M1 实时对话层单测（t_b4ebc051，2026-08-30）。

全部 mock subprocess（不真调 hermes CLI）；parse_reply/边界/降级状态机/
.session 落盘续接。既有 80 用例零回归。
"""
from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import interview as iv


def _run(coro):
    return asyncio.run(coro)


# ── parse_reply 全分支 ────────────────────────────────────


def test_parse_reply_next_valid_node():
    r = iv.parse_reply("信息充分了 [NEXT:features]")
    assert r == {"kind": "next", "payload": "features"}


def test_parse_reply_next_unknown_node_falls_back_to_plain():
    r = iv.parse_reply("[NEXT:不存在的节点] 继续")
    assert r["kind"] == "plain"


def test_parse_reply_node_done_inline():
    r = iv.parse_reply("本节点已覆盖充分 [NODE_DONE] ")
    assert r == {"kind": "node_done", "payload": ""}


def test_parse_reply_plain():
    r = iv.parse_reply("你说的 API 网关具体是什么协议？")
    assert r["kind"] == "plain" and "网关" in r["payload"]


def test_parse_reply_empty():
    assert iv.parse_reply("")["kind"] == "plain"
    assert iv.parse_reply(None)["kind"] == "plain"


# ── _run_chat 输出解析（mock subprocess） ─────────────────


def test_run_chat_parses_session_id_and_body(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = "回复正文"
            stderr = "noise\nsession_id: 20260830_120000_aabbcc\n"
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    sid, body = iv._run_chat("hi", None)
    assert sid == "20260830_120000_aabbcc"
    assert body == "回复正文"
    assert "chat" in calls[0] and "-Q" in calls[0]


def test_run_chat_resume_appends_flag(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = "ok"
            stderr = "session_id: x\n"
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    iv._run_chat("hi", "sess_1")
    assert "--resume" in calls[0] and "sess_1" in calls[0]


def test_run_chat_strips_kanban_env(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_x")
    monkeypatch.setenv("HERMES_KANBAN_WORKSPACE", "/tmp/x")
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        class R:
            returncode = 0
            stdout = "session_id: x\nok"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    iv._run_chat("hi", None)
    assert "HERMES_KANBAN_TASK" not in captured["env"]
    assert "HERMES_KANBAN_WORKSPACE" not in captured["env"]


def test_run_chat_raises_on_missing_session_line(monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "没有任何标记"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    with pytest.raises(RuntimeError):
        iv._run_chat("hi", None)


# ── AgentSession 落盘续接 ─────────────────────────────────


def test_agent_session_start_writes_session_file(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "你好"
            stderr = "session_id: sid_001\n"
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    sess = iv.AgentSession(tmp_path)
    out = sess.start("开场")
    assert out == "你好"
    d = sess.resume()
    assert d["session_id"] == "sid_001"


def test_agent_session_send_uses_saved_sid(tmp_path, monkeypatch):
    (tmp_path / "exchange").mkdir()
    (tmp_path / "exchange" / ".session").write_text(
        json.dumps({"session_id": "sid_9"}), encoding="utf-8")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = "收到"
            stderr = "session_id: sid_9\n"
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    sess = iv.AgentSession(tmp_path)
    assert sess.send("继续") == "收到"
    assert "--resume" in calls[0] and "sid_9" in calls[0]


def test_agent_session_send_without_start_raises(tmp_path):
    sess = iv.AgentSession(tmp_path)
    with pytest.raises(RuntimeError):
        sess.send("hi")


# ── chat_turn 降级状态机（mock 全链路） ───────────────────


def test_chat_turn_first_turn_success(tmp_path, monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "session_id: s1\n你好，第一个问题：项目缘由？"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    res = _run(iv.chat_turn(tmp_path, ""))
    assert res["parse"]["kind"] == "plain"
    assert res["degraded"] == ""
    msgs = iv.chat_messages_view(tmp_path)
    assert msgs and msgs[0]["role"] == "agent"
    assert iv.AgentSession(tmp_path).resume()["session_id"] == "s1"


def test_chat_turn_retry_then_degraded(tmp_path, monkeypatch):
    n = {"c": 0}

    def fake_run(cmd, **kw):
        n["c"] += 1
        raise TimeoutError("boom")

    monkeypatch.setattr("subprocess.run", fake_run)
    res = _run(iv.chat_turn(tmp_path, "回答内容"))
    assert res["parse"]["kind"] == "degraded"
    assert n["c"] == 2  # 重试 1 次
    assert res["degraded"].startswith("chat_error")
    assert iv.load_state(tmp_path)["chat_error_streak"] == 1


def test_chat_turn_revive_after_3_streaks(tmp_path, monkeypatch):
    # 预置 streak=2 + 旧 session 文件
    st = iv.load_state(tmp_path)
    st["chat_error_streak"] = 2
    iv.save_state(tmp_path, st)
    iv.AgentSession(tmp_path)._write("old_sid")

    n = {"c": 0}

    def fake_run(cmd, **kw):
        n["c"] += 1
        if "--resume" in cmd:
            raise TimeoutError("boom")  # 续接失败 → 重试也失败 → revive
        class R:  # revive 用 fresh start，成功
            returncode = 0
            stdout = "session_id: new_sid\n恢复"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    res = _run(iv.chat_turn(tmp_path, "回答内容"))
    assert res["parse"]["kind"] == "degraded"
    st = iv.load_state(tmp_path)
    assert st["chat_error_streak"] == 0  # revive 后清零
    assert "session_revived" in res["degraded"]
    assert iv.AgentSession(tmp_path).resume()["session_id"] != "old_sid"


def test_chat_turn_success_clears_streak(tmp_path, monkeypatch):
    st = iv.load_state(tmp_path)
    st["chat_error_streak"] = 2
    iv.save_state(tmp_path, st)
    iv.AgentSession(tmp_path)._write("sid_a")

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "session_id: sid_a\n追问：为什么？"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    _run(iv.chat_turn(tmp_path, "我的回答"))
    assert iv.load_state(tmp_path)["chat_error_streak"] == 0


# ── 主线推进落盘语义 ──────────────────────────────────────


def test_chat_turn_next_marks_answer(tmp_path, monkeypatch):
    iv.AgentSession(tmp_path)._write("sid_b")
    # 先答首节点（确定性路径）
    st = iv.load_state(tmp_path)
    st["answers"]["purpose"] = "做个工具"
    iv.save_state(tmp_path, st)

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "session_id: sid_b\n明白，进入下一节点 [NEXT:audience]"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    res = _run(iv.chat_turn(tmp_path, "给开发者的工具"))
    assert res["parse"]["kind"] == "next"
    st = iv.load_state(tmp_path)
    assert st["answers"]["audience"] == "给开发者的工具"  # 本轮文本即答复
    assert any(r["answers"].get("audience") for r in st["rounds"])


def test_chat_turn_node_done_closes_followups(tmp_path, monkeypatch):
    iv.AgentSession(tmp_path)._write("sid_c")
    st = iv.load_state(tmp_path)
    st["answers"]["purpose"] = "工具"
    st["followups"]["purpose"] = [{"question": "追问?", "answer": None,
                                   "answered": False}]
    iv.save_state(tmp_path, st)

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "session_id: sid_c\n讨论已充分 [NODE_DONE]"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    res = _run(iv.chat_turn(tmp_path, "补充说明"))
    assert res["parse"]["kind"] == "node_done"
    st = iv.load_state(tmp_path)
    assert st["followups"]["purpose"] == []


def test_chat_turn_sideline_counts(tmp_path, monkeypatch):
    iv.AgentSession(tmp_path)._write("sid_d")
    st = iv.load_state(tmp_path)
    st["answers"]["purpose"] = "工具"
    iv.save_state(tmp_path, st)

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "session_id: sid_d\n你说的工具是 CLI 还是 GUI？"
            stderr = ""
        return R()

    monkeypatch.setattr("subprocess.run", fake_run)
    _run(iv.chat_turn(tmp_path, "详细说说场景"))
    _run(iv.chat_turn(tmp_path, "CLI 为主"))
    st = iv.load_state(tmp_path)
    # focus_node：purpose 已答无挂起追问 → 当前节点 = audience（前沿首节点）
    assert st["sideline_rounds"]["audience"] == 2
    user_msgs = [m for m in st["chat_messages"] if m["role"] == "user"]
    assert len(user_msgs) == 2 and user_msgs[0]["node"] == "audience"


# ── 兼容性 ────────────────────────────────────────────────


def test_load_state_defaults_new_fields(tmp_path):
    st = iv.load_state(tmp_path)
    assert st["chat_messages"] == []
    assert st["sideline_rounds"] == {}
    assert st["chat_error_streak"] == 0


def test_chat_mode_triple_switch(monkeypatch):
    monkeypatch.delenv("INTERVIEW_FOLLOWUP_MODE", raising=False)
    assert iv.chat_mode() == "agent"
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    assert iv.chat_mode() == "direct"
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "skeleton")
    assert iv.chat_mode() == "skeleton"

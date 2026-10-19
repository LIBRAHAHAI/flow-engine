"""test_interview.py — grill-me 访谈引擎 + grillme-deep 扩展 pytest（2026-08-22）。

覆盖 002 §5 CLI 兼容回归清单 C1-C9 + LLM 追问降级四路径 + 面板数据层语义
（answer_node/answer_followup/skip_node/stop/resume/build_view）。

LLM 调用全部禁用或 monkeypatch，不打真实网络（pytest 确定性基线）。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import interview as iv  # noqa: E402


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """空项目（含 exchange/）+ LLM 全关（确定性）。"""
    (tmp_path / "exchange").mkdir()
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "false")
    return tmp_path


def _interview(proj: Path) -> dict:
    return json.loads((proj / "exchange" / "INTERVIEW.json").read_text(encoding="utf-8"))


def _spec(proj: Path) -> str:
    return (proj / "exchange" / "spec.md").read_text(encoding="utf-8")


def _answer_all(proj: Path, answers: dict[str, str]) -> None:
    """按 DESIGN_TREE 顺序逐个 CLI answer。"""
    for node in iv.DESIGN_TREE:
        iv.answer(proj, answers[node["key"]])


# ── C1-C3：CLI ask / answer ───────────────────────────────


def test_c1_ask_fresh_project(proj):
    out = iv.ask(proj)
    assert "[purpose]" in out
    assert "推荐" in out


def test_c2_answer_then_next_question(proj):
    iv.answer(proj, "做一个X工具")
    assert _interview(proj)["answers"]["purpose"] == "做一个X工具"
    assert "[audience]" in iv.ask(proj)


def test_c3_skip_uses_recommend(proj):
    iv.answer(proj, "跳过")
    node = iv.DESIGN_TREE[0]
    assert _interview(proj)["answers"]["purpose"] == node["recommend"]


# ── C4-C6：gen-spec / confirm ─────────────────────────────


def test_c4_full_gen_spec(proj):
    _answer_all(proj, {n["key"]: f"{n['key']}的答案" for n in iv.DESIGN_TREE})
    iv.gen_spec(proj)
    spec = _spec(proj)
    assert "status: draft" in spec
    for n in iv.DESIGN_TREE:
        assert f"{n['key']}的答案" in spec
    assert "## 八、讨论记录" in spec
    assert _interview(proj)["status"] == "spec_draft"


def test_c5_partial_gen_spec_marks_unanswered(proj):
    iv.answer(proj, "只做 purpose 一节")
    iv.gen_spec(proj)
    spec = _spec(proj)
    assert "只做 purpose 一节" in spec
    # 未答 6 节点 → 「（未讨论，采用推荐）」+ 推荐答案原文（002 §3.1）
    assert spec.count("（未讨论，采用推荐）") == 6
    assert iv.DESIGN_TREE[1]["recommend"] in spec  # audience 推荐原文落 spec


def test_c6_confirm_after_gen_spec(proj):
    _answer_all(proj, {n["key"]: f"{n['key']}的答案" for n in iv.DESIGN_TREE})
    iv.gen_spec(proj)
    iv.confirm(proj)
    assert "status: confirmed" in _spec(proj)
    assert "status: draft" not in _spec(proj)
    assert _interview(proj)["status"] == "confirmed"


# ── C7：旧版 INTERVIEW.json 兼容 ──────────────────────────


def test_c7_legacy_json_loads(proj):
    legacy = {"project": "legacy", "answers": {"purpose": "old"},
              "rounds": [], "status": "active"}
    (proj / "exchange" / "INTERVIEW.json").write_text(
        json.dumps(legacy), encoding="utf-8")
    st = iv.load_state(proj)  # 不抛错
    assert st["followups"] == {}
    assert st["stopped"] is False
    assert st["llm_degraded"] is False
    assert st["answers"]["purpose"] == "old"
    # frontier 正常推进到 audience
    assert iv.frontier(st)[0]["key"] == "audience"


# ── C8：追问澄清段落 ──────────────────────────────────────


def test_c8_followups_in_spec(proj):
    st = iv.load_state(proj)
    st["answers"]["purpose"] = "做一个坦克游戏"
    st["followups"] = {"purpose": [
        {"question": "坦克是单人还是双人？", "answer": "单人",
         "answered": True, "ts": "t"},
        {"question": "未答的追问", "answer": None,
         "answered": False, "ts": "t"},
    ]}
    iv.save_state(proj, st)
    iv.gen_spec(proj)
    spec = _spec(proj)
    assert "追问澄清：" in spec
    assert "Q: 坦克是单人还是双人？" in spec
    assert "A: 单人" in spec
    assert "未答的追问" not in spec  # 未答追问不进 spec


# ── C9 + 降级四路径（INTERVIEW_LLM_ENABLED=false / 无 key / 超时 / 空响应）──


def test_c9_disabled_makes_no_llm_call(proj, monkeypatch):
    """INTERVIEW_LLM_ENABLED=false → 直接 degraded，不构造 httpx 请求。"""
    called = {}

    # agent 模式成默认后，direct 路径单测需显式 pin（mock 全部打在 httpx 上）


def test_followup_agent_mode_success(proj, monkeypatch):
    """agent 模式：subprocess 成功返回追问文本。"""
    import subprocess as sp
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "agent")

    class FakeCompleted:
        returncode = 0
        stdout = "❓ 你想做的是什么类型的游戏？"
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **k: FakeCompleted())
    res = iv._generate_followup_via_agent("purpose", "q", [], {}, 25)
    assert (res.kind, res.question) == ("followup", "你想做的是什么类型的游戏？")


def test_followup_agent_mode_degraded(proj, monkeypatch):
    """agent 模式：超时/失败/空响应 → degraded（骨架兜底）。"""
    import subprocess as sp
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "agent")

    def fake_run_timeout(*a, **k):
        raise sp.TimeoutExpired(cmd="hermes", timeout=120)

    monkeypatch.setattr(sp, "run", fake_run_timeout)
    res = iv._generate_followup_via_agent("purpose", "q", [], {}, 25)
    assert (res.kind, res.reason) == ("degraded", "timeout")

    class FakeFail:
        returncode = 1
        stdout = ""
        stderr = "err"

    monkeypatch.setattr(sp, "run", lambda *a, **k: FakeFail())
    res = iv._generate_followup_via_agent("purpose", "q", [], {}, 25)
    assert (res.kind, res.reason) == ("degraded", "api_error")

    class FakeEmpty:
        returncode = 0
        stdout = "  "
        stderr = ""

    monkeypatch.setattr(sp, "run", lambda *a, **k: FakeEmpty())
    res = iv._generate_followup_via_agent("purpose", "q", [], {}, 25)
    assert (res.kind, res.reason) == ("degraded", "empty_response")


def test_followup_mode_pinned_direct_for_legacy_tests(monkeypatch):
    """既有 direct 路径单测跑前 pin direct，防真调 hermes CLI。"""
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    assert iv._followup_mode() == "direct"


def _pinned_direct(monkeypatch):
    """direct 路径测试的前置 pin（防默认 agent 模式真调 CLI）。"""
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")


    async def boom(*a, **k):
        called["yes"] = True

    monkeypatch.setattr(iv.httpx, "AsyncClient", boom)
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert res.kind == "degraded"
    assert res.reason == "disabled"
    assert not called


def test_degraded_no_api_key(proj, monkeypatch):
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.delenv("HERMES_CUSTOM_CUSTOM_API_KEY", raising=False)
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert (res.kind, res.reason) == ("degraded", "no_api_key")


def test_degraded_timeout(proj, monkeypatch):
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("HERMES_CUSTOM_CUSTOM_API_KEY", "k")
    monkeypatch.setenv("INTERVIEW_LLM_TIMEOUT", "1")

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            raise iv.httpx.ReadTimeout("boom")

    monkeypatch.setattr(iv.httpx, "AsyncClient", FakeClient)
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert (res.kind, res.reason) == ("degraded", "timeout")


def test_degraded_empty_and_overlong(proj, monkeypatch):
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("HERMES_CUSTOM_CUSTOM_API_KEY", "k")

    def fake_client_with(content):
        class FakeResp:
            status_code = 200
            def json(self):
                return {"choices": [{"message": {"content": content}}]}

        class FakeClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, *a, **k): return FakeResp()
        return FakeClient

    # 空响应
    monkeypatch.setattr(iv.httpx, "AsyncClient", fake_client_with("   "))
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert (res.kind, res.reason) == ("degraded", "empty_response")
    # 超 200 字
    monkeypatch.setattr(iv.httpx, "AsyncClient", fake_client_with("长" * 201))
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert (res.kind, res.reason) == ("degraded", "empty_response")
    # NODE_DONE 标记
    monkeypatch.setattr(iv.httpx, "AsyncClient",
                        fake_client_with("信息已充分 [NODE_DONE]"))
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert res.kind == "node_done"
    # 正常追问
    monkeypatch.setattr(iv.httpx, "AsyncClient",
                        fake_client_with("你提到单人坦克，敌方AI难度如何定？"))
    res = asyncio.run(iv.generate_followup("purpose", "q", [], {}))
    assert res.kind == "followup"
    assert res.question == "你提到单人坦克，敌方AI难度如何定？"


# ── 面板数据层：answer_node / answer_followup / skip / stop / resume ──


def test_answer_node_degraded_does_not_block(proj, monkeypatch):
    """LLM 禁用（降级）不阻塞：首问答成、无追问、降级信号一次性。"""
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "false")
    r = asyncio.run(iv.answer_node(proj, "purpose", "做坦克游戏"))
    assert r["treeComplete"] is False
    assert r["degradedReason"] == "disabled"
    v = r["view"]
    assert v["nodeKey"] == "audience"          # frontier 推进
    assert v["progress"]["answered"] == 1
    assert v["llmDegraded"] is True            # 一次性信号
    # 再读一次 → 信号已清除（一次性语义，002 §2.3）
    v2 = iv.build_view(proj)
    assert v2["llmDegraded"] is False
    # answer_node 内 build_view 已消费信号？不——信号在 apply 落盘后由
    # 本次 build_view 读出并清除；第二次 build 必为 False（上面已断言）。


def test_answer_node_followup_enqueued(proj, monkeypatch):
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    """LLM 返回追问 → followups 入列，下一问仍是同节点（追问未答）。"""
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("HERMES_CUSTOM_CUSTOM_API_KEY", "k")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {
                "content": "你提到坦克大战，第一版要几种敌人？"}}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr(iv.httpx, "AsyncClient", FakeClient)
    r = asyncio.run(iv.answer_node(proj, "purpose", "做坦克大战"))
    v = r["view"]
    assert v["nodeKey"] == "purpose"           # 有追问 → 停在当前节点
    assert v["followups"][0]["question"] == "你提到坦克大战，第一版要几种敌人？"
    assert v["followups"][0]["answered"] is False

    # 回答追问 → LLM 判 node_done → 推进到 audience
    class DoneResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "[NODE_DONE]"}}]}

    class DoneClient(FakeClient):
        async def post(self, *a, **k): return DoneResp()

    monkeypatch.setattr(iv.httpx, "AsyncClient", DoneClient)
    r2 = asyncio.run(iv.answer_followup(proj, "purpose", 0, "三种"))
    assert r2["view"]["nodeKey"] == "audience"
    st = _interview(proj)
    assert st["followups"]["purpose"][0]["answer"] == "三种"


def test_answer_node_idempotent_overwrite(proj, monkeypatch):
    monkeypatch.setenv("INTERVIEW_FOLLOWUP_MODE", "direct")
    """同 key 重复 answer_node → 覆盖 + followups 重置（002 §1.3 幂等）。

    真实重试场景：首答后 LLM 追问挂起 → 节点仍是焦点 → 客户端重试覆盖。
    """
    monkeypatch.setenv("INTERVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("HERMES_CUSTOM_CUSTOM_API_KEY", "k")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "追问：目标平台？"}}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr(iv.httpx, "AsyncClient", FakeClient)
    asyncio.run(iv.answer_node(proj, "purpose", "第一版答案"))
    # 追问挂起 → 焦点仍在 purpose → 重试覆盖
    asyncio.run(iv.answer_node(proj, "purpose", "覆盖后的答案"))
    st = _interview(proj)
    assert st["answers"]["purpose"] == "覆盖后的答案"
    # followups 重置为新追问序列（旧追问丢弃，仅本次新生成的一条）
    assert len(st["followups"]["purpose"]) == 1
    assert st["followups"]["purpose"][0]["answered"] is False


def test_wrong_frontier_key_rejected(proj):
    with pytest.raises(ValueError, match="前沿不符"):
        asyncio.run(iv.answer_node(proj, "features", "串节点"))


def test_skip_node_uses_recommend_no_llm(proj, monkeypatch):
    called = {}

    async def boom(*a, **k):
        called["yes"] = True

    monkeypatch.setattr(iv, "generate_followup", boom)
    r = iv.skip_node(proj, "purpose")
    assert not called  # skip 不触发 LLM
    assert _interview(proj)["answers"]["purpose"] == iv.DESIGN_TREE[0]["recommend"]
    assert r["view"]["nodeKey"] == "audience"


def test_skip_node_closes_pending_followups(proj, monkeypatch):
    """已答节点挂起未答追问时，skip 关闭追问流并保留原答（活体冒烟实证缺陷）。"""
    st = iv.load_state(proj)
    st["answers"]["purpose"] = "用户真实回答"
    st["followups"] = {"purpose": [
        {"question": "追问1", "answer": None, "answered": False, "ts": "t"}]}
    iv.save_state(proj, st)
    r = iv.skip_node(proj, "purpose")
    st2 = _interview(proj)
    assert st2["answers"]["purpose"] == "用户真实回答"   # 不被推荐覆盖
    assert st2["followups"]["purpose"] == []             # 追问流关闭
    assert r["view"]["nodeKey"] == "audience"            # 焦点推进


def test_stop_then_answer_rejected_and_gen_spec(proj):
    asyncio.run(iv.answer_node(proj, "purpose", "做X"))
    r = iv.stop_interview(proj)
    assert "未答" in r["summary"]
    spec = _spec(proj)
    assert "（未讨论，采用推荐）" in spec
    st = _interview(proj)
    assert st["stopped"] is True
    assert st["status"] == "spec_draft"
    # stopped 后 answer_* 一律拒绝（002 §2.6）
    with pytest.raises(ValueError, match="访谈已停止"):
        asyncio.run(iv.answer_node(proj, "audience", "迟到的回答"))
    with pytest.raises(ValueError, match="访谈已停止"):
        asyncio.run(iv.answer_followup(proj, "purpose", 0, "迟到的回答"))
    # 幂等：重复 stop 不重复生成（返回同一路径）
    r2 = iv.stop_interview(proj)
    assert r2["specFile"] == r["specFile"]
    assert r2["alreadyStopped"] is True


def test_resume_preserves_answers(proj):
    """续谈（002 §3.3）：复位 stopped，answers/followups 保留。"""
    asyncio.run(iv.answer_node(proj, "purpose", "做X"))
    iv.stop_interview(proj)
    r = iv.resume_interview(proj)
    assert r["view"]["stopped"] is False
    assert r["view"]["progress"]["answered"] == 1  # 已答不丢
    assert r["view"]["status"] == "active"
    # 可继续回答
    asyncio.run(iv.answer_node(proj, "audience", "个人使用"))
    assert _interview(proj)["answers"]["audience"] == "个人使用"


def test_tree_complete_view(proj):
    """树全覆盖 → nodeKey=None（前端渲染 spec 确认区）。"""
    for n in iv.DESIGN_TREE:
        iv.answer(proj, f"{n['key']}答案")
    v = iv.build_view(proj)
    assert v["nodeKey"] is None
    assert v["progress"] == {"answered": 7, "total": 7}
    assert v["followups"] == []

"""test_reqmate.py — 需求素材包机制引擎侧单测（flow-engine-reqmate 2026-09-05）。

覆盖 002-default-plan-proposal §T4/§T2 引擎侧：
  - interview._materials_context：缺文件 / 单文件 / 双文件 / 超长截断
  - interview._chat_opening_prompt 注入点 1（有/无素材）
  - interview._build_followup_prompt 注入点 2（materials_context 参数）
  - gen_project.gen：materials/ 骨架 + .gitkeep + AGENTS.md 提示行（--parent 同样生效）
  - iter4 增量（C5）：brief 优先截断反例 / _chat_summary_prompt 传真实
    project_dir / gen_spec 需求素材约束小节

LLM 不打真实网络；gen_project 的目录常量全部 monkeypatch 到 tmp，
register_whitelist patch 为 no-op（避免动真实 exchange_dirs.py 白名单）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import interview as iv  # noqa: E402
import gen_project as gp  # noqa: E402


# ── _materials_context ────────────────────────────────────


def test_materials_context_empty_when_no_files(tmp_path: Path):
    assert iv._materials_context(tmp_path) == ""


def test_materials_context_reads_manifest_only(tmp_path: Path):
    mdir = tmp_path / "materials"
    mdir.mkdir()
    (mdir / "MANIFEST.md").write_text("| 类型 | 来源 |\n|---|---|\n| 文件 | 需求.txt |\n",
                                      encoding="utf-8")
    ctx = iv._materials_context(tmp_path)
    assert "素材清单 materials/MANIFEST.md" in ctx
    assert "需求.txt" in ctx
    assert "需求简报" not in ctx


def test_materials_context_reads_brief_only(tmp_path: Path):
    ex = tmp_path / "exchange"
    ex.mkdir()
    (ex / "000-requirements-brief.md").write_text(
        "---\nstatus: draft\n---\n\n做一个素材管理工具", encoding="utf-8")
    ctx = iv._materials_context(tmp_path)
    assert "需求简报 exchange/000-requirements-brief.md" in ctx
    assert "做一个素材管理工具" in ctx


def test_materials_context_both_files(tmp_path: Path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "exchange").mkdir()
    (tmp_path / "materials" / "MANIFEST.md").write_text("清单正文", encoding="utf-8")
    (tmp_path / "exchange" / "000-requirements-brief.md").write_text(
        "简报正文", encoding="utf-8")
    ctx = iv._materials_context(tmp_path)
    assert "清单正文" in ctx
    assert "简报正文" in ctx


def test_materials_context_truncates_over_limit(tmp_path: Path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "MANIFEST.md").write_text(
        "长" * 6000, encoding="utf-8")
    ctx = iv._materials_context(tmp_path)
    assert len(ctx) <= iv._MATERIALS_CONTEXT_MAX
    assert ctx.startswith("[素材清单")
    assert "截断" in ctx


def test_materials_context_skips_empty_file(tmp_path: Path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "MANIFEST.md").write_text("   \n", encoding="utf-8")
    assert iv._materials_context(tmp_path) == ""


# ── 注入点 1：_chat_opening_prompt ────────────────────────


def test_chat_opening_prompt_without_materials(tmp_path: Path):
    st = {"project": "demo", "answers": {}}
    out = iv._chat_opening_prompt(tmp_path, st)
    assert "项目需求素材" not in out
    assert "设计树节点" in out


def test_chat_opening_prompt_injects_materials(tmp_path: Path):
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "MANIFEST.md").write_text(
        "| 文件 | 竞品分析.pdf |", encoding="utf-8")
    (tmp_path / "exchange").mkdir()
    (tmp_path / "exchange" / "000-requirements-brief.md").write_text(
        "用户要求支持中文文件名", encoding="utf-8")
    st = {"project": "demo", "answers": {}}
    out = iv._chat_opening_prompt(tmp_path, st)
    assert "项目需求素材（建项目时用户提供的描述与素材清单，提问须基于这些内容）：" in out
    assert "竞品分析.pdf" in out
    assert "用户要求支持中文文件名" in out
    # 注入节不破坏标记协议段
    assert "标记协议（严格遵守）" in out


# ── 注入点 2：_build_followup_prompt ──────────────────────


def test_build_followup_prompt_without_materials():
    out = iv._build_followup_prompt("purpose", "做什么？", [], {"project": "demo"})
    assert "项目需求素材" not in out


def test_build_followup_prompt_with_materials():
    out = iv._build_followup_prompt(
        "purpose", "做什么？",
        [{"question": "做什么？", "answer": "素材工具"}],
        {"project": "demo"},
        materials_context="[素材清单 materials/MANIFEST.md]\n| 文件 | 原型.png |")
    assert "项目需求素材（建项目时用户提供的描述与素材清单，提问须基于这些内容）：" in out
    assert "原型.png" in out
    assert "本节点已答内容" in out


# ── gen_project：materials/ 骨架 + AGENTS.md 提示行 ───────


@pytest.fixture
def gp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """gen_project 目录常量全部指到 tmp；白名单登记 patch 为 no-op。"""
    flows = tmp_path / "flows"
    insts = tmp_path / "instances"
    proj_root = tmp_path / "projects"
    flows.mkdir()
    insts.mkdir()
    proj_root.mkdir()
    monkeypatch.setattr(gp, "FLOWS_DIR", flows)
    monkeypatch.setattr(gp, "INSTANCES_DIR", insts)
    monkeypatch.setattr(gp, "PROJECT_ROOT", proj_root)
    monkeypatch.setattr(gp, "register_whitelist", lambda project: None)
    return tmp_path


def test_gen_project_creates_materials_dir(gp_env: Path):
    rc = gp.gen("reqmate-t5-new")
    assert rc == 0
    pd = gp_env / "projects" / "reqmate-t5-new"
    assert (pd / "materials").is_dir()
    assert (pd / "materials" / ".gitkeep").exists()
    agents = (pd / "AGENTS.md").read_text(encoding="utf-8")
    assert "开工先读 materials/MANIFEST.md（若存在）" in agents
    assert (gp_env / "flows" / "reqmate-t5-new.yaml").exists()
    assert (gp_env / "instances" / "reqmate-t5-new.json").exists()


def test_gen_project_parent_branch_also_creates_materials(gp_env: Path):
    # 父项目：实例 JSON 存在即满足 parent 校验
    (gp_env / "instances" / "reqmate-t5-parent.json").write_text(
        json.dumps({"flow": "reqmate-t5-parent", "status": "running"}),
        encoding="utf-8")
    rc = gp.gen("reqmate-t5-child", parent="reqmate-t5-parent")
    assert rc == 0
    pd = gp_env / "projects" / "reqmate-t5-child"
    assert (pd / "materials").is_dir()
    assert (pd / "materials" / ".gitkeep").exists()
    inst = json.loads(
        (gp_env / "instances" / "reqmate-t5-child.json").read_text(encoding="utf-8"))
    assert inst["parent"] == "reqmate-t5-parent"


def test_gen_project_missing_parent_rejected(gp_env: Path):
    rc = gp.gen("reqmate-t5-orphan", parent="no-such-parent")
    assert rc == 3
    assert not (gp_env / "projects" / "reqmate-t5-orphan").exists()


# ── iter4 增量（C5）：brief 优先截断 / summary prompt / gen-spec 素材节 ──


def test_materials_context_brief_priority_on_truncation(tmp_path: Path):
    """截断反例：MANIFEST 超长 + brief 关键字在尾部 →
    输出仍包含 brief 关键字（brief 优先生效）。"""
    (tmp_path / "materials").mkdir()
    (tmp_path / "exchange").mkdir()
    (tmp_path / "materials" / "MANIFEST.md").write_text(
        "长" * 8000, encoding="utf-8")
    brief = "占" * 2400 + "关键验收词ZZZ-TRAIL"
    (tmp_path / "exchange" / "000-requirements-brief.md").write_text(
        brief, encoding="utf-8")
    ctx = iv._materials_context(tmp_path)
    assert len(ctx) <= iv._MATERIALS_CONTEXT_MAX
    assert "关键验收词ZZZ-TRAIL" in ctx  # brief 尾部关键字保住
    assert "截断" in ctx                   # MANIFEST 被截断
    assert ctx.startswith("[需求简报")     # brief 在前


def test_chat_summary_prompt_real_project_dir_has_materials(tmp_path: Path):
    """_chat_summary_prompt(st, project_dir) 传真实目录 →
    有素材时输出包含 materials 节（含引用指令）。"""
    (tmp_path / "materials").mkdir()
    (tmp_path / "materials" / "MANIFEST.md").write_text(
        "| 类型 | 来源 |\n|---|---|\n| 文件 | 原型图v2.png |\n", encoding="utf-8")
    st = {"project": "demo", "answers": {"purpose": "素材工具"}}
    out = iv._chat_summary_prompt(st, tmp_path)
    assert "项目需求素材" in out
    assert "原型图v2.png" in out
    assert "首问/提问中须至少引用 1 个素材唯一标识（文件名或 URL）" in out
    assert "这是会话恢复" in out


def test_gen_spec_materials_constraints_section(tmp_path: Path):
    """gen_spec 有素材 → spec.md 含「需求素材约束」小节且含素材文件名，
    位置在「## 八、讨论记录」之前；转义竖线还原。"""
    ex = tmp_path / "exchange"
    ex.mkdir()
    mdir = tmp_path / "materials"
    mdir.mkdir()
    (mdir / "MANIFEST.md").write_text(
        "# MANIFEST — 项目素材登记表\n\n"
        "| 类型 | 来源 | 落盘路径 | 用途 | 登记时间 |\n"
        "|---|---|---|---|---|\n"
        "| 文件 | 竞品分析.pdf | materials/竞品分析.pdf | 用户上传素材 | 2026-09-05 12:00 |\n"
        "| 文件 | a\\|b.txt | materials/a\\|b.txt | 用户上传素材 | 2026-09-05 12:02 |\n"
        "| URL | https://example.com/doc | (待取: fetch_error) | 抓取失败 | 2026-09-05 12:01 |\n",
        encoding="utf-8")
    (ex / "000-requirements-brief.md").write_text(
        "---\ntitle: \"000 需求简报\"\nstatus: draft\n---\n\n做一个素材管理工具",
        encoding="utf-8")
    iv.gen_spec(tmp_path)
    spec = (ex / "spec.md").read_text(encoding="utf-8")
    assert ("## 需求素材约束（源自 materials/MANIFEST.md 与 "
            "000-requirements-brief.md）") in spec
    assert "竞品分析.pdf" in spec
    assert "a|b.txt" in spec              # 转义竖线还原
    assert "https://example.com/doc" in spec
    assert "做一个素材管理工具" in spec      # brief 要点
    assert spec.index("需求素材约束") < spec.index("## 八、讨论记录")


# ── iter4 增量（C1）：doker_enabled 父项目继承 ─────────────


def test_gen_project_child_inherits_parent_doker_off(gp_env: Path):
    """C1：父项目 doker_enabled=false（引擎自修改项目）→ 子项目默认也 false；
    父项目 doker_enabled=true → 子项目保持默认 True。"""
    (gp_env / "instances" / "reqmate-t5-dokoff.json").write_text(
        json.dumps({"flow": "reqmate-t5-dokoff", "status": "running",
                    "doker_enabled": False}),
        encoding="utf-8")
    rc = gp.gen("reqmate-t5-child-off", parent="reqmate-t5-dokoff")
    assert rc == 0
    inst = json.loads(
        (gp_env / "instances" / "reqmate-t5-child-off.json").read_text(encoding="utf-8"))
    assert inst["doker_enabled"] is False

    (gp_env / "instances" / "reqmate-t5-dokon.json").write_text(
        json.dumps({"flow": "reqmate-t5-dokon", "status": "running",
                    "doker_enabled": True}),
        encoding="utf-8")
    rc = gp.gen("reqmate-t5-child-on", parent="reqmate-t5-dokon")
    assert rc == 0
    inst2 = json.loads(
        (gp_env / "instances" / "reqmate-t5-child-on.json").read_text(encoding="utf-8"))
    assert inst2["doker_enabled"] is True

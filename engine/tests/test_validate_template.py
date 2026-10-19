"""test_validate_template.py — 模板校验器测试（v1.1, 2026-08-16）。

覆盖：flow 模板校验 / roadmap 模板校验（v1.1 新增）/ --all 全扫（路径修复回归）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import validate_template as vt  # noqa: E402

REAL_FLOWS = vt.FLOWS_DIR


@pytest.fixture
def tmp_flows(tmp_path: Path):
    """隔离环境：把 FLOWS_DIR 指到临时目录，测完恢复。"""
    vt.FLOWS_DIR = tmp_path
    yield tmp_path
    vt.FLOWS_DIR = REAL_FLOWS


def _write(fp: Path, content: str):
    fp.write_text(content, encoding="utf-8")


def test_flow_template_ok(tmp_flows):
    fp = tmp_flows / "good.yaml"
    _write(fp, """
name: good
steps:
  - id: p
    type: decision
    question: "ok?"
    next: d
  - id: d
    type: done
""")
    assert vt.validate_file(fp) == []


def test_flow_template_bad_type(tmp_flows):
    fp = tmp_flows / "bad.yaml"
    _write(fp, """
name: bad
steps:
  - id: x
    type: magic
""")
    errs = vt.validate_file(fp)
    assert any("非法 type" in e for e in errs)


def test_roadmap_template_ok(tmp_flows):
    """v1.1: stages 模板（gen_project.py 消费）应通过独立校验。"""
    fp = tmp_flows / "roadmap.yaml"
    _write(fp, """
name: roadmap-standard
stages:
  - id: charter
    name: "立项"
    action: dispatch
    desc: "起草四件套"
    next: review
  - id: review
    name: "双审"
    action: wait
    desc: "回执落盘"
    next: done
  - id: done
    name: "完成"
    action: done
    desc: "流程完成"
""")
    assert vt.validate_file(fp) == []


def test_roadmap_template_missing_field(tmp_flows):
    fp = tmp_flows / "roadmap-bad.yaml"
    _write(fp, """
name: roadmap-bad
stages:
  - id: charter
    action: dispatch
    next: done
  - id: done
    name: "完成"
    action: done
    desc: "流程完成"
""")
    errs = vt.validate_file(fp)
    assert any("缺 name" in e for e in errs)


def test_roadmap_template_bad_next(tmp_flows):
    fp = tmp_flows / "roadmap-next.yaml"
    _write(fp, """
name: roadmap-next
stages:
  - id: a
    name: "A"
    action: dispatch
    desc: "阶段A"
    next: no-such-stage
  - id: b
    name: "B"
    action: done
    desc: "阶段B"
""")
    errs = vt.validate_file(fp)
    assert any("指向不存在的阶段" in e for e in errs)


def test_all_sweep_real_flows():
    """回归（路径修复）：--all 全扫真实 flows/，全部通过。"""
    files = sorted(vt.FLOWS_DIR.glob("*.yaml"))
    assert files, "flows/ 应有模板文件"
    all_errs = []
    for fp in files:
        errs = vt.validate_file(fp)
        if errs:
            all_errs.extend(errs)
    assert not all_errs, f"flows/ 存在校验失败: {all_errs}"

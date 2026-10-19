"""test_materials.py — 需求素材端点单测（flow-engine-reqmate 2026-09-05）。

覆盖 002-default-plan-proposal §T1 面板后端：
  - 正常文件上传 + 中文文件名 UTF-8 端到端
  - 文件夹打包上传（相对路径在 filename 字段，递归落盘）
  - 累计上限（monkeypatch _MATERIALS_LIMIT_BYTES 到小值，超限只登记不拷贝）
  - URL 抓取失败登记「待取」（mock 网络，不抛错）/ 成功落盘
  - 404 口径（无实例 JSON 且 PROJECT_ROOT 下无目录）
  - description → exchange/000-requirements-brief.md（新建 + 追加不覆盖）
  - iter4 增量：failed_items 结构化（fetch_error / over_limit / 空列表）
    + MANIFEST 单元格 `|` 转义（列数不乱）

隔离：monkeypatch api 模块的 PROJECT_ROOT / INSTANCES_DIR 到 tmp；
URL 抓取函数 _fetch_url_text monkeypatch 掉，不打真实网络。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import api as api_mod  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离项目根 + 实例目录；返回 (client, project_root, instances)。"""
    proj_root = tmp_path / "projects"
    insts = tmp_path / "instances"
    proj_root.mkdir()
    insts.mkdir()
    monkeypatch.setattr(api_mod, "PROJECT_ROOT", proj_root)
    monkeypatch.setattr(api_mod, "INSTANCES_DIR", insts)
    return TestClient(api_mod.app), proj_root, insts


def _mk_project(proj_root: Path, insts: Path, name: str) -> Path:
    """建一个存在性成立的项目（实例 JSON + 目录双立）。"""
    pd = proj_root / name
    (pd / "exchange").mkdir(parents=True)
    (insts / f"{name}.json").write_text(
        f'{{"flow": "{name}", "status": "running", "step": "discuss"}}',
        encoding="utf-8")
    return pd


# ── 正常上传 + 中文文件名 ─────────────────────────────────


def test_upload_file_with_chinese_name(env):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-cn")
    r = client.post(
        "/api/flows/proj-cn/materials",
        files={"files": ("需求说明.txt", "这是一个中文需求 🎯".encode("utf-8"),
                         "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["saved"] == 1
    fp = pd / "materials" / "需求说明.txt"
    assert fp.exists()
    assert fp.read_text(encoding="utf-8") == "这是一个中文需求 🎯"
    manifest = (pd / "materials" / "MANIFEST.md").read_text(encoding="utf-8")
    assert "需求说明.txt" in manifest
    assert "| 类型 | 来源 | 落盘路径 | 用途 | 登记时间 |" in manifest


def test_upload_folder_pack_relative_path(env):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-dir")
    r = client.post(
        "/api/flows/proj-dir/materials",
        files=[
            ("files", ("docs/设计稿/原型说明.md", b"# proto", "text/markdown")),
            ("files", ("docs/readme.txt", b"hello", "text/plain")),
        ],
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    assert (pd / "materials" / "docs" / "设计稿" / "原型说明.md").read_bytes() == b"# proto"
    assert (pd / "materials" / "docs" / "readme.txt").read_bytes() == b"hello"


def test_upload_rejects_path_traversal(env):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-sec")
    r = client.post(
        "/api/flows/proj-sec/materials",
        files={"files": ("../../evil.txt", b"x", "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200
    assert not (pd.parent / "evil.txt").exists()
    assert not (pd.parent.parent / "evil.txt").exists()
    # 非法文件名不落盘，只在 MANIFEST 登记拒绝原因
    assert list((pd / "materials").glob("*evil*")) == []
    manifest = (pd / "materials" / "MANIFEST.md").read_text(encoding="utf-8")
    assert "拒绝" in manifest


# ── 累计上限：超限只登记不拷贝 ────────────────────────────


def test_over_limit_registers_but_not_copies(env, monkeypatch):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-limit")
    monkeypatch.setattr(api_mod, "_MATERIALS_LIMIT_BYTES", 10)
    r = client.post(
        "/api/flows/proj-limit/materials",
        files=[
            ("files", ("small.txt", b"12345", "text/plain")),
            ("files", ("big.bin", b"x" * 64, "application/octet-stream")),
        ],
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 1 and body["skipped_over_limit"] == 1
    assert (pd / "materials" / "small.txt").exists()
    assert not (pd / "materials" / "big.bin").exists()
    manifest = (pd / "materials" / "MANIFEST.md").read_text(encoding="utf-8")
    assert "超限未落盘" in manifest


# ── URL 抓取：失败登记待取 / 成功落盘 ─────────────────────


def test_url_fetch_failure_registers_pending(env, monkeypatch):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-url-fail")

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(api_mod, "_fetch_url_text", boom)
    r = client.post(
        "/api/flows/proj-url-fail/materials",
        data={"urls": "https://example.com/a\nhttps://example.com/b",
              "description": ""},
    )
    assert r.status_code == 200, r.text  # 失败不抛错
    body = r.json()
    assert body["urls_pending"] == 2 and body["saved"] == 0
    manifest = (pd / "materials" / "MANIFEST.md").read_text(encoding="utf-8")
    assert manifest.count("待取") >= 2
    assert "https://example.com/a" in manifest


def test_url_fetch_success_writes_md(env, monkeypatch):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-url-ok")
    monkeypatch.setattr(api_mod, "_fetch_url_text",
                        lambda url: f"# 正文 from {url}")
    r = client.post(
        "/api/flows/proj-url-ok/materials",
        data={"urls": "https://example.com/doc", "description": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["urls_pending"] == 0
    hits = list((pd / "materials").glob("example.com-doc*.md"))
    assert len(hits) == 1
    assert "正文 from https://example.com/doc" in hits[0].read_text(encoding="utf-8")


# ── 404 口径（同 create_flow parent 校验） ────────────────


def test_materials_404_when_project_missing(env):
    client, proj_root, insts = env
    r = client.post(
        "/api/flows/ghost-project/materials",
        files={"files": ("a.txt", b"x", "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 404


def test_materials_ok_with_dir_only_no_instance(env):
    """无实例 JSON 但 PROJECT_ROOT 下有目录 → 存在（同 parent 校验口径）。"""
    client, proj_root, insts = env
    (proj_root / "legacy-proj").mkdir()
    r = client.post(
        "/api/flows/legacy-proj/materials",
        files={"files": ("a.txt", b"x", "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text


# ── description → brief 落盘 ─────────────────────────────


def test_description_creates_brief(env):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-brief")
    r = client.post(
        "/api/flows/proj-brief/materials",
        data={"urls": "", "description": "做一个素材管理工具，支持中文文件名"},
    )
    assert r.status_code == 200, r.text
    fp = pd / "exchange" / "000-requirements-brief.md"
    assert fp.exists()
    txt = fp.read_text(encoding="utf-8")
    assert "status: draft" in txt
    assert "做一个素材管理工具，支持中文文件名" in txt


def test_description_appends_not_overwrites(env):
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-brief2")
    client.post("/api/flows/proj-brief2/materials",
                data={"urls": "", "description": "第一段描述"})
    r = client.post("/api/flows/proj-brief2/materials",
                    data={"urls": "", "description": "第二段补充描述"})
    assert r.status_code == 200
    txt = (pd / "exchange" / "000-requirements-brief.md").read_text(encoding="utf-8")
    assert "第一段描述" in txt  # 原文未覆盖
    assert "## 补充" in txt
    assert "第二段补充描述" in txt


# ── iter4 增量：failed_items 结构化 + MANIFEST 转义 ──────


def test_url_failure_failed_items_fetch_error(env, monkeypatch):
    """C4：URL 抓取失败 → 响应 failed_items[0].reason=='fetch_error'，
    MANIFEST 结构化登记（失败类别/来源/登记时间 ISO）。"""
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-url-fi")

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(api_mod, "_fetch_url_text", boom)
    r = client.post(
        "/api/flows/proj-url-fi/materials",
        data={"urls": "https://example.com/x", "description": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["urls_pending"] == 1  # 旧计数字段保留
    assert body["failed_items"], "URL 抓取失败必须登记 failed_items"
    it = body["failed_items"][0]
    assert it["reason"] == "fetch_error"
    assert it["type"] == "url"
    assert it["source"] == "https://example.com/x"
    assert it["ts"]  # ISO 登记时间
    manifest = (pd / "materials" / "MANIFEST.md").read_text(encoding="utf-8")
    assert "fetch_error" in manifest
    assert "https://example.com/x" in manifest


def test_over_limit_failed_items(env, monkeypatch):
    """C4：文件超限 → failed_items 含 reason=='over_limit'（type=file）。"""
    client, proj_root, insts = env
    _mk_project(proj_root, insts, "proj-limit-fi")
    monkeypatch.setattr(api_mod, "_MATERIALS_LIMIT_BYTES", 10)
    r = client.post(
        "/api/flows/proj-limit-fi/materials",
        files=[("files", ("big.bin", b"x" * 64, "application/octet-stream"))],
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    overs = [it for it in body["failed_items"] if it["reason"] == "over_limit"]
    assert overs, "超限文件必须登记 failed_items"
    assert overs[0]["type"] == "file"
    assert overs[0]["source"] == "big.bin"
    assert overs[0]["ts"]


def test_success_response_has_empty_failed_items(env):
    """C7/V3：无失败时响应体仍带 failed_items（空列表）。"""
    client, proj_root, insts = env
    _mk_project(proj_root, insts, "proj-ok-fi")
    r = client.post(
        "/api/flows/proj-ok-fi/materials",
        files={"files": ("ok.txt", b"hello", "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 1
    assert body["failed_items"] == []


def test_manifest_pipe_in_filename_escaped(env, monkeypatch):
    """C4：文件名含 `|` → MANIFEST 单元格转义，读回表格列数不乱。

    走超限路径登记（`|` 在 Windows 是非法文件名字符，不能真落盘）。
    """
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-pipe")
    monkeypatch.setattr(api_mod, "_MATERIALS_LIMIT_BYTES", 10)
    r = client.post(
        "/api/flows/proj-pipe/materials",
        files={"files": ("a|b.txt", b"x" * 32, "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    manifest = (pd / "materials" / "MANIFEST.md").read_text(encoding="utf-8")
    assert "a\\|b.txt" in manifest  # 转义生效
    # 读回：按未转义 `|` 切分，数据行仍为 5 列（类型/来源/路径/用途/登记时间）
    data_rows = [ln for ln in manifest.splitlines()
                 if ln.startswith("|") and "---" not in ln and "类型" not in ln]
    assert data_rows, "MANIFEST 应有数据行"
    for ln in data_rows:
        cells = re.split(r"(?<!\\)\|", ln.strip().strip("|"))
        assert len(cells) == 5, f"表格列数乱了: {ln!r}"


def test_write_error_registers_failed_item(env):
    """C4：文件系统拒绝写入（落盘目标已是目录）→ 不 500，
    failed_items 含 reason=='write_error'（平台无关复现）。"""
    client, proj_root, insts = env
    pd = _mk_project(proj_root, insts, "proj-werr")
    (pd / "materials" / "sub").mkdir(parents=True)
    r = client.post(
        "/api/flows/proj-werr/materials",
        files={"files": ("sub", b"data", "text/plain")},
        data={"urls": "", "description": ""},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    errs = [it for it in body["failed_items"] if it["reason"] == "write_error"]
    assert errs, "写入失败必须登记 failed_items"
    assert errs[0]["type"] == "file" and errs[0]["source"] == "sub"
    assert errs[0]["ts"]

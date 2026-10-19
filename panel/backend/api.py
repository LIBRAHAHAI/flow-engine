"""
Flow Engine API Server — FastAPI 后端 (M4 → v2 → 项目化改造 2026-08-23)

v1 现有 5 端点（行为不变，仅增量扩展）：
  GET    /flows              — 列出所有项目实例
  GET    /flows/{flow_id}     — 获取项目详情
  POST   /flows/{flow_id}/decide — 拍板决策
  POST   /flows               — 创建新项目（v2.0: 支持 parent 建改进子项目）
  GET    /flows/{flow_id}/history — 获取项目历史

v2 新增 4 端点（flow-engine-frontend-v2 step 8；
契约对齐前端 api/client.ts + types/index.ts + 006 §4.1）：
  GET    /flows/{flow_id}/logs          — 日志列表（返回 LogEntry[]，可选 level/limit/offset）
  DELETE /flows/{flow_id}               — 删除项目实例 + 关联文件（不删模板 YAML）
  GET    /flows/{flow_id}/grill-context — grill-me 上下文（decision: turns 平铺；
                                          discuss: interview 对象，grillme-deep 002 §1.2）
  POST   /flows/{flow_id}/grill-turn    — grill-me 单轮提交（decision: confirm/edit/skip；
                                          discuss: answer_node/answer_followup/skip_node）

grillme-deep 新增 2 端点（002 §1.4/§1.5，discuss 设计树访谈）：
  POST   /flows/{flow_id}/grill-stop    — 停止追问 → 冻结 + 生成 spec（未答=采用推荐，幂等）
  POST   /flows/{flow_id}/grill-confirm — 确认共识 → spec confirmed → 引擎 discuss 步推进
                                          （engine.py 零改动，_do_discuss 检测 confirmed）

项目化改造新增 3 端点（2026-08-23：层级 + 流程模板库；UI 文案「流程」→「项目」，
代码路径 /api/flows 保持不变避免破坏性重构）：
  GET    /api/flow-templates              — 执行流程模板列表（meta 段驱动）
  POST   /api/flows/{id}/template         — 给项目绑定执行流程模板（立项后选执行流程）
  GET    /api/flow-templates/current/{id} — 查询项目当前绑定的模板

数据源：
  - <engine>/instances/*.json
  - <engine>/flows/*.yaml

运行：
  cd <repo>/panel/backend
  python api.py
  → http://localhost:8650/api
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── 路径常量 ──────────────────────────────────────────────
# 可迁移部署（2026-08-24 工具包整合）：三级解析——环境变量 > 包内布局自动推断 >
# 原生开发布局缺省值。打包到任意机器解压即用，无需任何配置。
_API_DIR = Path(__file__).resolve().parent          # …/panel/backend
_PKG_ROOT = _API_DIR.parent.parent                   # 工具包根（panel 的上一级）；
                                                     # 原生布局下这两级也无 engine//projects/，推断自然回落缺省值


def _infer_engine_home() -> Path:
    env = os.environ.get("FLOW_ENGINE_HOME")
    if env:
        return Path(env)
    # 开发环境一律指向主库（2026-09-05 reqmate 实测 P0：包内布局推断命中
    # setup/engine 再生副本——主库 17 个历史实例全部不可见，TANK 却落 setup，
    # 与主库心跳（engine_heartbeat_wrapper 读主库 instances）割裂。
    # 包内布局仅在 setup/engine 无主库实例时生效（便携部署场景）。
    main_engine = Path(os.environ.get(
        "FLOW_ENGINE_DIR", str(_PKG_ROOT / "engine")))
    if (main_engine / "instances").is_dir() and any((main_engine / "instances").glob("*.json")):
        return main_engine
    if (_PKG_ROOT / "engine").is_dir():  # 包内布局：<包>/engine/
        return _PKG_ROOT / "engine"
    return main_engine  # 原生开发布局


def _infer_projects_root() -> Path:
    env = os.environ.get("FLOW_PROJECTS_ROOT")
    if env:
        return Path(env)
    # 与 ENGINE_HOME 同源（2026-09-05）：开发环境主库实例存在时项目根也是
# 与 projects root 同源，避免 gen 骨架与素材端点写两个目录。
    main_engine = Path(os.environ.get(
        "FLOW_ENGINE_DIR", str(_PKG_ROOT / "engine")))
    if (main_engine / "instances").is_dir() and any((main_engine / "instances").glob("*.json")):
        return Path(os.environ.get("FLOW_PROJECTS_ROOT", str(_PKG_ROOT.parent)))
    if (_PKG_ROOT / "projects").is_dir():  # 包内布局：<包>/projects/
        return _PKG_ROOT / "projects"
    return Path(os.environ.get("FLOW_PROJECTS_ROOT", "."))


ENGINE_DIR = _infer_engine_home()
INSTANCES_DIR = ENGINE_DIR / "instances"
FLOWS_DIR = ENGINE_DIR / "flows"
ENGINE_PY = ENGINE_DIR / "engine.py"
# 项目层级（2026-08-23）：父项目存在性 = 引擎实例 或 项目根目录下目录
# （flow-engine 等早期项目无实例 JSON，但目录 + charter 真实存在）
PROJECT_ROOT = _infer_projects_root()

# grillme-deep（002 §1）：discuss 设计树访谈数据层 = engine/interview.py
# LLM 只生成问题文本，所有落盘由 interview.py 确定性代码执行（charter D3）
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
import interview as interview_mod  # noqa: E402

# ── FastAPI 初始化 ────────────────────────────────────────

app = FastAPI(
    title="Flow Engine API",
    version="1.0.0",
    description="流程驱动引擎 REST API — 查询/拍板/创建流程",
)

# CORS（前端 SPA 跨域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本地服务，全放开
    allow_methods=["GET", "POST", "DELETE"],  # v2: DELETE /flows/{id} 需要
    allow_headers=["*"],
)

# ── 数据模型 ──────────────────────────────────────────────


class DecideRequest(BaseModel):
    decision: str


class CreateFlowRequest(BaseModel):
    template: str  # 项目名（flows/<name>.yaml）
    project_dir: str | None = None  # 项目目录（可选）
    parent: str | None = None  # 父项目 id（可选；提供时 = 改进现有项目的子项目，2026-08-23）
    # 需求描述（flow-engine-reqmate 2026-09-05）：建项目成功后落
    # exchange/000-requirements-brief.md（status: draft；失败不阻塞建项目）
    description: str | None = None


class GrillTurnRequest(BaseModel):
    """grill-me 单轮提交（契约 = 前端 types/index.ts GrillMeSubmitRequest）。

    grillme-deep（002 §1.3）：discuss 分支复用本端点，新增可选字段
    mode / nodeKey / followupIndex；decision 分支字段与语义不变
    （turnIndex 对 decision 必填，缺失时端点按旧校验语义返回 422）。
    """
    sessionId: str
    turnIndex: int | None = None
    action: str  # decision: confirm|edit|skip；discuss: answer_node|answer_followup|skip_node
    userText: str | None = None  # 仅 action=edit / answer_* 时使用
    # ── discuss interview 分支新增（002 §1.3，旧请求不带这些字段） ──
    mode: str | None = None
    nodeKey: str | None = None
    followupIndex: int | None = None


class GrillSessionRequest(BaseModel):
    """grill-stop / grill-confirm 请求体（002 §1.4/§1.5：仅 sessionId）。"""
    sessionId: str


# ── 工具函数 ──────────────────────────────────────────────


def _load_json(path: Path) -> dict | None:
    """安全读取 JSON 文件。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_yaml_flow(flow_name: str) -> dict | None:
    """读取流程模板 YAML。"""
    path = FLOWS_DIR / f"{flow_name}.yaml"
    if not path.exists():
        return None
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _get_step_type(flow_name: str, step_id: str) -> str:
    """从流程模板查步骤类型。"""
    flow = _load_yaml_flow(flow_name)
    if not flow:
        return ""
    for s in flow.get("steps", []):
        if s.get("id") == step_id:
            return s.get("type", "")
    return ""


def _get_step_def(flow_name: str, step_id: str) -> dict:
    """从流程模板查完整步骤定义。"""
    flow = _load_yaml_flow(flow_name)
    if not flow:
        return {}
    for s in flow.get("steps", []):
        if s.get("id") == step_id:
            return s
    return {}


def _get_flow_title(flow_name: str) -> str:
    """从流程模板查标题。"""
    flow = _load_yaml_flow(flow_name)
    if not flow:
        return flow_name
    return flow.get("title", flow_name)


def _get_flow_steps(flow_name: str) -> list[dict]:
    """从流程模板查所有步骤。"""
    flow = _load_yaml_flow(flow_name)
    if not flow:
        return []
    return flow.get("steps", [])


def _read_context_file(path_str: str) -> dict:
    """读取拍板上下文文件摘要。"""
    p = Path(path_str)
    if not p.exists():
        return {"path": path_str, "title": p.stem, "excerpt": "(文件不存在)"}
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
        # 提取 frontmatter title
        import re
        fm = re.match(r"^---\n(.*?)\n---", txt, re.S)
        title = p.stem
        body = txt
        if fm:
            block = fm.group(1)
            title_m = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', block, re.M)
            if title_m:
                title = title_m.group(1)
            body = txt[len(fm.group(0)):]
        body = re.sub(r"\s+", " ", body).strip()[:400]
        return {"path": path_str, "title": title[:60], "excerpt": body}
    except Exception as e:
        return {"path": path_str, "title": p.stem, "excerpt": f"(读取失败: {e})"}


def _build_flow_summary(name: str, st: dict) -> dict:
    """构建 FlowSummary 响应。"""
    step = st.get("step", "?")
    step_type = _get_step_type(name, step)
    steps = _get_flow_steps(name)
    total = len(steps)
    current_idx = 0
    for i, s in enumerate(steps):
        if s.get("id") == step:
            current_idx = i + 1
            break
    history = st.get("history", [])
    recent = history[-3:] if history else []
    has_pending = st.get("status") == "running" and step_type == "decision"
    return {
        "id": name,
        "title": _get_flow_title(name),
        "status": st.get("status", "?"),
        "parent": st.get("parent"),  # 父项目 id（子项目才有；2026-08-23 项目层级）
        "template": st.get("template"),  # 执行流程模板名（立项后绑定，2026-08-23）
        "currentStep": step,
        "currentStepType": step_type,
        "iteration": st.get("iteration", 0),
        "totalSteps": total,
        "currentStepIndex": current_idx,
        "recentHistory": recent,
        "hasPendingDecision": has_pending,
        "updatedAt": datetime.now().isoformat(),
    }


def _build_flow_detail(name: str, st: dict) -> dict:
    """构建 FlowDetail 响应。"""
    summary = _build_flow_summary(name, st)
    step = st.get("step", "?")
    step_def = _get_step_def(name, step)
    # 上下文文件
    ctx_files = step_def.get("context_files") or []
    context_files = [_read_context_file(cf) for cf in ctx_files[:4]]
    # 步骤定义列表
    steps = _get_flow_steps(name)
    step_defs = [
        {
            "id": s.get("id", ""),
            "type": s.get("type", ""),
            "description": s.get("description", ""),
            "question": s.get("question"),
            "options": s.get("options"),
            "contextFiles": s.get("context_files"),
        }
        for s in steps
    ]
    summary.update({
        "steps": step_defs,
        "contextFiles": context_files,
        "history": st.get("history", []),
    })
    return summary


# ── v2 工具函数（step 8 implement_api 新增） ───────────────

# 流程 id 白名单（防路径穿越；DELETE 是破坏性操作，从严）
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

# history 条目 → 日志级别 的启发式（引擎 history 无 level 字段）
_WARN_MARKS = ("⚠", "❌", "FAIL", "超时", "失败", "打回")


def _entry_level(h: dict) -> str:
    """history 条目 → LogEntry.level。"""
    if h.get("passed") is False:
        return "warn"
    text = str(h.get("log") or h.get("detail") or "")
    if any(m in text for m in _WARN_MARKS):
        return "warn"
    return "info"


def _entry_message(h: dict) -> str:
    """history 条目 → LogEntry.message（取最可读的字段）。"""
    for k in ("log", "detail", "decision"):
        v = h.get(k)
        if v:
            return str(v)
    return str(h.get("action", ""))


def _parse_log_file(fp: Path) -> list[dict]:
    """解析可选的引擎日志文件（每行 ISO|level|source|message，容错）。"""
    entries: list[dict] = []
    try:
        lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return entries
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        parts = ln.split("|", 3)
        if len(parts) == 4 and parts[1] in ("info", "warn", "error", "debug"):
            entries.append({"timestamp": parts[0], "level": parts[1],
                            "source": parts[2], "message": parts[3]})
        else:
            entries.append({"timestamp": "", "level": "info",
                            "source": "raw", "message": ln})
    return entries


def _build_log_entries(flow_id: str, st: dict, mtime: float) -> list[dict]:
    """instance history + 可选 .log 文件 → LogEntry[]（升序）。

    引擎 history 条目不带时间戳 → 以实例文件 mtime 为锚点、按条目间隔
    60s 向前合成时间线（保证倒序展示稳定）。未来引擎写真时间戳日志时，
    .log 文件条目按原时间戳合并。
    """
    history = st.get("history", [])
    n = len(history)
    entries: list[dict] = []
    for i, h in enumerate(history):
        ts = mtime - (n - 1 - i) * 60.0
        entries.append({
            "timestamp": datetime.fromtimestamp(max(ts, 1.0)).isoformat(),
            "level": _entry_level(h),
            "source": str(h.get("step", "")),
            "message": _entry_message(h),
        })
    log_fp = INSTANCES_DIR / f"{flow_id}.log"
    if log_fp.exists():
        entries.extend(_parse_log_file(log_fp))
    entries.sort(key=lambda e: e["timestamp"])
    return entries


def _save_instance(fp: Path, st: dict) -> None:
    """原子写实例 JSON（tmp + os.replace，避免引擎并发读到半截文件）。"""
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, fp)


def _grill_session_id(flow_id: str, step: str) -> str:
    """grill-me 会话 id（flow+step 确定性哈希，提交时校验防串会话）。"""
    h = hashlib.sha256(f"{flow_id}:{step}".encode("utf-8")).hexdigest()[:8]
    return f"grill-{flow_id}-{step}-{h}"


def _build_grill_turns(flow_id: str, st: dict) -> list[dict]:
    """decision 步骤 → grill-me 延伸追问列表（规则式生成，确定性）。

    turn 0 = 拍板问题本身 + 选项推荐；后续每个上下文文件一轮
    （"是否已审阅 + 有无反例"）。全部字段来自流程模板，不编造内容。
    """
    step = st.get("step", "")
    step_def = _get_step_def(flow_id, step)
    question = (step_def.get("question") or step_def.get("description")
                or f"步骤 [{step}] 待拍板")
    options = step_def.get("options") or []
    if options:
        opts_txt = "；".join(
            f"{o.get('key', '')}={o.get('label', '')}" for o in options)
        rec = (f"推荐选项 {options[0].get('key', 'A')}："
               f"{options[0].get('label', '')}（全部选项：{opts_txt}）")
    else:
        rec = "同意并继续（accept）"
    ctx_files = step_def.get("context_files") or []
    turns: list[dict] = [{
        "index": 0,
        "question": question,
        "recommendedAnswer": rec,
        "contextRefs": [str(c) for c in ctx_files[:4]],
    }]
    for i, cf in enumerate(ctx_files[:2], start=1):
        name = Path(str(cf)).name
        turns.append({
            "index": i,
            "question": (f"拍板前是否已审阅「{name}」的关键结论？"
                         f"是否存在与推荐选项冲突的信息？"),
            "recommendedAnswer": (f"已审阅「{name}」，未发现与推荐选项冲突的信息，"
                                  f"按推荐选项拍板。"),
            "contextRefs": [str(cf)],
        })
    return turns


def _grill_active(st: dict, flow_id: str) -> bool:
    """grill-me 在 running + decision 或 discuss 步骤激活（006 §4.1.1）。

    2026-08-22 返工（P1b）：原仅 decision 激活——discuss 立项访谈步骤
    拿不到上下文，面板报「当前步骤未触发 grill-me」，新项目访谈闭环断裂。
    """
    return (st.get("status") == "running"
            and _get_step_type(flow_id, st.get("step", "")) in ("decision", "discuss"))


def _grill_mode(flow_id: str, st: dict) -> str:
    """grillme-deep 双模式判别（002 §1.1）。

    decision → 现有 _build_grill_turns 拍板追问（一字不改）；
    discuss  → interview 设计树访谈链路（interview.py 数据层）。
    """
    if _get_step_type(flow_id, st.get("step", "")) == "discuss":
        return "interview"
    return "decision"


def _interview_project_dir(flow_id: str) -> Path | None:
    """discuss 步的 project_dir（取自流程模板，与 _get_step_def 同源，002 §1.1）。"""
    step_def = _get_step_def(flow_id, "discuss")
    pd = step_def.get("project_dir")
    if not pd:
        return None
    return Path(str(pd))


def _build_grill_interview(flow_id: str, st: dict) -> dict:
    """GET /grill-context 的 discuss 分支响应（002 §1.2）。

    interview 对象由 interview.build_view 从 INTERVIEW.json 重建（纯读 +
    一次性降级信号清除，零 LLM 调用——刷新/断点恢复路径安全，002 §2.5）。
    """
    pd = _interview_project_dir(flow_id)
    if pd is None:
        raise HTTPException(
            status_code=500,
            detail=f"流程 {flow_id} 的 discuss 步骤缺少 project_dir 字段")
    step = st.get("step", "")
    return {
        "active": True,
        "sessionId": _grill_session_id(flow_id, step),
        "mode": "interview",
        "turns": [],  # discuss 模式恒为 []（保留字段防前端旧代码崩溃）
        "interview": interview_mod.build_view(pd),
    }


# ── API 端点 ──────────────────────────────────────────────


@app.get("/api/flows")
async def list_flows():
    """列出所有流程实例。"""
    flows = []
    if INSTANCES_DIR.is_dir():
        for fp in sorted(INSTANCES_DIR.glob("*.json")):
            name = fp.stem
            st = _load_json(fp)
            if not st:
                continue
            flows.append(_build_flow_summary(name, st))
    return flows


@app.get("/api/flows/{flow_id}")
async def get_flow(flow_id: str):
    """获取流程详情。"""
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    return _build_flow_detail(flow_id, st)


@app.post("/api/flows/{flow_id}/decide")
async def decide(flow_id: str, req: DecideRequest):
    """拍板决策。"""
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    # 仅允许在 decision 步骤拍板
    step = st.get("step", "")
    step_type = _get_step_type(flow_id, step)
    if st.get("status") != "running" or step_type != "decision":
        raise HTTPException(
            status_code=400,
            detail=f"当前不在拍板点（step={step}, type={step_type or '非decision'}），拍板已忽略"
        )
    # 写决策文件
    decision_path = INSTANCES_DIR / f"{flow_id}.decision"
    try:
        INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(req.decision, encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写拍板失败: {e}")
    # 立即触发引擎心跳
    try:
        subprocess.run(
            [sys.executable, str(ENGINE_PY), "--flow", flow_id],
            capture_output=True, text=True, timeout=120
        )
    except Exception:
        pass  # 引擎心跳失败不阻塞拍板响应
    return {"status": "ok", "decision": req.decision, "flow": flow_id}


# ── mechanism-fix-engine-verify-wait F2 新增 2 端点 ──────
# paused 实例可恢复（002 §F2.1；纯新增，不改现有 9 端点）


@app.post("/api/flows/{flow_id}/resume")
async def resume_flow(flow_id: str):
    """恢复 paused 流程实例 → running（002 §F2.1）。

    契约（前端 api/client.ts resumeFlow）：
      200 {"status": "ok", "flow": flow_id, "action": "resumed"}
      404 流程不存在；409 当前 status 非 paused；500 读写失败。

    成功后 history 追加 user_resume + 触发一次引擎心跳
    （与 decide 端点对齐；心跳失败不阻塞响应）。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    cur = st.get("status", "?")
    if cur != "paused":
        raise HTTPException(
            status_code=409,
            detail=f"当前 status={cur}，非 paused，resume 已拒绝",
        )
    st["status"] = "running"
    st.setdefault("history", []).append({
        "step": "system",
        "action": "user_resume",
        "detail": "面板 resume：paused → running",
        "time": datetime.now().isoformat(),
    })
    try:
        _save_instance(fp, st)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写实例失败: {e}")
    # 触发一次引擎心跳，让流程立即推进（不阻塞响应）
    try:
        subprocess.run(
            [sys.executable, str(ENGINE_PY), "--flow", flow_id],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        pass
    return {"status": "ok", "flow": flow_id, "action": "resumed"}


@app.post("/api/flows/{flow_id}/pause")
async def pause_flow(flow_id: str):
    """暂停 running 流程实例 → paused（002 §F2.1）。

    契约（前端 api/client.ts pauseFlow）：
      200 {"status": "ok", "flow": flow_id, "action": "paused"}
      404 流程不存在；409 当前 status 非 running；500 读写失败。

    成功后 history 追加 user_pause；不触发心跳
    （paused 实例本就不被 wrapper 扫描）。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    cur = st.get("status", "?")
    if cur != "running":
        raise HTTPException(
            status_code=409,
            detail=f"当前 status={cur}，非 running，pause 已拒绝",
        )
    st["status"] = "paused"
    st.setdefault("history", []).append({
        "step": "system",
        "action": "user_pause",
        "detail": "面板 pause：running → paused",
        "time": datetime.now().isoformat(),
    })
    try:
        _save_instance(fp, st)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写实例失败: {e}")
    return {"status": "ok", "flow": flow_id, "action": "paused"}


@app.post("/api/flows")
async def create_flow(req: CreateFlowRequest):
    """创建新流程（调用 gen_project.py）。"""
    gen_py = ENGINE_DIR / "gen_project.py"
    if not gen_py.exists():
        raise HTTPException(status_code=500, detail="gen_project.py 不存在")
    project = req.template
    if not project or not project.replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="项目名必须为英文 slug")
    # 项目层级（2026-08-23）：parent 提供时必须是已存在项目（父项目）。
    # 存在性 = 引擎实例 JSON 或 Project 根下目录（flow-engine 等早期项目
    # 无实例但真实存在，见 PROJECT_ROOT 注释）
    if req.parent is not None:
        parent = req.parent.strip()
        parent_exists = (INSTANCES_DIR / f"{parent}.json").exists() or (
            (PROJECT_ROOT / parent).is_dir()
        )
        if not parent or not parent_exists:
            raise HTTPException(
                status_code=404,
                detail=f"父项目不存在: {parent}（改进项目需选择已有项目）",
            )
    cmd = [sys.executable, str(gen_py), project]
    if req.parent:
        cmd += ["--parent", req.parent]
    if req.project_dir:
        cmd += ["--project-dir", req.project_dir]
    # 路径同源透传（2026-09-05 reqmate 实测 P0）：gen_project 的 PROJECT_ROOT /
    # INSTANCES_DIR 默认值与 api.py 的包内布局推断可能分裂——api 建项目在
    # projects root 与 ENGINE_HOME 同源（否则素材端点写错目录）。子进程 env 必须与
    # api.py 同源，保证 brief/素材/gen 骨架落同一目录。
    sub_env = dict(os.environ)
    sub_env["FLOW_PROJECTS_ROOT"] = str(PROJECT_ROOT)
    sub_env["FLOW_ENGINE_HOME"] = str(ENGINE_DIR)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=sub_env
        )
        # gen_project.py exit 2 = 项目已存在（存在性保护拒绝覆盖）→ 409
        if result.returncode == 2:
            raise HTTPException(
                status_code=409,
                detail=f"流程已存在，拒绝重复创建: {project}（防止覆盖定制模板/实例历史）"
            )
        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"创建失败: {result.stderr[:200]}"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建异常: {e}")
    # 返回新建的流程
    fp = INSTANCES_DIR / f"{project}.json"
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail="创建后读取失败")
    # 需求描述落盘（flow-engine-reqmate 2026-09-05，002 §T2）：
    # exchange/000-requirements-brief.md（status: draft）。失败不阻塞建项目。
    if req.description and req.description.strip():
        try:
            pd = Path(req.project_dir) if req.project_dir else PROJECT_ROOT / project
            _write_requirements_brief(pd, req.description)
        except Exception:
            # iter4 C7：失败不静默——不阻塞建项目，但必须留痕（不 pass 吞掉）
            logging.exception("requirements brief 落盘失败（create_flow 端点）")
    return _build_flow_summary(project, st)


@app.get("/api/flows/{flow_id}/history")
async def get_history(flow_id: str):
    """获取流程完整历史。"""
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    return st.get("history", [])


# ── 需求素材包（flow-engine-reqmate 2026-09-05，002 §T1/§T2） ─────────

# 单项目素材累计上限 200 MiB = 209,715,200 bytes：超出只在 MANIFEST 登记，不拷贝
_MATERIALS_LIMIT_BYTES = 200 * 1024 * 1024

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


def _sanitize_rel_path(filename: str) -> str | None:
    """清洗上传文件名（文件夹上传时 filename 字段带相对路径）。

    前端 webkitdirectory 选文件夹，浏览器把相对路径放进 filename
    （如 "子目录/a.txt"）。统一分隔符后逐段拒绝 ".." / 绝对路径 /
    盘符（防目录穿越）；合法返回 posix 风格相对路径，非法返回 None。
    """
    if not filename:
        return None
    parts = [p for p in filename.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    return "/".join(parts)


def _write_requirements_brief(pd: Path, description: str) -> Path:
    """需求描述落 exchange/000-requirements-brief.md（frontmatter status: draft）。

    已存在则追加「## 补充」节，不覆盖原文（002 §T1）。
    """
    ex = pd / "exchange"
    ex.mkdir(parents=True, exist_ok=True)
    fp = ex / "000-requirements-brief.md"
    desc = (description or "").strip()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if fp.exists():
        old = fp.read_text(encoding="utf-8")
        fp.write_text(old.rstrip() + f"\n\n## 补充（{now}）\n\n{desc}\n",
                      encoding="utf-8")
    else:
        fp.write_text(
            "---\n"
            "title: \"000 需求简报\"\n"
            "artifact_type: artifact\n"
            "status: draft\n"
            f"created_at: \"{now}\"\n"
            "---\n\n"
            f"# 需求简报（建项目时提供）\n\n{desc}\n",
            encoding="utf-8")
    return fp


def _manifest_cell(text: str) -> str:
    """表格单元格转义（iter4 C4）：`|` → `\\|`、换行 → 空格，防破坏表格列数。"""
    return (str(text)
            .replace("|", "\\|")
            .replace("\r\n", " ")
            .replace("\n", " ")
            .replace("\r", " "))


def _manifest_append(materials_dir: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """生成/追加 materials/MANIFEST.md（markdown 表格）。

    rows: [(类型, 来源, 落盘路径, 用途), ...]；登记时间由本函数统一填。
    每格文本经 _manifest_cell 转义（文件名含 `|`/换行时列数不乱）。
    """
    fp = materials_dir / "MANIFEST.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"| {_manifest_cell(t)} | {_manifest_cell(src)} | "
             f"{_manifest_cell(dst)} | {_manifest_cell(use)} | {now} |"
             for (t, src, dst, use) in rows]
    if fp.exists():
        fp.write_text(fp.read_text(encoding="utf-8").rstrip() + "\n"
                      + "\n".join(lines) + "\n", encoding="utf-8")
    else:
        fp.write_text(
            "# MANIFEST — 项目素材登记表\n\n"
            "> 由面板素材端点（POST /api/flows/{id}/materials）自动生成/追加；"
            "开工先读本表再取素材。\n\n"
            "| 类型 | 来源 | 落盘路径 | 用途 | 登记时间 |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(lines) + "\n", encoding="utf-8")


def _fetch_url_text(url: str) -> str:
    """抓 URL 正文文本（优先 httpx，无则回落 urllib）。失败抛异常，调用方登记待取。"""
    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None
    if httpx is not None:
        r = httpx.get(url, timeout=20, follow_redirects=True,
                      headers={"User-Agent": "flow-engine-reqmate/1.0"})
        r.raise_for_status()
        return r.text
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "flow-engine-reqmate/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _url_slug(url: str) -> str:
    """URL → 落盘文件名 slug（域名+路径骨架，非法字符换连字符）。"""
    s = re.sub(r"^https?://", "", url.strip())
    s = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "-", s).strip("-.")
    return s[:64] or "url"


@app.post("/api/flows/{flow_id}/materials")
async def upload_materials(
    flow_id: str,
    files: list[UploadFile] = File(default=[]),
    urls: str = Form(default=""),
    description: str = Form(default=""),
):
    """需求素材上传（flow-engine-reqmate 2026-09-05，002 §T1）。

    multipart 三段：files（文件/图片/文件夹打包，文件夹的相对路径在
    filename 字段）、urls（换行/逗号分隔）、description（需求描述）。

    落盘规则（项目目录 = PROJECT_ROOT/<flow_id>，快照复制不动原件）：
      - 文件/图片 → materials/；累计上限 200MiB，超出只登记 MANIFEST 不拷贝
      - URL → 抓正文存 materials/<slug>.md；抓取失败登记「待取」，不抛错
      - description → exchange/000-requirements-brief.md（已存在追加不覆盖）
      - 生成/追加 materials/MANIFEST.md（类型/来源/落盘路径/用途/登记时间）

    大文件说明：单个上传文件单次读入内存（uf.read()），依赖 200MiB
    累计上限兜底（超限文件仍会先整体读入再拒落盘）。

    失败不静默（iter4 C4/C7）：URL 抓取失败 / 文件超限 / brief 落盘失败
    均登记进响应体 failed_items（每项 {type, source, reason, ts}）；
    urls_pending / skipped_over_limit 计数字段保留向后兼容。

    404 口径同 create_flow 的 parent 校验：无实例 JSON 且 PROJECT_ROOT
    下无目录 → 项目不存在。
    """
    flow_exists = (INSTANCES_DIR / f"{flow_id}.json").exists() \
        or (PROJECT_ROOT / flow_id).is_dir()
    if not flow_exists:
        raise HTTPException(status_code=404, detail=f"项目不存在: {flow_id}")
    pd = PROJECT_ROOT / flow_id
    materials_dir = pd / "materials"
    materials_dir.mkdir(parents=True, exist_ok=True)

    # 累计上限按「已落盘素材 + 本批」计算
    used = sum(f.stat().st_size for f in materials_dir.rglob("*") if f.is_file())
    rows: list[tuple[str, str, str, str]] = []
    failed_items: list[dict] = []  # iter4 C4：失败明细（空列表 = 全成功）
    saved = skipped = pending = 0

    for uf in files:
        raw_name = uf.filename or ""
        rel = _sanitize_rel_path(raw_name)
        if rel is None:
            rows.append(("文件", raw_name or "(未知)", "(拒绝: 文件名非法)", "路径安全校验未过"))
            failed_items.append({"type": "file", "source": raw_name or "(未知)",
                                 "reason": "unsafe_name",
                                 "ts": datetime.now().isoformat()})
            continue
        data = await uf.read()
        kind = "图片" if Path(rel).suffix.lower() in _IMAGE_EXTS else "文件"
        if used + len(data) > _MATERIALS_LIMIT_BYTES:
            rows.append((kind, raw_name, "(超限未落盘)", "超 200MiB 累计上限，待手动补录"))
            failed_items.append({"type": "file", "source": raw_name,
                                 "reason": "over_limit",
                                 "ts": datetime.now().isoformat()})
            skipped += 1
            continue
        dst = materials_dir.joinpath(*rel.split("/"))
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        except OSError as e:
            # iter4 C4：文件系统拒绝写入（如 Windows 文件名含 `|` 等非法字符）
            # → 结构化登记，不 500（失败不静默）
            logging.exception("素材写入失败: %s", raw_name)
            rows.append((kind, raw_name, "(写入失败)",
                         f"文件系统拒绝写入: {type(e).__name__}"))
            failed_items.append({"type": "file", "source": raw_name,
                                 "reason": "write_error",
                                 "ts": datetime.now().isoformat()})
            continue
        used += len(data)
        rows.append((kind, raw_name, str(dst.relative_to(pd)).replace("\\", "/"),
                     "用户上传素材"))
        saved += 1

    url_list = [u.strip() for u in re.split(r"[,\n]+", urls or "") if u.strip()]
    for url in url_list:
        dst = materials_dir / f"{_url_slug(url)}.md"
        if dst.exists():
            dst = materials_dir / f"{dst.stem}-{hashlib.md5(url.encode('utf-8')).hexdigest()[:6]}.md"
        try:
            text = _fetch_url_text(url)
            dst.write_text(
                f"<!-- 来源: {url} | 抓取时间: {datetime.now().isoformat()} -->\n\n{text}",
                encoding="utf-8")
            rows.append(("URL", url, str(dst.relative_to(pd)).replace("\\", "/"),
                         "网页正文（抓取）"))
        except Exception:
            # iter4 C4：失败结构化登记（失败类别/来源/登记时间 ISO），不静默
            now_iso = datetime.now().isoformat()
            rows.append(("URL", url, "(待取: fetch_error)",
                         f"网页正文抓取失败（fetch_error），待取重试；"
                         f"登记时间 {now_iso}"))
            failed_items.append({"type": "url", "source": url,
                                 "reason": "fetch_error", "ts": now_iso})
            pending += 1

    if description and description.strip():
        # iter4 C7：失败不静默——登记 failed_items + 记日志，不吞异常
        try:
            _write_requirements_brief(pd, description)
        except Exception as e:
            logging.exception("requirements brief 落盘失败（materials 端点）")
            failed_items.append({"type": "brief",
                                 "source": "000-requirements-brief.md",
                                 "reason": f"{type(e).__name__}: {e}"[:200],
                                 "ts": datetime.now().isoformat()})

    if rows:
        _manifest_append(materials_dir, rows)

    manifest_fp = materials_dir / "MANIFEST.md"
    return {
        "status": "ok",
        "flow": flow_id,
        "saved": saved,
        "skipped_over_limit": skipped,
        "urls_pending": pending,
        "failed_items": failed_items,
        "manifest": str(manifest_fp) if manifest_fp.exists() else "",
    }


# ── v2 新增 4 端点（step 8 implement_api） ────────────────


@app.get("/api/flows/{flow_id}/logs")
async def list_logs(
    flow_id: str,
    level: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    """流程日志（v2 新增）。

    契约 = 前端 api/client.ts listLogs：返回 LogEntry[]（平铺数组），
    每条 { timestamp, level, source?, message }（types/index.ts LogEntry）。
    前端 Logs.tsx 自行倒序展示。

    可选查询参数（后端保留能力，前端当前不传）：
      level=info|warn|error|debug 过滤；limit/offset 分页。
    """
    if level and level not in ("info", "warn", "error", "debug"):
        raise HTTPException(
            status_code=400, detail=f"level 必须为 info|warn|error|debug，收到: {level}")
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        mtime = datetime.now().timestamp()
    entries = _build_log_entries(flow_id, st, mtime)
    if level:
        entries = [e for e in entries if e["level"] == level]
    return entries[offset:offset + limit]


@app.delete("/api/flows/{flow_id}")
async def delete_flow(flow_id: str):
    """删除流程实例（v2 新增）。

    契约 = 前端 api/client.ts deleteFlow：成功返回 { status, flow }。
    仅删实例侧文件（instance JSON + .decision + .log），模板 YAML 保留
    （可重新创建）；删除前先归档该流程残留的 kanban 派发卡，防幽灵卡。
    """
    if not _SAFE_ID.match(flow_id):
        raise HTTPException(status_code=400, detail="非法的流程 id")
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp) or {}
    # 归档残留 kanban 卡 —— 仅终态流程（completed/failed）。
    # running 流程的卡可能是活跃工作（如当前 step 的验收卡），绝不归档；
    # 且引擎下次心跳会重建实例 JSON，删了也会回来，故仅清理实例侧文件。
    archived: list[str] = []
    if st.get("status") in ("completed", "failed"):
        try:
            sys.path.insert(0, str(ENGINE_DIR))
            import kanban_client
            cards = kanban_client.archive_flow_cards(flow_id)
            archived = [c.get("id", "?") for c in (cards or [])]
        except Exception:
            pass
    # 删实例侧文件
    removed = []
    for suffix in (".json", ".decision", ".log"):
        f = INSTANCES_DIR / f"{flow_id}{suffix}"
        if f.exists():
            try:
                f.unlink()
                removed.append(f.name)
            except OSError as e:
                raise HTTPException(
                    status_code=500, detail=f"删除失败: {f.name}: {e}")
    return {"status": "ok", "flow": flow_id,
            "removed": removed, "archivedCards": archived}


@app.get("/api/flows/{flow_id}/grill-context")
async def get_grill_context(flow_id: str):
    """grill-me 上下文（v2 新增）。

    契约 = 前端 GrillMePanel.tsx 消费字段：{ active, sessionId, turns[] }
    （平铺，非嵌套 grillMe；GrillMePanel 直接读 ctx.active / ctx.sessionId /
    ctx.turns）。active=false 时前端展示「当前步骤未触发 grill-me」。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    if not _grill_active(st, flow_id):
        return {"active": False, "sessionId": "", "turns": []}
    # grillme-deep 双模式：discuss 步走 interview 分支（002 §1.2）；
    # decision 分支响应保持不变（{active, sessionId, turns[]}）
    if _grill_mode(flow_id, st) == "interview":
        return _build_grill_interview(flow_id, st)
    step = st.get("step", "")
    return {
        "active": True,
        "sessionId": _grill_session_id(flow_id, step),
        "turns": _build_grill_turns(flow_id, st),
    }


async def _handle_interview_turn(flow_id: str, req: GrillTurnRequest) -> dict:
    """discuss 设计树访谈提交（002 §1.3，由 /grill-turn interview 分支调用）。

    LLM 追问生成本请求内完成（≤25s 超时，四路径降级不阻塞访谈，002 §2.5）。
    所有落盘由 interview.py 确定性代码执行（charter D3）；ValueError（stopped
    冻结/前沿不符/越界/空答）统一映射 400（§1.3 错误语义）。
    """
    pd = _interview_project_dir(flow_id)
    if pd is None:
        raise HTTPException(
            status_code=500,
            detail=f"流程 {flow_id} 的 discuss 步骤缺少 project_dir 字段")
    if req.action not in ("answer_node", "answer_followup", "skip_node"):
        raise HTTPException(
            status_code=400,
            detail=("interview action 必须为 "
                    f"answer_node|answer_followup|skip_node，收到: {req.action}"))
    if req.action in ("answer_node", "answer_followup") \
            and not (req.userText or "").strip():
        raise HTTPException(status_code=400, detail="当前 action 需要非空 userText")
    if not req.nodeKey:
        raise HTTPException(status_code=400, detail=f"{req.action} 需要 nodeKey")
    try:
        if req.action == "answer_node":
            result = await interview_mod.answer_node(pd, req.nodeKey, req.userText or "")
        elif req.action == "answer_followup":
            if req.followupIndex is None:
                raise HTTPException(
                    status_code=400, detail="answer_followup 需要 followupIndex")
            result = await interview_mod.answer_followup(
                pd, req.nodeKey, req.followupIndex, req.userText or "")
        else:  # skip_node：采用推荐答案，不触发 LLM
            result = interview_mod.skip_node(pd, req.nodeKey)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    view = result["view"]
    return {
        "status": "ok",
        "interview": view,
        "treeComplete": result["treeComplete"],
        "llmDegraded": bool(view.get("llmDegraded")),
        "degradedReason": result.get("degradedReason", ""),
    }


@app.post("/api/flows/{flow_id}/grill-turn")
async def submit_grill_turn(flow_id: str, req: GrillTurnRequest):
    """grill-me 单轮提交（v2 新增；grillme-deep 扩 discuss 分支）。

    契约 = 前端 GrillMePanel.tsx：
    - decision 分支（语义一字不改）：请求 { sessionId, turnIndex, action, userText? }
      响应 { status: 'ok'|'error', nextTurn: GrillMeTurn|null, done: bool, summary? }
      用户最终答复写入实例 grill_me_log（006 §4.1.3：不影响 decision 拍板语义）。
    - discuss 分支（002 §1.3）：请求 { sessionId, mode:'interview', action:
      answer_node|answer_followup|skip_node, userText?, nodeKey, followupIndex? }
      响应 { status:'ok', interview, treeComplete, llmDegraded, degradedReason }
      LLM 追问生成在本请求内同步完成（≤25s，002 §2.5）。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    if not _grill_active(st, flow_id):
        raise HTTPException(
            status_code=400,
            detail=f"当前不在拍板点/访谈点（step={st.get('step')}），grill-me 不可用")
    step = st.get("step", "")
    expect_sid = _grill_session_id(flow_id, step)
    if req.sessionId != expect_sid:
        raise HTTPException(status_code=400, detail="sessionId 与当前拍板点不匹配")

    # ── discuss interview 分支（002 §1.3） ──
    if _grill_mode(flow_id, st) == "interview":
        return await _handle_interview_turn(flow_id, req)

    # ── decision 分支（既有语义不变） ──
    if req.action not in ("confirm", "edit", "skip"):
        raise HTTPException(
            status_code=400, detail=f"action 必须为 confirm|edit|skip，收到: {req.action}")
    if req.action == "edit" and not (req.userText or "").strip():
        raise HTTPException(
            status_code=400, detail="当前 action=edit 但 userText 为空")
    if req.turnIndex is None:
        raise HTTPException(status_code=422, detail="decision 分支必须提供 turnIndex")
    turns = _build_grill_turns(flow_id, st)
    if req.turnIndex < 0 or req.turnIndex >= len(turns):
        raise HTTPException(
            status_code=400,
            detail=f"turnIndex 越界: {req.turnIndex}（共 {len(turns)} 轮）")
    turn = turns[req.turnIndex]
    final_answer = (req.userText or "").strip() if req.action == "edit" \
        else turn["recommendedAnswer"]
    # 记录本轮最终答复（006 §4.1.3：grill_me_log，不影响 decision 语义）
    st.setdefault("grill_me_log", []).append({
        "sessionId": expect_sid,
        "step": step,
        "turnIndex": req.turnIndex,
        "action": req.action,
        "question": turn["question"],
        "answer": final_answer,
        "ts": datetime.now().isoformat(),
    })
    _save_instance(fp, st)
    # 下一轮 / 完成
    if req.turnIndex + 1 < len(turns):
        return {"status": "ok", "nextTurn": turns[req.turnIndex + 1],
                "done": False}
    return {"status": "ok", "nextTurn": None, "done": True,
            "summary": (f"grill-me 共完成 {len(turns)} 轮，"
                        "所有答复已记录到引擎实例。")}


# ── grillme-deep 新端点（002 §1.4/§1.5，纯新增不动既有端点） ──


@app.post("/api/flows/{flow_id}/grill-stop")
async def stop_grill_me(flow_id: str, req: GrillSessionRequest):
    """停止追问（G3，002 §1.4）：冻结问题流 + 生成 spec（未答节点采用推荐）。

    幂等：重复 stop → 直接返回已有 spec 路径，不重复生成。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    if not _grill_active(st, flow_id) or _grill_mode(flow_id, st) != "interview":
        raise HTTPException(
            status_code=400,
            detail=f"当前不在访谈点（step={st.get('step')}），grill-stop 不可用")
    step = st.get("step", "")
    if req.sessionId != _grill_session_id(flow_id, step):
        raise HTTPException(status_code=400, detail="sessionId 与当前访谈点不匹配")
    pd = _interview_project_dir(flow_id)
    if pd is None:
        raise HTTPException(
            status_code=500,
            detail=f"流程 {flow_id} 的 discuss 步骤缺少 project_dir 字段")
    try:
        result = interview_mod.stop_interview(pd)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "specFile": result["specFile"],
            "summary": result["summary"],
            "alreadyStopped": result.get("alreadyStopped", False)}


@app.post("/api/flows/{flow_id}/grill-confirm")
async def confirm_grill_me(flow_id: str, req: GrillSessionRequest):
    """确认共识（G5，002 §1.5）：spec draft → confirmed → 引擎 discuss 步推进。

    engine.py 零改动（004 §13 验证成立）：_do_discuss 检测 spec.md
    status=confirmed 即推进。前置校验：spec.md 存在且 status=draft（002 §1.5）。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    if not _grill_active(st, flow_id) or _grill_mode(flow_id, st) != "interview":
        raise HTTPException(
            status_code=400,
            detail=f"当前不在访谈点（step={st.get('step')}），grill-confirm 不可用")
    step = st.get("step", "")
    if req.sessionId != _grill_session_id(flow_id, step):
        raise HTTPException(status_code=400, detail="sessionId 与当前访谈点不匹配")
    pd = _interview_project_dir(flow_id)
    if pd is None:
        raise HTTPException(
            status_code=500,
            detail=f"流程 {flow_id} 的 discuss 步骤缺少 project_dir 字段")
    spec_fp = pd / "exchange" / "spec.md"
    if not spec_fp.exists():
        # 2026-08-22 hotfix（user 实验 test-project 发现）：树全覆盖阳光路径
        # 自动补生成。原缺陷：7 节点答完点「确认共识」报 400「spec.md 不存在」——
        # 002 §3.3 写了"树全覆盖后可手动 gen-spec"但未实现任何入口（gen_spec
        # 只挂在 grill-stop 停止语义上）。修复：树已全覆盖 → confirm 时自动
        # gen_spec 再 confirm（用户心智：答完点确认 = 一步到位）；
        # 未全覆盖仍拒绝（防跳过问题直接确认）。
        iv_st = interview_mod.load_state(pd)
        answered_n = len(iv_st.get("answers", {}))
        total_n = len(interview_mod.DESIGN_TREE)
        if answered_n >= total_n and not interview_mod.frontier(iv_st):
            interview_mod.gen_spec(pd)
        else:
            raise HTTPException(
                status_code=400,
                detail=(f"spec.md 不存在且设计树未全覆盖（已答 {answered_n}/{total_n} 节点）："
                        f"请继续答完全部节点，或用「停止追问」冻结生成 spec"))
    m = re.search(r"^status:\s*(\S+)", spec_fp.read_text(encoding="utf-8"), re.M)
    if (m.group(1) if m else "?") != "draft":
        raise HTTPException(
            status_code=400,
            detail=f"spec.md status={m.group(1) if m else '?'}，confirm 要求 draft")
    try:
        interview_mod.confirm(pd)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "confirmed": True}


@app.get("/api/health")
async def health():
    """健康检查。"""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ── 流程模板库（2026-08-23 项目化改造 P3）──────────────────


def _load_template_meta(name: str, data: dict) -> dict:
    """从模板 YAML 提取 meta（无 meta 段时给默认值，兼容存量模板）。"""
    meta = data.get("meta") or {}
    return {
        "id": name,
        "title": meta.get("title") or data.get("title") or name,
        "description": meta.get("description", ""),
        "category": meta.get("category", "general"),
        "source": meta.get("source", "built-in"),
        "steps": len(data.get("steps", [])),
        "decisionPoints": sum(
            1 for s in data.get("steps", []) if s.get("type") == "decision"
        ),
    }


@app.get("/api/flow-templates")
async def list_flow_templates():
    """列出可用执行流程模板（flows/template-*.yaml，meta 段驱动）。

    返回 FlowTemplateMeta[]：id / title / description / category /
    source / steps / decisionPoints。立项后可把某模板绑定到项目
    （POST /api/flows/{id}/template），引擎按该模板做任务分派/监察/验收。
    """
    templates = []
    for fp in sorted(FLOWS_DIR.glob("template-*.yaml")):
        try:
            data = _load_yaml_flow(fp.stem)
            if not data:
                raise ValueError("YAML 解析为空")
        except Exception as e:  # 单个模板损坏不影响列表
            templates.append({
                "id": fp.stem,
                "title": f"⚠ 模板损坏: {e}",
                "description": "", "category": "broken",
                "source": "unknown", "steps": 0, "decisionPoints": 0,
            })
            continue
        if not isinstance(data, dict) or not data.get("steps"):
            continue
        templates.append(_load_template_meta(fp.stem, data))
    return templates


class BindTemplateRequest(BaseModel):
    template_id: str  # flows/<template_id>.yaml（不含扩展名）


@app.post("/api/flows/{flow_id}/template")
async def bind_flow_template(flow_id: str, req: BindTemplateRequest):
    """给项目绑定执行流程模板（写入实例 JSON 的 template 字段）。

    立项后选择「用哪套流程执行该项目」：任务分派、监察、验收均按
    该模板进行。重复调用覆盖。

    2026-08-24 根治（doker 事故）：绑定同时重写项目级 yaml
    （flows/{flow_id}.yaml，占位符替换），展示与执行同一份定义；
    当前 step 不在新模板步骤集时 409 拒绝（防执行错位）。
    """
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"项目不存在: {flow_id}")
    tid = (req.template_id or "").strip()
    # 防路径穿越 + 模板必须存在
    if not tid or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tid):
        raise HTTPException(status_code=400, detail=f"非法模板 id: {tid!r}")
    tpath = FLOWS_DIR / f"{tid}.yaml"
    if not tpath.exists():
        raise HTTPException(status_code=404, detail=f"流程模板不存在: {tid}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")

    # ── 根治（2026-08-24 doker 事故）：重写项目级 yaml ──────────
    # 只记 JSON 字段会造成双定义源（展示=A/B options，执行=旧 yaml 无
    # options 走词典），「A」被判驳回打回。绑定时把模板内容落到项目级
    # yaml（占位符替换与 gen_project 同源），展示与执行永远同一份。
    #
    # 兼容性守卫：实例当前 step 必须存在于新模板步骤集中，否则拒绝重写
    # （防止进行中的流程被切到没有该步骤的定义上，引擎下一步直接失踪）。
    new_flow = _load_yaml_flow(tid)
    new_ids = {s.get("id") for s in (new_flow or {}).get("steps", [])}
    cur_step = st.get("step")
    if cur_step and cur_step not in new_ids:
        raise HTTPException(
            status_code=409,
            detail=(f"当前步骤 {cur_step} 不在模板 {tid} 的步骤中，"
                    f"拒绝重写项目流程（防执行错位）。"
                    f"建议在流程起点/拍板点绑定，或保持现有模板"))
    try:
        src_text = tpath.read_text(encoding="utf-8")
        proj_yaml = (src_text
                     .replace("PROJECT_PLACEHOLDER", flow_id)
                     .replace("REVIEW_PLACEHOLDER", f"004-xuanwu-tech-review-{flow_id}")
                     .replace("RESULT_PLACEHOLDER", f"003-{flow_id}-implement-report"))
        # name 行必须等于项目名（引擎按文件名寻址，保持一致防未来踩坑）
        proj_yaml = re.sub(r"^name:.*$", f"name: {flow_id}",
                           proj_yaml, count=1, flags=re.MULTILINE)
        target = FLOWS_DIR / f"{flow_id}.yaml"
        if target.exists():
            bak = target.with_suffix(
                f".yaml.bak-bind-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        target.write_text(proj_yaml, encoding="utf-8")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重写项目流程失败: {e}")

    old = st.get("template")
    st["template"] = tid
    hist = st.setdefault("history", [])
    hist.append({
        "step": st.get("step", "?"),
        "action": "bind_template",
        "detail": f"绑定流程模板: {old or '（无）'} → {tid}",
        "timestamp": datetime.now().isoformat(),
    })
    try:
        _save_instance(fp, st)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写实例失败: {e}")
    return {"status": "ok", "flow": flow_id, "template": tid}


@app.get("/api/flow-templates/current/{flow_id}")
async def get_current_template(flow_id: str):
    """查询项目当前绑定的流程模板（未绑定为 null）。"""
    fp = INSTANCES_DIR / f"{flow_id}.json"
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"项目不存在: {flow_id}")
    st = _load_json(fp)
    if not st:
        raise HTTPException(status_code=500, detail=f"读取失败: {flow_id}")
    return {"flow": flow_id, "template": st.get("template")}


# ── Kanban 可视化 + 催动 + 父项目数据源合并（2026-08-24 六项优化 P0）───────
#
# 数据源：Hermes kanban 库（只读）——tasks 表字段含 assignee/status/
#   last_heartbeat_at/consecutive_failures 等，面板此前从未消费。
# 状态推导规则（derived_state，前端直接渲染，不再自行判断）：
#   done            完成
#   running         执行中（有 worker 且心跳 < STALE_SECONDS）
#   stuck           疑似卡住（running 但心跳超时）
#   blocked         连续失败 ≥2 次（受阻，需要人看失败原因）
#   unassigned-agent 派给了不存在的执行者（如 assignee=flow-engine），没人会认领
#   ready           待认领（正常排队）
#   other           引擎返回的未知状态原样透出
# 催动边界（user 2026-08-24 拍板）：「催一下」= 往该 agent 的待办桥队列
#   （pending-<agent>.json）推一条提醒，机器送信、人工触发；不代投拍板、
#   不改任务状态。hermes 走 pending-main.json 既有命名（notice_watch.py 同源）。

import sqlite3
import time

KANBAN_DB = Path(
    os.environ.get(
        "FLOW_KANBAN_DB",
        str(Path.home() / "AppData/Local/hermes/kanban/boards/multi-agents/kanban.db"),
    )
)
WAKE_STATE_DIR = Path(
    os.environ.get(
        "FLOW_WAKE_STATE_DIR", str(Path.home() / ".flow-engine/wake-state")
    )
)
NUDGE_AGENTS = {
    "hermes": "pending-main.json",
    "athena": "pending-athena.json",
    "xuanwu": "pending-xuanwu.json",
    "duanmu": "pending-duanmu.json",
}
NUDGE_FALLBACK_AGENT = "hermes"  # assignee 无效时的兜底催动对象（executor_dispatch 维护方）
STALE_SECONDS = 15 * 60          # 心跳超过 15 分钟视为疑似卡住
READY_STALE_SECONDS = 30 * 60    # ready 超过 30 分钟无人领 → 前端亮黄灯
NUDGE_COOLDOWN_SECONDS = 600     # 同一任务 10 分钟内不重复入队
ENGINE_TITLE_RE = re.compile(r"^\[引擎:([^\]]+)\]\s*(.+)$")


def _derive_task_state(t: dict) -> tuple[str, bool]:
    """(derived_state, ready_stale)。纯函数便于冒烟。"""
    st = (t.get("status") or "").lower()
    if st == "done":
        return "done", False
    hb = t.get("last_heartbeat_at")
    hb_age = (time.time() - float(hb)) if hb else None
    if st == "running":
        if hb_age is None or hb_age > STALE_SECONDS:
            return "stuck", False
        return "running", False
    if st == "ready":
        if (t.get("consecutive_failures") or 0) >= 2:
            return "blocked", False
        if (t.get("assignee") or "").strip() not in NUDGE_AGENTS:
            return "unassigned-agent", False
        created = t.get("created_at")
        try:
            created_val = float(created)
            ready_stale = (time.time() - created_val) > READY_STALE_SECONDS
        except (TypeError, ValueError):
            ready_stale = False
        return "ready", ready_stale
    return st or "other", False


def _load_kanban_tasks(limit: int = 300) -> list[dict]:
    """只读拉取 kanban 任务并附推导状态。库不存在时返回空表（前端显示空态）。"""
    if not KANBAN_DB.exists():
        return []
    uri = f"file:{KANBAN_DB.as_posix()}?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute(
            "SELECT id, title, assignee, status, created_at, started_at,"
            " completed_at, worker_pid, last_heartbeat_at, idempotency_key,"
            " consecutive_failures, last_failure_error"
            " FROM tasks ORDER BY rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        db.close()
    out: list[dict] = []
    now_iso = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        t = dict(r)
        m = ENGINE_TITLE_RE.match(t.get("title") or "")
        state, ready_stale = _derive_task_state(t)
        t["flow"] = m.group(1) if m else None
        t["stepTitle"] = m.group(2) if m else t.get("title")
        t["iteration"] = None
        if m:
            im = re.search(r"\(iter (\d+)\)", m.group(2))
            if im:
                t["iteration"] = int(im.group(1))
        t["derivedState"] = state
        t["readyStale"] = ready_stale
        t["failureCount"] = t.pop("consecutive_failures", None)
        t["fetchedAt"] = now_iso
        out.append(t)
    return out


@app.get("/api/kanban/tasks")
async def list_kanban_tasks(limit: int = Query(default=300, le=1000)):
    """Kanban 任务列表（含执行者 + 推导状态），供看板页与项目详情消费。"""
    tasks = _load_kanban_tasks(limit=limit)
    return {"tasks": tasks, "count": len(tasks)}


class NudgeRequest(BaseModel):
    message: str | None = None  # 可选补充说明（拼进队列条目 note）


@app.post("/api/kanban/tasks/{task_id}/nudge")
async def nudge_kanban_task(task_id: str, req: NudgeRequest | None = None):
    """人工催动：往执行者的待办桥队列 append 一条提醒（不代投拍板、不改任务状态）。

    防骚扰：同一任务 10 分钟内已催过 → 409。assignee 无效时兜底催 hermes。
    """
    tasks = {t["id"]: t for t in _load_kanban_tasks(limit=1000)}
    t = tasks.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"kanban 任务不存在: {task_id}")

    assignee = (t.get("assignee") or "").strip()
    target, qname = (
        (assignee, NUDGE_AGENTS[assignee])
        if assignee in NUDGE_AGENTS
        else (NUDGE_FALLBACK_AGENT, NUDGE_AGENTS[NUDGE_FALLBACK_AGENT])
    )
    qpath = WAKE_STATE_DIR / qname

    # 冷却检查：队列里已有同任务的近期催动条目则拒绝重复
    marker = f"[nudge:{task_id}]"
    try:
        queue = json.loads(qpath.read_text(encoding="utf-8")) if qpath.exists() else []
        if not isinstance(queue, list):
            queue = []
    except Exception:
        queue = []
    for it in queue:
        if isinstance(it, dict) and marker in (it.get("note") or ""):
            try:
                ts = datetime.fromisoformat(str(it.get("ts")))
                if (datetime.now() - ts).total_seconds() < NUDGE_COOLDOWN_SECONDS:
                    raise HTTPException(
                        status_code=409,
                        detail=f"{target} 已在 {NUDGE_COOLDOWN_SECONDS // 60} 分钟内被催过这张卡，等一等")
            except ValueError:
                continue
    note = (
        f"[nudge:{task_id}] 面板催动：kanban 卡 {task_id}"
        f"「{t.get('stepTitle')}」（项目 {t.get('flow') or '?'}，assignee={assignee or '空'}，"
        f"status={t.get('status')}）。请认领/推进该卡；若已完成请忽略。"
    )
    if req and req.message:
        note += f" 附言：{req.message}"
    entry = {
        "file": f"kanban://{KANBAN_DB.as_posix()}#{task_id}",
        "fname": f"kanban-nudge-{task_id}",
        "ts": datetime.now().isoformat(timespec="seconds"),
        "source": "panel-manual-push",
        "note": note,
    }
    queue.append(entry)
    try:
        WAKE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        qpath.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入待办桥队列失败: {e}")
    return {
        "status": "ok",
        "target": target,
        "queueFile": str(qpath),
        "effectiveTarget": target if target == assignee else f"{assignee!r}无效→兜底{target}",
    }


# 父项目下拉数据源：实例 ∪ Project 根目录（隐藏目录排除规则在此维护）
PROJECT_HIDE_EXACT = {
    "_safety", "archive-governor", "agent-notify-ack", "exchange",
    "exchange-debloat-workspace", "flow-engine-setup", "test-project",
}
PROJECT_HIDE_PREFIX = (".", "_", "archive-", "rename-backup", "mem0-backup")


@app.get("/api/projects")
async def list_projects():
    """父项目候选列表 = 引擎实例 ∪ Project 根目录真实项目。

    背景（2026-08-23 遗留）：改进项目的下拉原来只读实例列表，flow-engine 本体
    这类早于引擎诞生的根项目永远选不到。本端点合并两个来源：
      projects[]   —— 实例项目（全量字段，含 parent/template/status）
      dirProjects[] —— 有目录无实例的项目（id 即目录名，hasInstance=false）
    """
    projects: list[dict] = []
    for fp in sorted(INSTANCES_DIR.glob("*.json")):
        st = _load_json(fp) or {}
        projects.append({
            "id": fp.stem,
            "title": st.get("title") or fp.stem,
            "parent": st.get("parent"),
            "template": st.get("template"),
            "status": st.get("status"),
            "hasInstance": True,
            "isDir": (PROJECT_ROOT / fp.stem).is_dir(),
        })
    seen = {p["id"] for p in projects}
    dir_projects: list[dict] = []
    if PROJECT_ROOT.exists():
        for d in sorted(PROJECT_ROOT.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            if d.name in PROJECT_HIDE_EXACT or d.name.startswith(PROJECT_HIDE_PREFIX):
                continue
            dir_projects.append({
                "id": d.name, "title": d.name, "parent": None, "template": None,
                "status": None, "hasInstance": False, "isDir": True,
            })
    return {"projects": projects, "dirProjects": dir_projects}


# ── 前端静态托管（2026-08-24 P2 工具包：单端口 8650 全家桶）────────────
# dist 存在时托管 SPA（pnpm build 产物）；所有 API 路由先注册所以优先匹配。
# 用 catch-all 而非 StaticFiles(html=True) 挂根：后者对 /kanban 等客户端
# 路由直接 404，没有 SPA fallback；catch-all 把未命中路径都送回 index.html。
# 不存在 dist 时跳过（纯 API 模式，前端照旧走 vite dev server）。
FRONTEND_DIST = Path(
    os.environ.get("FLOW_FRONTEND_DIST")
    or (
        _PKG_ROOT / "panel" / "frontend" / "dist"
        if (_PKG_ROOT / "panel" / "frontend" / "dist").is_dir()
        else str(Path(__file__).resolve().parent.parent / "frontend" / "dist")
    )
)

# 显式 MIME 表：Windows 注册表常缺 .js 映射，mimetypes 会返回 text/plain，
# ES 模块要求 JS MIME 否则浏览器静默拒载（2026-08-24 冒烟实证）。
EXT_MIME = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

if FRONTEND_DIST.is_dir():
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        """非 /api 未命中路径 → 静态文件或 index.html（SPA 客户端路由兜底）。"""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (FRONTEND_DIST / full_path).resolve()
        # 防目录穿越：解析后必须仍在 dist 内。
        # ⚠️ 用 Path.is_relative_to 而非 str.startswith——Windows 下 resolve()
        # 返回反斜杠路径，与正斜杠字面量比较永远 False（2026-08-24 冒烟实证）。
        if full_path and candidate.is_relative_to(FRONTEND_DIST) and candidate.is_file():
            mime = EXT_MIME.get(candidate.suffix.lower())
            return FileResponse(candidate, media_type=mime)
        return FileResponse(
            FRONTEND_DIST / "index.html", media_type="text/html"
        )


# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # FLOW_PORT：打包部署多实例并存用（如 8651 起快照副本验证）；缺省 8650
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("FLOW_PORT", "8650")),
        log_level="info",
    )

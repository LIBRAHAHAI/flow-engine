"""cron_coupling.py — 引擎心跳 cron 与引擎生命周期的自动耦合（2026-10-10 任务书）。

背景：流程引擎的推进依赖常驻心跳 cron（3d807760e7a6，每 5 分钟扫 instances/
推一拍），但心跳此前与"引擎是否真的在跑"无关——实例全部终态后心跳仍在每
5 分钟空转（最近一次实例活动 2026-08-16），人工判断"引擎死活"又不可靠。
本模块把两件事焊死：

    引擎在跑（存在活动实例）⇒ 心跳 job 处于 scheduled
    引擎全停（无活动实例）  ⇒ 心跳 job 自动 pause

接入点（缺一不可，见任务书 §二.2）：
  A. 实例创建入口（gen_project.gen——面板 create_flow 也经它落盘 / engine._load_state
     隐式建档后的首次 _save_state）→ reconcile_silent()
  B. 实例终态收口（engine._save_state 落 completed/failed 后；--reset 删除后；
     面板 pause/resume 状态转移）→ reconcile_silent()
  C. 心跳 wrapper 每次运行先 reconcile() 自愈（防 A/B 漏挂）

硬性约束：
  - 异常隔离：hermes 不在 PATH / 超时 / 非零退出 → 只写 logs/cron_coupling.log，
    绝不抛给调用方、绝不阻塞引擎推进或实例创建（reconcile_silent 兜底）。
  - 幂等：目标态已达成时不再 resume/pause（返回 'unchanged'），重复调用无副作用。
  - 子进程限时 20s（HERMES_TIMEOUT_S）。
  - hermes 路径：shutil.which 解析，失败回退 venv 绝对路径；再失败写日志并由
    reconcile_silent 静默跳过，禁止硬编码猜测后静默失败。

活动实例口径（与 engine_heartbeat_wrapper._discover_running_flows 严格同源，
"心跳在跑 ⟺ wrapper 有活干"）：status == "running" 且有 flow 字段——wrapper
只推进 running，paused/completed/failed 一律跳过（2026-08-16 自动发现改造的
现行语义），故 paused 实例不维持心跳；面板 resume 挂钩会把它接回来。

测试隔离：环境变量 CRON_COUPLING_DISABLE=1（conftest 已设置）时
reconcile_silent 直接跳过——pytest 回归绝不触达真实 cron job。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

# ── 本引擎对应的 cron job（wrapper 从这里读，不许散落硬编码）──
COUPLED_JOB_ID = "3d807760e7a6"  # 引擎心跳-双流程-5min（hermes cron）

# hermes 可执行文件兜底路径（本机无系统级 Python；hermes 装在 hermes-agent venv）
HERMES_PATH_FALLBACK = Path(os.environ.get(
    "HERMES_EXE",
    str(Path.home() / "AppData/Local/hermes/hermes-agent/venv/Scripts/hermes.exe")))
HERMES_TIMEOUT_S = 20

# 终态口径（engine.run() 拒跑集合；paused 非"推进意义终态"但 wrapper 不推进它，
# 不计入活动口径——见模块 docstring）
TERMINAL_STATUSES = ("completed", "failed")

LOG_FILE = Path(__file__).resolve().parent / "logs" / "cron_coupling.log"
_DISABLE_ENV = "CRON_COUPLING_DISABLE"
_TRUTHY = ("1", "true", "yes", "on")


def _log(msg: str) -> None:
    """追加一行日志；日志失败静默（绝不因日志问题影响主流程）。"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass


def log_error(where: str, exc: BaseException) -> None:
    """公开给 wrapper 的错误落日志入口（wrapper 自身 except 分支复用）。"""
    _log(f"[ERROR] {where}: {exc.__class__.__name__}: {exc}")


def disabled() -> bool:
    """测试禁用开关：CRON_COUPLING_DISABLE 置真时 reconcile_silent 直接跳过。"""
    return os.environ.get(_DISABLE_ENV, "").strip().lower() in _TRUTHY


def _instances_dir() -> Path:
    """实例目录。engine.py 本身无 env 覆盖，这里提供 FLOW_INSTANCES_DIR 仅作
    测试/沙箱钩子（conftest 走 CRON_COUPLING_DISABLE，不依赖它）；缺省 = 本模块
    所在 engine 目录下的 instances/。"""
    env = os.environ.get("FLOW_INSTANCES_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "instances"


def active_instances() -> list[str]:
    """当前活动流程实例名（口径与 engine_heartbeat_wrapper 发现逻辑一致）。"""
    d = _instances_dir()
    if not d.is_dir():
        return []
    out: list[str] = []
    for fp in sorted(d.glob("*.json")):
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 撕裂实例容忍跳过（wrapper 同款）
            continue
        if st.get("status") == "running" and st.get("flow"):
            out.append(str(st["flow"]))
    return out


def _hermes_exe() -> str:
    exe = shutil.which("hermes")
    if exe:
        return exe
    if HERMES_PATH_FALLBACK.is_file():
        return str(HERMES_PATH_FALLBACK)
    raise FileNotFoundError(
        f"hermes 可执行文件解析失败（shutil.which('hermes') 与兜底 "
        f"{HERMES_PATH_FALLBACK} 均不可用）")


def _run_hermes(*args: str) -> str:
    """子进程调 hermes CLI，限时 20s，非零退出抛错（调用方兜底）。"""
    exe = _hermes_exe()
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    r = subprocess.run([exe, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=HERMES_TIMEOUT_S, env=env)
    if r.returncode != 0:
        raise RuntimeError(
            f"hermes {' '.join(args)} 退出码 {r.returncode}: "
            f"{(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def job_state() -> str:
    """本 job 当前状态（'active' / 'paused' / 其他）。

    必须子进程实查 `hermes cron list --all`（paused job 在默认列表里不显示，
    --all 才可见，实测），禁止靠猜。找不到 job 抛错（调用方兜底）。
    """
    out = _run_hermes("cron", "list", "--all")
    m = re.search(rf"^\s*{re.escape(COUPLED_JOB_ID)}\s+\[(\w+)\]", out, re.M)
    if not m:
        raise RuntimeError(f"hermes cron list 输出中找不到 job {COUPLED_JOB_ID}")
    return m.group(1)


def ensure_heartbeat_on() -> None:
    """有活动实例时保证心跳 job 处于 scheduled；已是则什么都不做（幂等）。"""
    state = job_state()
    if state == "active":
        return
    _run_hermes("cron", "resume", COUPLED_JOB_ID)
    _log(f"resume {COUPLED_JOB_ID}（原状态 [{state}]，存在活动实例）")


def reconcile() -> str:
    """按当前实例状态对齐心跳开关，返回 'on'（本次 resume）/ 'off'（本次 pause）
    / 'unchanged'（已是目标态）。

    本函数可能抛错（hermes 不可用等）——引擎/面板/wrapper 侧统一走
    reconcile_silent()，wrapper 自身 except 后走降级路径。
    """
    active = active_instances()
    state = job_state()
    if active:
        if state == "active":
            return "unchanged"
        ensure_heartbeat_on()
        return "on"
    if state == "paused":
        return "unchanged"
    _run_hermes("cron", "pause", COUPLED_JOB_ID)
    _log(f"pause {COUPLED_JOB_ID}（无活动实例，原状态 [{state}]）")
    return "off"


def reconcile_silent() -> None:
    """reconcile 的静默包装：任何异常只写日志，绝不抛给调用方、绝不阻塞引擎主流程。"""
    if disabled():
        return
    try:
        reconcile()
    except Exception as exc:  # noqa: BLE001 — 异常隔离红线（任务书 §二.1）
        log_error("reconcile_silent", exc)

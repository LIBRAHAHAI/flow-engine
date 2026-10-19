"""engine_heartbeat_wrapper.py — 引擎心跳 cron wrapper（no_agent 模式）。

推进 flow-engine 项目下所有**运行中**流程实例（status=running 自动发现，
不再硬编码名单——2026-08-16 tank-battle-game 开引擎后漏挂心跳的根因修复：
原 FLOWS 硬编码 [032, flow-engine-build]，新项目开引擎不会自动加入）。
输出：引擎有推进时输出一行；纯等待（无变化）时静默（供 cron 去重）。

流程推进规则：
- wait 等待中（产物未出现）→ 静默
- 有推进（产物出现/步骤变更/拍板点到达）→ 输出
- 拍板点到达 → 输出醒目提示（user 需拍板）
- completed/failed/paused 实例 → 不轮询（终态/暂停无需推进）
"""
import json
import subprocess
import sys
from pathlib import Path

ENGINE_PY = str(Path(__file__).resolve().parent.parent / "engine" / "engine.py")
INSTANCES_DIR = Path(ENGINE_PY).parent / "instances"

# 生命周期耦合（2026-10-10 任务书 C）：每次运行先 reconcile() 自愈——
# 有 running 实例 ⇒ 心跳保证 scheduled；全停 ⇒ 本拍把自己 pause。job_id/活动
# 口径单一来源 = flow-engine/engine/cron_coupling.py（COUPLED_JOB_ID），不许在
# 此散落硬编码。模块缺失只降级耦合（心跳推进照旧），不响亮报错（与 ww 侧不同：
# 本脚本历史上无 preflight，保持静默降级语义）。
try:
    sys.path.insert(0, str(Path(ENGINE_PY).parent))
    import cron_coupling
except Exception:  # noqa: BLE001 — 耦合降级不拦心跳推进
    cron_coupling = None


def _discover_running_flows() -> list[str]:
    """自动发现运行中流程实例（instances/*.json 且 status=running）。

    硬编码名单的教训（2026-08-16）：flow-engine-build 收官后 032+自举双双
    completed，新开 tank-battle-game 时心跳漏挂 → 流程永不推进。改为扫描
    instances 目录，终态（completed/failed）与暂停（paused）自动跳过。
    """
    if not INSTANCES_DIR.is_dir():
        return []
    flows: list[str] = []
    for fp in sorted(INSTANCES_DIR.glob("*.json")):
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue  # 撕裂/半写实例容忍，跳过
        if st.get("status") == "running" and st.get("flow"):
            flows.append(st["flow"])
    return flows


def _heartbeat(flow: str) -> str | None:
    r = subprocess.run(
        [sys.executable, ENGINE_PY, "--flow", flow],
        capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        return f"[engine:{flow}] 心跳异常 exit={r.returncode}: {r.stderr[-200:]}"
    out = r.stdout.strip()
    if not out:
        return None
    # 纯等待（无变化，如 wait 步骤等产物）→ 静默
    # 拍板点等待（⏸ 拍板点... 等待拍板）→ 投递（M3.1: 拍板点到达必须通知 user）
    if ("等待" in out or "⏳" in out) and "拍板点" not in out:
        return None
    return out


def main() -> int:
    # 生命周期耦合（2026-10-10 任务书 C）：先对齐心跳开关，再推进。
    # CRON_COUPLING_DISABLE=1（测试回归/紧急人工关断）时本段整体跳过。
    flows = _discover_running_flows()
    coupling = None
    if cron_coupling is not None and not cron_coupling.disabled():
        try:
            coupling = cron_coupling.reconcile()
        except Exception as e:  # noqa: BLE001 — 异常隔离：耦合失败不拦心跳推进
            cron_coupling.log_error("engine wrapper reconcile", e)
    if not flows:
        # 自停告知（有输出=会投递，属正常告知非错误，任务书 §二.2-C）：
        # reconcile 成功时 job 已 paused，本行只会投递一次。
        if cron_coupling is not None:
            job_id = getattr(cron_coupling, "COUPLED_JOB_ID", "?")
            print(f"[coupling] 无运行中流程实例，心跳已自停（job={job_id}，reconcile={coupling}）")
        return 0
    reports = []
    for flow in flows:
        out = _heartbeat(flow)
        if out:
            reports.append(out)

    if not reports:
        return 0  # 全部静默

    print("【引擎心跳】")
    for r in reports:
        print(f"  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

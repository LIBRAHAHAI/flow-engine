"""engine.py — 流程驱动引擎（正式版 v0.4，2026-08-16 缺陷修复）。

基于 proto v0.2 验证的架构，升级为可对接真实系统的版本：
- 支持 --start-step 从任意步骤续跑（032 已进行到 critique，从中点接入）
- 步骤类型扩展：dispatch/verify/decision/done + wait(等待外部产物)
- 实例状态持久化到 engine/instances/<flow>.json

2026-08-16 缺陷修复（t_62463005）：
- done 步骤收尾：归档本 flow 全部残留派发卡（ready/running/todo），
  防止 dispatcher 恢复后把旧迭代卡批量 spawn 成幽灵任务（实测 12 卡）

2026-08-24 ft_impl1：实例 json 新增 doker_enabled 字段（flow-engine-doker 容器化运行环境，
详见 exchange/flow-engine-doker/002-design-doc.md §2.2）。当 instance.json 写：
  - doker_enabled 缺失/false：执行器在本地跑（K3 零回归红线）
  - doker_enabled=true + doker_project_dir=<项目根>：执行器在 docker compose exec 容器内跑
  - doker_enabled=true 但缺 doker_project_dir：立即 RuntimeError（002 P4 失败显式原则）

用法：
  python engine.py --flow 032-mem0-hmem --step <id>   # 推进到指定步骤（或按模板 next）
  python engine.py --flow 032-mem0-hmem --status      # 查看实例状态
  python engine.py --flow 032-mem0-hmem --advance     # 心跳：推进一个动作
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml

import kanban_client

import cron_coupling  # 生命周期耦合（2026-10-10）：建档/终态收口 → 心跳 cron 对齐

ENGINE_DIR = Path(__file__).parent
FLOWS_DIR = ENGINE_DIR / "flows"
INSTANCES_DIR = ENGINE_DIR / "instances"

# 步骤类型
T_DISPATCH = "dispatch"    # 派发执行（调用执行器）
T_VERIFY = "verify"        # 验收（调用验收器）
T_DECISION = "decision"    # 拍板点（暂停等 user）
T_DONE = "done"            # 终态
T_WAIT = "wait"            # 等待外部产物出现（轮询 artifact 存在性）
T_DISPATCH_GROUP = "dispatch_group"  # 并行组（2026-09-10 user 拍板）：一次派 N 张卡，
                            # 各卡幂等键独立，组后接 wait 屏障等全部产物齐备。
                            # 根治 P5（wp_publish 素材五路链式放行串行效率）。
T_DISCUSS = "discuss"      # 立项讨论（访谈模式，等 user 逐轮回答，spec 确认后继续）


def _resolve_artifact(artifact: str) -> Path | None:
    """解析屏障/验收产物路径（2026-08-30 wait 屏障断链修复）。

    精确路径：存在即返回（旧行为不变，向后兼容）。
    含通配符（glob）：取 mtime 最新的匹配文件——exwrite 取号不固定，
    屏障按 slug 模式匹配（如 exchange/artifacts/*-grc-panel-report*.md）。
    无匹配返回 None。
    """
    ap = Path(artifact)
    if not ap.exists():
        if "*" not in artifact and "?" not in artifact:
            return None
        matches = [m for m in ap.parent.glob(ap.name) if m.is_file()]
        if not matches:
            return None
        ap = max(matches, key=lambda m: m.stat().st_mtime)
    return ap


class FlowEngine:
    def __init__(self, flow_name: str):
        self.flow_name = flow_name
        self.template_path = FLOWS_DIR / f"{flow_name}.yaml"
        if not self.template_path.exists():
            raise FileNotFoundError(f"流程模板不存在: {self.template_path}")
        self.template = yaml.safe_load(self.template_path.read_text(encoding="utf-8"))
        self.instance_path = INSTANCES_DIR / f"{flow_name}.json"

    # ---- 状态读写 ----
    def _load_state(self) -> dict:
        if not self.instance_path.exists():
            return {"flow": self.flow_name, "step": None, "iteration": 0,
                    "history": [], "status": "idle"}
        return json.loads(self.instance_path.read_text(encoding="utf-8"))

    def _save_state(self, state: dict) -> None:
        INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
        # 生命周期耦合（2026-10-10）：首次落盘（CLI 隐式建档，_load_state 只返回
        # 默认 idle 不落盘）或终态收口时对齐心跳开关。常规推进每步都落盘，
        # 不逐拍 reconcile（避免平白多 1-2 次 hermes 子进程调用）。
        first_write = not self.instance_path.exists()
        self.instance_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if first_write or state.get("status") in cron_coupling.TERMINAL_STATUSES:
            cron_coupling.reconcile_silent()

    def _step_by_id(self, sid: str) -> dict:
        for s in self.template["steps"]:
            if s["id"] == sid:
                return s
        raise KeyError(f"步骤不存在: {sid}")

    # ---- 步骤执行 ----
    def _run_in_env(self, cmd: list[str]) -> "subprocess.CompletedProcess":
        """按 instance json doker_enabled 字段决定本地或容器内执行（002-design-doc.md §2.2）。

        - doker_enabled 缺失/false → 本地 subprocess.run（零回归，K3 红线）
        - doker_enabled=true + doker_project_dir → docker compose exec 容器内执行
        - doker_enabled=true 但缺 doker_project_dir → 显式 RuntimeError（P4 失败显式原则）

        exec 用 -T 无 TTY（与 capture_output 管道模式匹配；交互 shell 走 doker-shell.sh 有 TTY）。
        shlex.quote 逐参包裹避免空格/引号注入风险。

        执行环境区分由 _do_dispatch 给 history.log 加 [doker] 前缀（§2.3）——
        本方法不额外打印，避免污染 subprocess.run 的 capture_output。
        """
        st = self._load_state()
        if not st.get("doker_enabled"):
            return subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        proj = st.get("doker_project_dir")
        svc = st.get("doker_service", "main")
        if not proj:
            # 显式开关打开但缺路径 → 立即报错（不回退本地，P4 失败显式原则）
            raise RuntimeError(
                "[doker] doker_enabled=true 但 instance.json 缺 doker_project_dir（不回退本地）"
            )
        inner = " ".join(shlex.quote(c) for c in cmd)
        # Windows 路径 → docker compose -f 在 daemon 端用主机路径；compose 自行转换跨平台边界。
        compose_path = f"{proj}/docker/compose.yaml"
        # 2026-09-01 enginefix2 首航缺陷：执行器脚本在 ENGINE_DIR（引擎目录），
        # 而 compose 模板只挂载项目目录 → 容器内找不到脚本。doker 模式下把
        # ENGINE_DIR 只读挂载到 /engine，路径参数同步映射（项目目录→/workspace）。
        inner = inner.replace(str(ENGINE_DIR).replace("\\", "/"), "/engine") \
                     .replace(str(ENGINE_DIR), "/engine") \
                     .replace("\\", "/")
        # exec 不能加 -v 挂载（只有 run 支持）；引擎目录由 compose.yaml 声明挂载。
        # 模板 compose.template.yaml 需含 ENGINE_DIR 挂载（gen_project 生成时注入）。
        return subprocess.run(
            ["docker", "compose", "-f", compose_path,
             "exec", "-T", svc, "bash", "-lc", inner],
            capture_output=True, text=True, timeout=300,
        )

    def _do_dispatch(self, step: dict, state: dict) -> dict:
        """派发：调用执行器。executor 字段指向脚本；缺省用引擎内置记录。

        2026-08-24 ft_impl1：cmd 经 _run_in_env 包装，doker_enabled=true 时进容器 exec。
        log 字段按 instance.json doker_enabled 加 [doker] 前缀（002 §2.3 排障一眼区分）。
        """
        executor = step.get("executor")
        state["iteration"] += 1
        prefix = "[doker] " if state.get("doker_enabled") else ""
        # 2026-09-10 修复（we-write-engine 首航实证）：doker 模式下容器内
        # kanban DB 只读（/host-hermes:ro），executor 的 reopen_done_card
        # 路径（done 卡打回）会 sqlite3 readonly crash。卡 done 后引擎心跳
        # 不应再次进入 dispatch 重开语义——wait 屏障的 card_done 锚点会放行，
        # 这里提前幂等跳过，与 F7 短路精神一致（写操作永远落主机/不回退）。
        if state.get("doker_enabled"):
            try:
                _card = kanban_client.resolve_step_card(self.flow_name, step["id"])
                if _card and _card.get("status") == "done":
                    log = f"{prefix}dispatch {step['id']}: 卡 {_card['id']} 已 done（幂等跳过，不重开）"
                    state["history"].append({"step": step["id"], "iteration": state["iteration"],
                                             "action": "dispatch", "log": log})
                    return {"next": step.get("next"), "log": log}
            except Exception:  # noqa: BLE001 — 查询失败走原路径（不阻断派发）
                pass
        if executor:
            # 2026-09-01 enginefix2 首航缺陷：doker 模式下 sys.executable 是 Windows
            # venv 路径，容器内不存在（'command not found'）。doker_enabled=true 时
            # 解释器换为容器内 python3；本地模式保持 sys.executable（K3 零回归）。
            interpreter = "python3" if state.get("doker_enabled") else sys.executable
            cmd = [interpreter, str(ENGINE_DIR / executor),
                   "--flow", self.flow_name, "--step", step["id"]]
            r = self._run_in_env(cmd)
            if r.returncode != 0:
                raise RuntimeError(f"执行器失败: {r.stderr[-300:]}")
            log = (r.stdout.strip()[:300] if r.stdout else "")
            # 2026-09-01 enginefix2 首航补丁：doker 容器无法执行 Windows hermes.exe，
            # 容器内 executor 输出 DOKER_CARD_JSON marker，主机侧代执行 kanban create
            # （写操作永远落主机，容器只跑只读/计算逻辑）。
            if state.get("doker_enabled") and "DOKER_CARD_JSON:" in (r.stdout or ""):
                m = re.search(r"DOKER_CARD_JSON:(\{.*\})", r.stdout)
                if m:
                    payload = json.loads(m.group(1))
                    card = subprocess.run(
                        ["hermes", "kanban", "create", payload["title"],
                         "--assignee", payload["assignee"],
                         "--idempotency-key",
                         f"engine:{payload['flow']}:{payload['step']}",
                         "--body", payload["body"],
                         "--project", ("flow-engine"
                                       if payload["flow"] == "flow-engine-build"
                                       else "multi-agents"),
                         "--created-by", "flow-engine"],
                        capture_output=True, text=True, timeout=120)
                    if card.returncode != 0 and "already exists" not in (card.stderr + card.stdout).lower():
                        raise RuntimeError(f"主机代建卡失败: {(card.stderr or card.stdout)[-200:]}")
                    log = "[doker] 主机代建卡: " + (card.stdout or card.stderr).strip()[:200]
                    log = prefix + log
                else:
                    # 容器内执行时给 log 加 [doker] 前缀（§2.3）
                    log = prefix + (r.stdout.strip()[:300] if r.stdout else "")
            else:
                log = prefix + (r.stdout.strip()[:300] if r.stdout else "")
        else:
            log = f"{prefix}dispatch {step['id']} (iteration {state['iteration']})"
        state["history"].append({"step": step["id"], "iteration": state["iteration"],
                                 "action": "dispatch", "log": log})
        return {"next": step.get("next"), "log": log}

    def _do_dispatch_group(self, step: dict, state: dict) -> dict:
        """并行组（2026-09-10）：按 members 逐个调用执行器建卡。

        members = [{id: <step id>, ...普通 dispatch 字段}]，每个成员走一次
        executor_dispatch（幂等键 engine:<flow>:<member id> 独立，已建卡幂等跳过）。
        全部成员派完后 next 指向组屏障 wait（模板约定，artifacts 全齐才过）。
        任一成员执行器失败：fail-fast 抛错（下一轮心跳重试该组，幂等保证已建卡不重复）。
        """
        executor_name = step.get("executor", "executor_dispatch.py")
        members = step.get("members") or []
        if not members:
            raise RuntimeError(f"dispatch_group {step['id']}: 缺 members 列表")
        results = []
        for m in members:
            mid = m.get("id")
            if not mid:
                raise RuntimeError(f"dispatch_group {step['id']}: member 缺 id: {m}")
            # 成员卡派发：直接复用 dispatch 语义（executor 收 member id）
            if m.get("executor"):
                exec_name = m["executor"]
            else:
                exec_name = executor_name
            state["iteration"] += 0  # iteration 由各成员卡 title 自取实例值，组不计
            cmd = [sys.executable, str(ENGINE_DIR / exec_name),
                   "--flow", self.flow_name, "--step", mid]
            r = self._run_in_env(cmd)
            if r.returncode != 0:
                raise RuntimeError(
                    f"dispatch_group {step['id']} 成员 {mid} 执行器失败: {(r.stderr or r.stdout or '')[-300:]}")
            results.append(f"{mid}: {(r.stdout or '').strip().splitlines()[0][:80] if r.stdout else 'ok'}")
        return {"next": step.get("next"),
                "log": f"✅ dispatch_group {step['id']}: {len(members)} 卡并行已派 ({'; '.join(results)})"}

    def _do_verify(self, step: dict, state: dict) -> dict:
        """验收：检查外部产物是否满足条件（条件 = artifact 存在 + status 达标）。

        2026-08-20 C 治本修复（flow-engine-frontend-v2 verify_frontend 空转 10 轮教训）：
        status 未达标时分两种语义——
          - status=done（实施完成、等待人工验收）→ 不打回实施步骤，改为
            派发验收卡（executor_dispatch，幂等）+ 留在本步骤等待。
            验收人实测后把产物 status 改为 accepted/closed → 下次心跳 PASS；
            验收不通过改其他值（rejected/failed）→ 走 on_fail 打回。
          - 其他值（rejected/failed 等）→ 沿用 on_fail 打回。
        防无限等待：等待轮次超过 verify_wait_max（默认 72 轮）后强制走 on_fail。

        2026-08-21 F1 修复（mechanism-fix-engine-verify-wait，002 设计 §1.2）：
        等待期间查询验收卡 kanban 状态——卡 blocked（验收人挂起等人决策）时
        记 verify_escalate（不计 waited 轮次、不超时打回、不重派卡），输出含
        「拍板点」关键字的醒目提示走 wrapper 无条件投递，等人工处置。
        """
        artifact = step.get("artifact")
        cur = None
        ok = True
        detail = ""
        if artifact:
            ap = _resolve_artifact(artifact)
            if ap is None:
                # D4/S4（2026-08-31 enginefix）：产物缺失 ≠ 实施失败，机械 FAIL 打回
                # 会误重开实施卡（simflight t_d6ab5873 iter7 误重开实证）。改走
                # verify_wait 语义：先派验收卡（executor_dispatch 幂等）再留本步等待，
                # 验收人核实后落产物/改 status → 下轮心跳重试 verify。
                import subprocess
                state["iteration"] += 1
                log = f"verify {step['id']}: 产物不存在 ({artifact}) → verify_wait 语义，建验收卡等待"
                executor = step.get("executor", "executor_dispatch.py")
                try:
                    cmd = [sys.executable, str(ENGINE_DIR / executor),
                           "--flow", self.flow_name, "--step", step["id"]]
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if r.returncode != 0:
                        log += f" | ⚠ 验收卡派发失败: {(r.stderr or '')[-120:]}"
                    else:
                        log += f" | {(r.stdout or '').strip()[-80:]}"
                except Exception as e:  # noqa: BLE001 — 派发失败不阻断等待语义
                    log += f" | ⚠ 验收卡派发异常: {e}"
                state["history"].append({"step": step["id"], "iteration": state["iteration"],
                                         "action": "verify_wait", "log": log})
                return {"next": None, "log": f"⏳ {log}"}
            else:
                # 简单检查：frontmatter status 是否在 step.status_in 内
                if step.get("status_in"):
                    txt = ap.read_text(encoding="utf-8", errors="ignore")
                    import re
                    m = re.search(r"^status:\s*(\S+)", txt, re.M)
                    cur = m.group(1) if m else "?"
                    if cur not in step["status_in"]:
                        ok = False
                        detail = f"status={cur} ∉ {step['status_in']}"
                    else:
                        detail = f"status={cur} ∈ {step['status_in']}"
                # F8b（enginefix2 000-brief #8）：验收产物 author 校验——
                # step 配 require_author 时，产物 author ≠ 验收人 → FAIL 打回
                # （防实施者自写 accepted 蒙混过关，default 代写验收报告实证）。
                if ok and step.get("require_author"):
                    txt2 = ap.read_text(encoding="utf-8", errors="ignore")
                    import re as _re
                    ma = _re.search(r"^author:\s*(\S+)", txt2, re.M)
                    author = ma.group(1) if ma else "(无 author 字段)"
                    if author != step["require_author"]:
                        ok = False
                        detail = (f"author={author} ≠ require_author={step['require_author']}"
                                  "（验收产物须由验收人本人落盘，疑似实施者代写）")
        if ok:
            state["history"].append({"step": step["id"], "action": "verify",
                                     "passed": True, "detail": detail})
            return {"next": step.get("next"), "log": f"verify {step['id']}: PASS ({detail})"}

        # status=done = 实施完成待验收：派验收卡 + 等待（不打回，防空转死循环）
        if cur == "done":
            wait_max = int(step.get("verify_wait_max", 72))
            waited = sum(1 for h in state["history"]
                         if h.get("step") == step["id"] and h.get("action") == "verify_wait_done")

            # ── F1（002 §1.2）：验收卡 blocked → escalate，不计轮不超时不重派 ──
            # 必须在 waited 超时判断之前：否则 blocked 卡仍会触发超时打回。
            try:
                card = kanban_client.resolve_step_card(self.flow_name, step["id"])
            except Exception:  # noqa: BLE001 — 查询异常降级旧逻辑（计轮等待，下轮自愈）
                card = None
            if card and card.get("status") == "blocked":
                try:
                    info = kanban_client.get_block_info(card["id"])
                except Exception:  # noqa: BLE001 — 同上，降级为未知原因
                    info = None
                reason = ((info or {}).get("reason") or "未知原因")[:200]
                state["history"].append({
                    "step": step["id"],
                    "action": "verify_escalate",
                    "card_id": card["id"],
                    "reason": reason,
                    "log": f"⚠🚨 验收卡 {card['id']} blocked，到达拍板点：需人工处置",
                })
                return {
                    "next": None,
                    "log": (f"⚠🚨 verify {step['id']}: 验收卡 {card['id']} blocked"
                            f"（拍板点，需人工处置）原因: {reason[:100]} → escalate 不计轮"),
                }
            # ── F1 结束 ──

            if waited >= wait_max:
                state["history"].append({"step": step["id"], "action": "verify",
                                         "passed": False,
                                         "detail": f"等待验收超时 {waited}/{wait_max} 轮"})
                back = step.get("on_fail")
                return {"next": back,
                        "log": f"verify {step['id']}: 等待验收超时（{waited}/{wait_max}）→ 打回 {back}"}
            import subprocess
            state["iteration"] += 1
            log = f"verify {step['id']}: 产物 status=done 待验收"
            executor = step.get("executor", "executor_dispatch.py")
            try:
                cmd = [sys.executable, str(ENGINE_DIR / executor),
                       "--flow", self.flow_name, "--step", step["id"]]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    log += f" | ⚠ 验收卡派发失败: {(r.stderr or '')[-120:]}"
                else:
                    log += f" | {(r.stdout or '').strip()[-80:]}"
            except Exception as e:  # noqa: BLE001 — 验收卡派发失败不阻断等待语义
                log += f" | ⚠ 验收卡派发异常: {e}"
            state["history"].append({"step": step["id"], "iteration": state["iteration"],
                                     "action": "verify_wait_done", "log": log})
            return {"next": None,
                    "log": f"⏳ {log}（验收人通过后产物改 accepted/closed 自动 PASS）"}

        state["history"].append({"step": step["id"], "action": "verify",
                                 "passed": False, "detail": detail})
        back = step.get("on_fail")
        return {"next": back, "log": f"verify {step['id']}: FAIL ({detail}) → 打回 {back}"}

    def _do_decision(self, step: dict, state: dict) -> dict:
        """拍板点：暂停等 user。正式版通过 decision 文件交互（引擎外部推送主会话）。

        支持模板 options（如 A/B/C 选项）：
          options:
            - key: A
              label: 接受立项 + 修订后补（推荐）
              next: m1_core
            - key: B
              label: 延迟立项
              next: review
        决策值匹配选项 key（忽略大小写/空白）；无 options 时用 accept_words 传统判断。
        """
        decision_file = INSTANCES_DIR / f"{self.flow_name}.decision"
        if not decision_file.exists():
            return {"next": None,
                    "log": f"⏸ 拍板点[{step['id']}]: {step.get('question', '待拍板')} — 等待拍板"}
        decision = decision_file.read_text(encoding="utf-8").strip()
        decision_file.unlink(missing_ok=True)
        state["history"].append({"step": step["id"], "action": "decision",
                                 "decision": decision})
        # 选项模式：决策值匹配选项 key → 走该选项的 next/on_reject
        options = step.get("options")
        if options:
            d = decision.strip().upper()
            for opt in options:
                key = str(opt.get("key", "")).strip().upper()
                if key and d == key:
                    nxt = opt.get("next")
                    if nxt:
                        return {"next": nxt, "log": f"✅ 拍板[{step['id']}]: {decision}（{opt.get('label','')}）→ 继续"}
                    return {"next": step.get("next"), "log": f"✅ 拍板[{step['id']}]: {decision} → 继续"}
            # 未匹配任何选项 → 打回 on_reject（模板兜底）
            back = step.get("on_reject")
            if back:
                return {"next": back, "log": f"❌ 拍板[{step['id']}]: {decision} 未匹配选项 → 打回 {back}"}
            return {"next": step.get("next"), "log": f"拍板[{step['id']}]: {decision}"}
        # 传统模式：accept_words 判断
        # 2026-08-24 doker 事故兜底：前端 FALLBACK 拍板按钮发「A」，
        # 词典原不含单字母 a → 被判驳回打回。加 "a"（b/c 不加：
        # 多模板 B=驳回/C=条件通过，收下会歧义）
        accept_words = ("a", "同意", "批准", "accept", "ok", "1", "是", "通过", "accepted", "拍板", "确认", "确认立项", "可以", "好", "完成", "可交付", "接受", "同意收官", "完成收官")
        if any(w in decision.lower() for w in accept_words):
            return {"next": step.get("next"), "log": f"✅ 拍板[{step['id']}]: {decision} → 继续"}
        back = step.get("on_reject")
        if back:
            return {"next": back, "log": f"❌ 拍板[{step['id']}]: {decision} → 打回 {back}"}
        return {"next": step.get("next"), "log": f"拍板[{step['id']}]: {decision}"}

    def _do_wait(self, step: dict, state: dict) -> dict:
        """等待：轮询外部产物出现（幂等，未出现则保持等待）。

        支持 artifacts 列表（多产物，全部存在才通过）或 artifact 单产物。
        支持 timeout_minutes：超过则记录超时并走 on_timeout/next（由模板定义超时策略）。

        2026-08-30 wait 屏障断链修复（t_46516f87，t_b4ebc051 实证）：
        产物缺失时联动检查屏障上游 dispatch 卡的 kanban 状态——卡已 done
        （worker 交付完毕）但产物 N 轮（wait_escalate_max，默认 3）仍缺失
        → 记 wait_escalate 醒目输出投递 user，而非静默 next: None 永等
        （参考 _do_verify 的 verify_escalate 模式：不计轮、不超时打回）。
        """
        artifacts = step.get("artifacts") or ([step["artifact"]] if step.get("artifact") else [])
        card_ids = step.get("card_done") or []
        # artifact 优先：有 artifact 时 artifact 是唯一锚点（齐全 → 直接通过，
        # 缺失才允许 card_done 兜底）；无 artifact 时 card_done 是唯一锚点。
        missing = [a for a in artifacts if _resolve_artifact(str(a)) is None]
        if not artifacts and card_ids:
            # ── card_done 锚点（t_a1f33cfc，user 拍板方案 A 2026-08-30）──
            # notify-retrofit 后双审回执走 kanban comment，19 个 flow 的 wait
            # 屏障失去 artifact 锚点。card_done 提供替代锚点：指定卡（或上游
            # dispatch 卡）status=done 即屏障通过。
            done = self._card_done_ok(card_ids, step)
            if done:
                state["history"].append({"step": step["id"], "action": "wait",
                                         "detail": f"card_done: {done}"})
                return {"next": step.get("next"),
                        "log": f"✅ wait {step['id']}: {done}（card_done 锚点）"}
            # 无 artifact 且 card_done 未命中 → 等待（不得落入"产物已出现"）
            state["history"].append({"step": step["id"], "action": "wait_missing_done",
                                     "detail": f"card_done 未命中，等待 {card_ids}"})
            return {"next": None,
                    "log": f"⏳ wait {step['id']}: 等待 card_done {card_ids}"}
        elif missing and card_ids:
            # artifact 缺失 + card_done 兜底锚点
            done = self._card_done_ok(card_ids, step)
            if done:
                state["history"].append({"step": step["id"], "action": "wait",
                                         "detail": f"card_done: {done}"})
                return {"next": step.get("next"),
                        "log": f"✅ wait {step['id']}: {done}（card_done 锚点）"}
        if missing:
            timeout_min = step.get("timeout_minutes")
            if timeout_min:
                import time
                ages = []
                for a in artifacts:
                    p = Path(str(a))
                    if p.exists():
                        ages.append((time.time() - p.stat().st_mtime) / 60)
                if ages and max(ages) > timeout_min:
                    state["history"].append({"step": step["id"], "action": "wait_timeout",
                                             "detail": f"超时 {timeout_min}min, 缺: {[str(m)[-30:] for m in missing[:3]]}"})
                    return {"next": step.get("on_timeout") or step.get("next"),
                            "log": f"⏰ wait {step['id']}: 超时 {timeout_min}min → 继续"}

            # ── 卡状态联动（t_46516f87）：屏障上游卡已 done 但产物持续缺失 → escalate ──
            # 上游卡 = 模板中 next 指向本 wait 步的 dispatch 步骤。
            esc_max = int(step.get("wait_escalate_max", 3))
            waited = sum(1 for h in state["history"]
                         if h.get("step") == step["id"] and h.get("action") == "wait_missing_done")
            up = self._upstream_dispatch(step)
            if up and waited >= esc_max:
                card = None
                try:
                    card = kanban_client.resolve_step_card(self.flow_name, up["id"])
                except Exception:  # noqa: BLE001 — 查询异常降级旧静默语义（下轮自愈）
                    card = None
                if card and card.get("status") == "done":
                    state["history"].append({
                        "step": step["id"],
                        "action": "wait_escalate",
                        "card_id": card["id"],
                        "missing": [str(m) for m in missing[:3]],
                        "log": (f"⚠🚨 wait {step['id']}: 上游卡 {card['id']} 已 done 但屏障产物 "
                                f"{missing[0]} 连续 {waited} 轮缺失——疑似交付路径与屏障契约错位，需人工处置"),
                    })
                    return {
                        "next": None,
                        "log": (f"⚠🚨 wait {step['id']}: 卡 {card['id']} done 但产物缺失 {waited}/{esc_max} 轮"
                                f"（拍板点，需人工处置）→ escalate 不计轮"),
                    }
            if up and waited >= esc_max:
                # 上游卡在途（ready/running）→ 正常等待，计轮但不 escalate
                state["history"].append({"step": step["id"], "action": "wait_missing_done",
                                         "detail": f"缺 {missing[0]}，上游卡在途 {waited}/{esc_max}"})
                return {"next": None,
                        "log": f"⏳ wait {step['id']}: 等待 {missing[0] if len(missing)==1 else f'{len(missing)}个产物'}"}
            state["history"].append({"step": step["id"], "action": "wait_missing_done",
                                     "detail": f"缺 {missing[0]} {waited}/{esc_max}"})
            return {"next": None,
                    "log": f"⏳ wait {step['id']}: 等待 {missing[0] if len(missing)==1 else f'{len(missing)}个产物'}"}
        state["history"].append({"step": step["id"], "action": "wait",
                                 "detail": f"产物齐: {len(artifacts)}"})
        return {"next": step.get("next"), "log": f"✅ wait {step['id']}: 产物已出现"}

    def _card_done_ok(self, card_ids: list, step: dict) -> str | None:
        """card_done 锚点检查（t_a1f33cfc）：任一锚点命中即返回描述串。

        锚点形态：
          - "t_xxx"     显式卡 id → 只读查 tasks 表 status=done；
          - "@upstream" 上游 dispatch 卡（_upstream_dispatch 定位）→ 卡 done 即通过。
        接入点步骤（无上游 dispatch，如 032 proposal_critique）：降级查本 flow
        实例历史——实例 status=completed（已收官）时视为历史已通过（wait 不会
        真实阻塞终态实例，此分支只为消校验错 + 续跑语义正确）。
        全部未命中返回 None（继续等待，旧行为）。
        """
        for cid in card_ids:
            cid = str(cid).strip()
            if cid == "@upstream":
                up = self._upstream_dispatch(step)
                if not up:
                    # 接入点无上游：实例已收官 → 历史已通过
                    st = self._load_state()
                    if st.get("status") == "completed":
                        return "实例已收官（@upstream 历史通过）"
                    return None
                try:
                    card = kanban_client.resolve_step_card(self.flow_name, up["id"])
                except Exception:  # noqa: BLE001 — 查询异常降级旧静默语义
                    card = None
                if card and card.get("status") == "done":
                    return f"上游卡 {card['id']} 已 done"
                continue
            try:
                row = kanban_client._connect().execute(
                    "SELECT status FROM tasks WHERE id = ?", (cid,)
                ).fetchone()
            except Exception:  # noqa: BLE001 — 查询异常降级旧静默语义
                row = None
            if row and row["status"] == "done":
                return f"卡 {cid} 已 done"
        return None

    def _upstream_dispatch(self, step: dict) -> dict | None:
        """找屏障上游 dispatch 步骤（next 指向本 wait 步的步骤，t_46516f87）。

        找不到（如屏障 next 指向别处/首步）返回 None——不联动，保持旧静默语义。
        """
        for s in self.template.get("steps", []):
            if s.get("next") == step["id"] and s.get("type") == "dispatch":
                return s
        return None

    def _do_discuss(self, step: dict, state: dict) -> dict:
        """立项讨论：等 spec 确认（status: confirmed）→ 推进。

        引擎 v2（2026-08-16）：立项不是草草建卡，而是访谈式需求澄清
        （借鉴 omh-deep-interview / grill-me）。engine 输出讨论问题，
        等 user 逐轮回答（INTERVIEW.json 记录），全部覆盖后生成 spec.md，
        user 确认（status: confirmed）→ 本步骤通过 → 推进。
        """
        spec_fp = Path(step.get("spec_file", "exchange/spec.md"))
        if not spec_fp.is_absolute():
            spec_fp = Path(step.get("project_dir", ".")) / spec_fp
        if spec_fp.exists():
            txt = spec_fp.read_text(encoding="utf-8", errors="ignore")
            import re
            m = re.search(r"^status:\s*(\S+)", txt, re.M)
            st_spec = m.group(1) if m else "?"
            if st_spec == "confirmed":
                state["history"].append({"step": step["id"], "action": "discuss",
                                         "detail": f"立项讨论完成: spec confirmed ({spec_fp.name})"})
                return {"next": step.get("next"),
                        "log": f"✅ discuss {step['id']}: 立项 spec 已确认 → 继续"}
        return {"next": None,
                "log": f"💬 discuss {step['id']}: 立项讨论中（等 user 回答 + spec 确认）"}

    def _do_done(self, step: dict, state: dict) -> dict:
        """终态：流程完成 + 收尾清理本 flow 残留派发卡（2026-08-16 缺陷修复）。

        收尾语义：flow 到 done = 全部工作已交付，本 flow 遗留的
        ready/running/todo 引擎卡全部是残留（旧迭代卡/未收口卡），归档防
        dispatcher 恢复后批量 spawn 成幽灵任务。清理失败不阻断完成
        （done 是主结果，清理是兜底）。
        """
        state["status"] = "completed"
        cleanup_log = ""
        try:
            archived = kanban_client.archive_flow_cards(self.flow_name)
            if archived:
                cleanup_log = f" | 收尾归档残留卡 {len(archived)} 张: " + ", ".join(
                    c["id"] for c in archived[:5])
                if len(archived) > 5:
                    cleanup_log += f" …共{len(archived)}张"
            else:
                cleanup_log = " | 无残留派发卡"
        except Exception as e:  # noqa: BLE001 — 收尾兜底失败不阻断完成
            cleanup_log = f" | ⚠ 收尾清理异常: {e}"
        return {"next": None, "log": "🎉 流程完成" + cleanup_log}

    # ---- 主入口 ----
    def run(self, target_step: str | None = None) -> str:
        state = self._load_state()
        if state["status"] in ("completed", "failed"):
            return f"流程已终态: {state['status']}"

        if target_step:
            # 从指定步骤续跑（首次接入：跳过已完成步骤）
            state["step"] = target_step
            state["status"] = "running"

        if state["step"] is None:
            state["step"] = self.template["steps"][0]["id"]
            state["status"] = "running"

        step = self._step_by_id(state["step"])
        t = step["type"]

        if t == T_DISPATCH:
            res = self._do_dispatch(step, state)
        elif t == T_DISPATCH_GROUP:
            res = self._do_dispatch_group(step, state)
        elif t == T_VERIFY:
            res = self._do_verify(step, state)
        elif t == T_DECISION:
            res = self._do_decision(step, state)
        elif t == T_WAIT:
            res = self._do_wait(step, state)
        elif t == T_DONE:
            res = self._do_done(step, state)
        elif t == T_DISCUSS:
            res = self._do_discuss(step, state)
        else:
            raise ValueError(f"未知步骤类型: {t}")

        if res.get("next"):
            state["step"] = res["next"]
        self._save_state(state)
        return f"[{state['step']}] {res['log']}"

    def status(self) -> str:
        state = self._load_state()
        lines = [f"流程: {self.flow_name}", f"状态: {state['status']}",
                 f"当前步骤: {state['step']}", f"迭代: {state['iteration']}"]
        # doker 容器化执行状态行（002-design-doc.md §2.3）
        if state.get("doker_enabled"):
            svc = state.get("doker_service", "main")
            proj = state.get("doker_project_dir") or "<缺 doker_project_dir>"
            lines.append(f"doker: enabled (service={svc}, dir={proj})")
        else:
            lines.append("doker: off")
        if state["history"]:
            lines.append("历史:")
            for h in state["history"][-8:]:
                lines.append(f"  {h}")
        return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="流程驱动引擎正式版")
    ap.add_argument("--flow", required=True, help="流程名（flows/<name>.yaml）")
    ap.add_argument("--step", help="从指定步骤续跑")
    ap.add_argument("--status", action="store_true", help="查看实例状态")
    ap.add_argument("--reset", action="store_true", help="重置实例")
    args = ap.parse_args()

    eng = FlowEngine(args.flow)
    if args.reset:
        eng.instance_path.unlink(missing_ok=True)
        # 生命周期耦合（2026-10-10）：实例删除可能使引擎全停 → 心跳自停
        cron_coupling.reconcile_silent()
        print("已重置")
        return 0
    if args.status:
        print(eng.status())
        return 0
    print(eng.run(args.step))
    return 0


if __name__ == "__main__":
    sys.exit(main())

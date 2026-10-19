"""executor_dispatch.py — 引擎派发执行器（v0.6, 2026-08-16 缺陷修复）。

引擎 dispatch 步骤的执行器：把"派发干活"转成真实 kanban 建卡 + dispatcher spawn。

机制（M2 已验证 dry-run）：
  1. kanban create --assignee <agent> --idempotency-key <flow:step>
                  --body "<从模板 step 生成执行指令>" <标题>
  2. 引擎推进 → kanban dispatch（cron/daemon 驱动）自动 spawn ready 卡
  3. agent 执行 → 产物落盘 → 引擎 wait/verify 检测

2026-08-16 缺陷修复（t_62463005）：
  - 幂等键去 iteration：`engine:{flow}:{step}`（原 `...:{step}:{iteration}` 导致
    同 step 每次迭代建新卡，flow completed 后旧卡批量 spawn 成幽灵任务）
  - 复用决策：dispatch 前查同 flow 同 step 已有卡（兼容旧键）
      * 无卡 → 新建
      * 卡 done（verify FAIL 打回重做）→ 重开为 ready（复用同一张卡）
      * 卡 ready/running/todo/blocked（在途）→ 幂等跳过
  - iteration 不再参与幂等键，只记录在 flow instance json + 卡 title 标注

安全：
  - --dry-run：只打印将执行的建卡命令，不实际建卡（预研/测试用）
  - --idempotency-key：flow+step 唯一，防重复派发
  - 幂等键冲突（卡已存在）→ 跳过不重复建

用法（引擎调用）：
  python executor_dispatch.py --flow <flow> --step <step_id> [--dry-run] [--assignee <agent>]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import os
import sys
import time
from pathlib import Path

import yaml

import kanban_client

ENGINE_DIR = Path(__file__).parent
FLOWS_DIR = ENGINE_DIR / "flows"

# ---------------------------------------------------------------------------
# 派工表数据化（2026-08-29，architecture-multi-agent 002 总纲改进项）：
# 真相源 = engine/config/dispatch_profiles.yaml；下面代码内置表仅作 fallback
# （YAML 缺失/损坏时生效并打印告警，防派发瘫痪——失败显式优于静默错派）。
# ---------------------------------------------------------------------------
_CONFIG_PATH = ENGINE_DIR / "config" / "dispatch_profiles.yaml"
_yaml_cfg: dict | None = None
if _CONFIG_PATH.exists():
    try:
        import yaml as _yaml
        _raw = _yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        _yaml_cfg = {
            "default_assignee": dict(_raw.get("default_assignee") or {}),
            "step_assignee": dict(_raw.get("step_assignee") or {}),
            "step_goal": dict(_raw.get("step_goal") or {}),
            "step_skills": dict(_raw.get("step_skills") or {}),
        }
    except Exception as _e:  # 损坏不中断，走 fallback + 醒目告警
        print(f"[executor] [WARN] dispatch_profiles.yaml 加载失败，启用代码内置 fallback: {_e}",
              flush=True)
else:
    print(f"[executor] [WARN] dispatch_profiles.yaml 不存在（{_CONFIG_PATH}），启用代码内置 fallback",
          flush=True)

# flow 模板 → 默认执行 agent（可在 step.executor_assignee 覆盖）
DEFAULT_ASSIGNEE = {
    "032-mem0-hmem": "default",        # 032 实施主体
    "flow-engine-build": "default",    # 引擎自举实施主体
}

# 步骤 id → 目标 agent（step 未指定 executor_assignee 时查此表）
STEP_ASSIGNEE = {
    # 内置 fallback：内置流程模板（engine/flows/template-*.yaml）各步骤的默认执行者。
    # 真相源 = engine/config/dispatch_profiles.yaml（存在则覆盖本表）。
    # assignee 必须是你环境中可 spawn 的真实 agent profile 名，按需修改。
    "bf_charter": "default",
    "bf_repro": "default",
    "bf_fix": "default",
    "bf_regression": "default",
    "bf_verify": "default",      # 建议改为你的评审 profile
    "bf_verify2": "default",     # 建议改为你的评审 profile
    "charter": "default",
    "implement": "default",
    "verify": "default",         # 建议改为你的评审 profile
    "plan": "default",
    "rs_charter": "default",
    "rs_survey": "default",
    "rs_experiment": "default",
    "rs_conclusion": "default",
    "rs_verify1": "default",
    "rs_verify2": "default",
    "ds_charter": "default",
    "ds_empathize": "default",
    "ds_needs": "default",
    "ds_concept": "default",
    "ds_review": "default",
}

# 步骤 → 执行指令模板（驱使 agent 干活的目标）
STEP_GOAL = {
    # 内置 fallback：通用化步骤目标。项目个性化目标写在 dispatch_profiles.yaml 的
    # step_goal 段（键 = 项目前缀化的 step id），存在则覆盖本表。
    "bf_charter": "生成 charter 四件套（charter/STATE/ROLLBACK/INCIDENT-RESPONSE），锚定本项目 spec 的目标/约束/成功标准，落盘本项目 exchange/。",
    "bf_repro": "TDD 先行：为本项目每个待修缺陷编写失败复现用例（先红），输出复现报告落盘 exchange/artifacts/。",
    "bf_fix": "逐缺陷最小修复，禁顺手重构；每修一项把对应复现用例转绿。",
    "bf_regression": "全量回归测试必须全绿，输出回归报告落盘 exchange/artifacts/。",
    "bf_verify": "验收：核对红→绿证据与报告产物齐全达标；通过置 accepted，不达标置 rejected 并列问题清单。",
    "bf_verify2": "二次验收：全量回归 + 边界用例复核；通过置 accepted，不达标置 rejected。",
    "charter": "生成立项 charter 四件套，锚定 spec，落盘 exchange/。",
    "plan": "按 spec 制定实施计划（任务拆分/顺序/验收标准），落盘 exchange/artifacts/。",
    "implement": "按计划实施，产物落盘项目目录，完成后写实施报告（status=done）。",
    "verify": "验收：对照成功标准逐条实测；通过置 accepted，不达标置 rejected 并列问题清单。",
    "rs_charter": "生成研究项目 charter（问题定义/方法/验收标准），落盘 exchange/。",
    "rs_survey": "文献/资料调研，输出结构化调研报告（来源可溯），落盘 exchange/artifacts/。",
    "rs_experiment": "按调研结论设计并执行实验/验证，记录数据与结论，落盘 exchange/artifacts/。",
    "rs_conclusion": "汇总调研+实验产出结题报告，落盘 exchange/artifacts/。",
    "rs_verify1": "调研报告验收：来源可靠、结构完整、结论有据。",
    "rs_verify2": "结题报告验收：对照 charter 成功标准逐条核对。",
    "ds_charter": "生成设计项目 charter（用户/问题/范围），落盘 exchange/。",
    "ds_empathize": "用户共情研究：访谈/场景/痛点归纳，落盘 exchange/artifacts/。",
    "ds_needs": "需求定义：从共情研究提炼需求清单与优先级，落盘 exchange/artifacts/。",
    "ds_concept": "概念设计：方案草案+关键交互定义，落盘 exchange/artifacts/。",
    "ds_review": "设计评审：方案完整性/可行性/与需求对齐度；通过置 accepted。",
}


def _derive_delivery(flow: str, step: str) -> str | None:
    """从 flow yaml 派生本步骤的交付产物路径（单一事实源，2026-08-30 wait 屏障断链修复）。

    规则：dispatch 步骤的交付路径不应在 dispatch_profiles.yaml 与 flow yaml
    两处手写（两处不一致 = wait 屏障永等且无告警，t_b4ebc051 实证）。
    真相源 = flow yaml：找本 step 的 next 指向的下游步骤（wait/verify 优先，
    取其 artifact/artifacts 字段），作为交付路径派生给卡 body。

    返回 None 表示 yaml 无法派生（flow 不存在 / 下游无 artifact）——调用方
    保持 goal 原样（向后兼容，不报错）。
    """
    fp = FLOWS_DIR / f"{flow}.yaml"
    if not fp.exists():
        return None
    try:
        tpl = yaml.safe_load(fp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — yaml 损坏时降级为不派生，不阻断建卡
        return None
    steps = {s.get("id"): s for s in tpl.get("steps", []) if s.get("id")}
    cur = steps.get(step)
    if not cur:
        return None
    # 优先级：next 链下游第一个带 artifact 的步骤（wait/verify 屏障语义）
    nxt = cur.get("next")
    down = steps.get(nxt) if nxt else None
    # dispatch 自身可能直接带 artifact（罕见）→ 优先用
    for cand in (cur, down):
        if not cand:
            continue
        art = cand.get("artifact") or (cand.get("artifacts") or [None])[0]
        if art:
            return str(art)
    return None


def build_card_body(flow: str, step: str, iteration: int) -> str:
    """组装卡 body（D6/S6 抽取，2026-08-31 enginefix；原内联在 _build_cmd）。

    巡检类 step（step id 含 patrol/巡检）：首行必须是 exwrite 交付指令
    （jingwei 3 次把报告写进 game/ 实证），后再接 goal 正文。
    """
    if _yaml_cfg is not None:
        dflt = _yaml_cfg["default_assignee"].get(flow) or _yaml_cfg["default_assignee"].get("flow-engine-build", "default")
        assignee = _yaml_cfg["step_assignee"].get(step) or dflt
        goal = _yaml_cfg["step_goal"].get(step, f"{flow}/{step}: 按引擎派发执行")
        skills = _yaml_cfg["step_skills"].get(step) or []
    else:
        assignee = STEP_ASSIGNEE.get(step, DEFAULT_ASSIGNEE.get(flow, "default"))
        goal = STEP_GOAL.get(step, f"{flow}/{step}: 按引擎派发执行")
        skills = []
    # 交付屏道脚本与项目根可按环境覆盖（开源默认：exwrite.py 在 PATH，项目根 = 当前目录）
    _exwrite = os.environ.get("FLOW_ENGINE_EXWRITE", "exwrite.py")
    _projects_root = os.environ.get("FLOW_ENGINE_PROJECTS_ROOT", ".")
    # D6/S6（2026-08-31 enginefix）：巡检类卡 exwrite 交付指令放 body 首行
    if "patrol" in step or "巡检" in goal:
        goal = ("[交付指令（必须遵守）] 交付走 exwrite 取号: "
                f"python {_exwrite} "
                f"--dir {_projects_root}/{flow}/exchange ——报告等产物一律写到 exchange/ 屏道路径，"
                "禁止写进业务目录\n" + goal)
    # F4（enginefix2 000-brief #4）：verify/验收类卡与 patrol 类同款——首行必须
    # 是 exwrite 交付指令。验收 worker 不走 exwrite → 产物 glob 失配 → 引擎误建卡
    # （simflight 实证）。守卫式：step id 含 verify 或 goal 含 验收 即命中。
    if ("verify" in step or "验收" in goal) and not ("exwrite" in goal and "交付" in goal):
        goal = ("[交付指令（必须遵守）] 交付走 exwrite 取号: "
                f"python {_exwrite} "
                f"--dir {_projects_root}/{flow}/exchange ——验收产物一律写到 exchange/ 屏道路径，"
                "禁止写进业务目录；验收完成后产物 frontmatter status 置 accepted/rejected\n" + goal)
    # F8a（enginefix2 000-brief #8）：实施类卡职责边界指令——防实施 agent 越界
    # 代写验收产物（default 代写验收报告实证）。verify/patrol 类卡本身职责即验收/
    # 巡检，不受此边界约束。
    if "verify" not in step and "patrol" not in step:
        if "禁止代写验收产物" not in goal:
            goal = (goal + "（职责边界：禁止代写验收产物——验收报告等 verify 产物必须由验收人"
                    "（verify 步 assignee）经 exwrite 落盘，author 字段须为验收人本人，"
                    "实施者代写的验收产物将被引擎 author 校验拒绝）")
    # assignee 归一化守卫（2026-08-30 user 拍板）：'hermes' 是保留名 spawn 会被拒，
    # 语义上 hermes 本体 = default profile。任何路径（YAML 表/代码 fallback/模板覆写）
    # 落到 'hermes' 一律归一化为 'default'，防保留名 spawn_failed 复发（t_58a5d606 实证）。
    # ⚠️ 语义边界（hermes 双语义）：本守卫只作用于 kanban assignee 槽位——槽位值只可能是
    # agent profile，此处 hermes 必指「default agent 本体」，不可能是系统语义；函数内
    # cmd[0] 的 "hermes"（CLI 程序名）等系统语义引用不经过本变量，不受影响。
    if assignee == "hermes":
        assignee = "default"
    # skill 注入两层制（2026-08-29 user 拍板）：
    #   纪律层 = step_skills 静态表（永远必带，无判断）；领域层 = AGENTS.md 项目 Skill 池（执行 agent 按任务匹配挑选）
    pool_hint = ("[执行前第一步：读项目 AGENTS.md 的「本项目 Skill 池」，判断本步骤环节，"
                 "点名列出将加载的 skill + 一句话理由，skill_view 后再动手；池中无适用则明说，禁止硬凑] ")
    # 验证纪律（2026-09-05 user 拍板，codex 实测 t_括号函数：Codex 写完不主动跑测试，
    # 需显式清单逼才验证。固化"人的追问"进每张卡，治纪律类 skill 被动触发）
    pool_hint += ("[完成前必须验证：编码任务实际运行测试/构建并贴真实输出，"
                  "未运行的验证不算完成] ")
    if skills:
        goal = f"[加载 skill: {', '.join(skills)}（先 skill_view 后执行）] " + pool_hint + goal
    else:
        goal = pool_hint + goal
    # 单一事实源（2026-08-30 wait 屏障断链修复 t_46516f87）：交付路径从 flow yaml
    # 派生并显式注入卡 body——worker 走 exwrite 时按屏道路径对齐，禁止两处手写漂移。
    # goal 内已含该路径则不重复注入（向后兼容旧 goal 文案）。
    delivery = _derive_delivery(flow, step)
    if delivery and delivery not in goal:
        goal = f"{goal}（交付路径以引擎屏道为准: {delivery}）"
    # verify 卡 status 语义指令（2026-08-31 冒烟实证根治）：verify 步骤的卡若不写
    # 「实测通过后把产物 frontmatter status 从 done 改为 accepted；不通过改 rejected」，
    # worker 会验证通过却不动 status → 引擎机械等待 verify 永不过（本次 t_84bedc2e 实证）。
    # 守卫式注入：step 名含 verify 且 goal 未含该指令时追加，兜住所有 flow 的 verify 卡。
    if "verify" in step and "status 从 done 改为 accepted" not in goal:
        goal = (goal + "（验收纪律：实测通过后把交付产物 frontmatter status 从 done 改为 accepted；"
                "不通过改 rejected 并在卡内写问题清单——引擎只认产物 status 字段）")
    return goal


def _build_cmd(flow: str, step: str, iteration: int) -> list[str]:
    """构造 kanban create 命令（不执行）。body 组装走 build_card_body（D6 抽取）。"""
    assignee = _resolve_assignee(flow, step)
    title = f"[引擎:{flow}] {step} (iter {iteration})"
    cmd = [
        "hermes", "kanban", "create", title,
        "--assignee", assignee,
        "--idempotency-key", f"engine:{flow}:{step}",
        "--body", build_card_body(flow, step, iteration),
        "--project", "flow-engine" if flow == "flow-engine-build" else "multi-agents",
        "--created-by", "flow-engine",
    ]
    return cmd


class QuotaExhaustedError(RuntimeError):
    """assignee provider 额度耗尽（429/insufficient_quota）→ 拒派（D3/S3）。"""


# ─── F6（enginefix2 000-brief #6）：glm worker 思考空转检测 ───
def detect_thought_loop(log: str) -> bool:
    """日志连续纯 thought 无任何工具调用 → 判空转（True）。

    特征来源：simflight-v2 013 收官报告 §四.4——glm worker 输出全部为
    `<|channel>thought` 行且无 tool_use/tool_result，进程看似在跑实则
    空转直到挂死。健康日志（thought 与 tool_use 交替）返回 False。
    """
    lines = [ln.strip() for ln in (log or "").splitlines() if ln.strip()]
    if not lines:
        return False
    thought_re = re.compile(r"<\|channel>\s*thought|thought\b", re.IGNORECASE)
    tool_re = re.compile(r"tool_use|tool_result|tool_call")
    has_thought = any(thought_re.search(ln) for ln in lines)
    has_tool = any(tool_re.search(ln) for ln in lines)
    # 空转 = 有 thought 输出但全程零工具调用（含零输出行也判 False 防误伤空日志）
    return has_thought and not has_tool


# ─── F7（enginefix2 000-brief #7）：打回前下游产物短路检查 ───
def downstream_accepted(flow: str, step: str) -> tuple[bool, str]:
    """查 flow yaml 中 step 的下游（next 指向的）verify 步产物 frontmatter status。

    下游 verify 产物 status ∈ accepted/closed → (True, 原因)（本 step 不应再
    重开重做——收官后全链回退空跑实证，000-brief #7）。查不到/未达标 → (False, ...)。
    """
    flow_file = FLOWS_DIR / f"{flow}.yaml"
    if not flow_file.exists():
        return (False, f"flow yaml 不存在: {flow_file}")
    try:
        cfg = yaml.safe_load(flow_file.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001 — yaml 损坏不短路（失败显式，走原路径）
        return (False, f"flow yaml 解析失败: {e}")
    steps = {s.get("id"): s for s in (cfg.get("steps") or []) if isinstance(s, dict)}
    cur = steps.get(step)
    if not cur:
        return (False, f"step 不存在: {step}")
    # 下游 = next 指向的 verify 步（可有多 next 情形按首个命中即可，本引擎 flow 均单链）
    nxt = cur.get("next")
    if isinstance(nxt, list):
        nxt = nxt[0] if nxt else None
    down = steps.get(nxt) if nxt else None
    if not down or down.get("type") != "verify":
        return (False, f"下游非 verify 步: {nxt}")
    artifact = down.get("artifact")
    if not artifact:
        return (False, f"下游 {nxt} 无 artifact 字段")
    ap = Path(artifact)
    if not ap.exists():
        return (False, f"下游产物不存在: {artifact}")
    try:
        m = re.search(r"^status:\s*(\S+)", ap.read_text(encoding="utf-8", errors="ignore"), re.M)
    except Exception as e:  # noqa: BLE001
        return (False, f"产物读取失败: {e}")
    st = m.group(1) if m else "?"
    if st in ("accepted", "closed"):
        return (True, f"下游 {nxt} 产物已 {st}（引擎收官态），无需回退重做")
    return (False, f"下游 {nxt} 产物 status={st}，未达 accepted/closed")


def _resolve_assignee(flow: str, step: str) -> str:
    """按 dispatch_profiles/代码表解析 assignee（与 _build_cmd 同源，D3 用）。"""
    if _yaml_cfg is not None:
        dflt = _yaml_cfg["default_assignee"].get(flow) or _yaml_cfg["default_assignee"].get("flow-engine-build", "default")
        assignee = _yaml_cfg["step_assignee"].get(step) or dflt
    else:
        assignee = STEP_ASSIGNEE.get(step, DEFAULT_ASSIGNEE.get(flow, "default"))
    return "default" if assignee == "hermes" else assignee


def _resolve_provider(assignee: str) -> str:
    """读 assignee profile config 的 provider（D3；读不到返回空串=跳过预检）。"""
    import os as _os
    root = _os.environ.get(
        "HERMES_HOME",
        str(Path.home() / "AppData/Local/hermes"))
    base = root if assignee == "default" else f"{root}/profiles/{assignee}"
    cfgp = Path(base) / "config.yaml"
    try:
        cfg = yaml.safe_load(cfgp.read_text(encoding="utf-8")) or {}
        return str(cfg.get("provider") or cfg.get("model_provider") or "").strip()
    except Exception:  # noqa: BLE001 — profile 不可读 → 跳过预检（不阻断派发）
        return ""


# ── D3 三级降级预检（玄武 010 C1 拍板）：本地失败标记(15min TTL) → 额度缓存
# (5min TTL) → API 轻量探针；探针自身 429 直接标记失败不重试 ──
_QUOTA_CACHE_DIR = Path(os.environ.get(
    "FLOW_ENGINE_QUOTA_CACHE",
    str(Path(__file__).resolve().parent / ".quota_cache")))


def _quota_cache_path(assignee: str) -> Path:
    return _QUOTA_CACHE_DIR / f"{assignee}.json"


def _quota_cache_read(assignee: str, ttl_s: int) -> tuple[bool, str] | None:
    """读本地额度缓存；命中返回 (ok, detail)，过期/缺失返回 None。"""
    try:
        data = json.loads(_quota_cache_path(assignee).read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > ttl_s:
            return None
        return bool(data.get("ok")), str(data.get("detail", ""))
    except Exception:  # noqa: BLE001
        return None


def _quota_cache_write(assignee: str, ok: bool, detail: str) -> None:
    try:
        _QUOTA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _quota_cache_path(assignee).write_text(
            json.dumps({"ok": ok, "detail": detail, "ts": time.time()}),
            encoding="utf-8")
    except Exception:  # noqa: BLE001 — 缓存写失败不阻断
        pass


def check_provider_quota(assignee: str, provider: str) -> tuple[bool, str]:
    """spawn 前按 assignee 预检 provider 额度（D3/S3，三级降级）。

    返回 (ok, detail)。ok=False → main_with_args 拒派（QuotaExhaustedError）。
    provider 为空（profile 不可读）→ 放行（预检不可用优于瘫痪派发）。
    """
    if not provider:
        return (True, "no provider resolved, skip precheck")
    # 一级：失败标记（15min TTL）——最近失败直接拒，不重试不探针
    hit = _quota_cache_read(assignee, ttl_s=900)
    if hit and not hit[0]:
        print(f"[executor] [quota] L1 失败标记命中: {assignee}/{provider} {hit[1]}")
        return hit
    # 二级：额度缓存（5min TTL）——最近成功直接放行
    if hit and hit[0]:
        print(f"[executor] [quota] L2 缓存命中(ok): {assignee}/{provider}")
        return hit
    # 三级：API 轻量探针（models 列表，零 token 消耗）
    try:
        import os as _os
        import urllib.request
        root = _os.environ.get(
            "HERMES_HOME", str(Path.home() / "AppData/Local/hermes"))
        base = root if assignee == "default" else f"{root}/profiles/{assignee}"
        cfg = yaml.safe_load(
            (Path(base) / "config.yaml").read_text(encoding="utf-8")) or {}
        base_url = str(cfg.get("base_url") or "").strip()
        key = ""
        try:
            env = Path(base) / ".env"
            for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith(("OPENAI_API_KEY", "ZAI_API_KEY", "API_KEY")) and "=" in line:
                    key = line.split("=", 1)[1].strip().strip('"')
                    break
        except Exception:  # noqa: BLE001
            pass
        if not base_url:
            return (True, "no base_url, skip probe")
        req = urllib.request.Request(
            base_url.rstrip("/") + "/models",
            headers={"Authorization": f"Bearer {key}"} if key else {})
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status // 100 == 2:
                    _quota_cache_write(assignee, True, "probe ok")
                    return (True, "probe ok")
                _quota_cache_write(assignee, False, f"probe http {r.status}")
                return (False, f"probe http {r.status}")
        except urllib.error.HTTPError as he:
            # 探针自身 429 → 直接标记失败（不重试，玄武 C1）
            detail = f"429 insufficient_quota" if he.code == 429 else f"probe http {he.code}"
            _quota_cache_write(assignee, False, detail)
            return (False, detail)
        except Exception as e:  # noqa: BLE001 — 探针网络失败 → 放行（不阻断）
            return (True, f"probe error({type(e).__name__}), allow")
    except Exception as e:  # noqa: BLE001
        return (True, f"precheck error({type(e).__name__}), allow")


def main_with_args(argv: list[str]) -> int:
    """main() 可注入入口（D3 测试约定 docstring）：argv 形如 ['--flow', X, '--step', Y]。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--flow", required=True)
    ap.add_argument("--step", required=True)
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不实际建卡")
    args = ap.parse_args(argv)

    # D3/S3：建卡前按 assignee 预检 provider 额度（athena zai 429 空跑 20min×2 实证）
    assignee = _resolve_assignee(args.flow, args.step)
    provider = _resolve_provider(assignee)
    ok, detail = check_provider_quota(assignee, provider)
    if not ok:
        print(f"[executor] [WARN] 拒派 {args.flow}/{args.step}: assignee={assignee} "
              f"provider={provider} 额度预检失败 ({detail})", flush=True)
        return 2

    # 读实例状态拿 iteration（仅用于 title 标注，不再参与幂等键）
    inst = ENGINE_DIR / "instances" / f"{args.flow}.json"
    iteration = 1
    if inst.exists():
        try:
            st = json.loads(inst.read_text(encoding="utf-8"))
            iteration = st.get("iteration", 1)
        except Exception:
            pass

    # 复用决策（2026-08-16 修复）：同 flow 同 step 已有卡优先复用。
    # dry-run 模式下跳过复用分支，始终展示建卡命令（预演工具的可观测性）。
    existing = None if args.dry_run else kanban_client.resolve_step_card(args.flow, args.step)
    if existing:
        if existing["status"] == "done":
            # F7（enginefix2 000-brief #7）：打回前查下游 verify 产物 status——
            # 已 accepted/closed = 引擎收官态，回退重开是空跑，短路返回 0。
            short, why = downstream_accepted(args.flow, args.step)
            if short:
                print(f"[executor] 短路: 不重开 {args.flow}/{args.step} —— {why}", flush=True)
                return 0
            # F5（enginefix2 000-brief #5）：打回重做注入增量模式标记，防 worker
            # 全量重跑跑偏挂死。fix_hint 带 reason 供 worker 判断增量范围。
            title = f"[引擎:{args.flow}] {args.step} (iter {iteration})"
            ok2 = kanban_client.reopen_done_card(
                existing["id"], new_title=title,
                reason=f"verify FAIL 打回重做 iter={iteration}",
                mode="incremental",
                fix_hint="本轮为打回重做：只做增量修复，按最新 rejected 意见清单改，勿全量重跑",
            )
            if ok2:
                print(f"[executor] 复用并重开卡 {existing['id']} (done→ready, iter={iteration})")
            else:
                print(f"[executor] 卡 {existing['id']} 重开失败（状态非 done?）status={existing['status']}")
                return 1
            return 0
        # 在途卡（ready/running/todo/blocked）→ 幂等跳过，不重复建卡
        print(f"[executor] 卡已存在（幂等跳过）: {existing['id']} status={existing['status']}")
        return 0

    # 无卡 → 新建
    cmd = _build_cmd(args.flow, args.step, iteration)

    if args.dry_run:
        print("[executor] (dry-run) 将执行:")
        print("  " + " ".join(cmd))
        return 0

    # 真实执行：kanban create
    print(f"[executor] 建卡 {args.flow}/{args.step} iter={iteration} →", flush=True)
    # 2026-09-01 enginefix2 首航补丁：doker 容器是 Linux，无法执行 Windows hermes.exe
    # （WSL interop 不可用）。改为输出建卡指令到 stdout（DOKER_CARD_JSON marker），
    # 由主机侧 engine.py 解析后代为执行 kanban create —— 写操作永远落主机。
    doker_mode = os.path.isdir("/host-hermes") and os.path.isdir("/engine")
    cmd_resolved = list(cmd)
    if doker_mode:
        card_title = f"[引擎:{args.flow}] {args.step} (iter {iteration})"
        payload = {
            "flow": args.flow, "step": args.step, "iteration": iteration,
            "assignee": assignee,
            "title": card_title,
            "body": build_card_body(args.flow, args.step, iteration),
        }
        print("DOKER_CARD_JSON:" + json.dumps(payload, ensure_ascii=False))
        return 0
    if cmd_resolved and cmd_resolved[0] == "hermes":
        host_cli = "/host-hermes/hermes-agent/venv/Scripts/hermes.exe"
        if os.path.exists(host_cli):
            cmd_resolved[0] = host_cli
    r = subprocess.run(cmd_resolved, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        # 幂等键冲突（卡已存在）才算幂等跳过；其他错误（参数错/用法错）如实报出
        err = (r.stderr or "") + (r.stdout or "")
        if "already exists" in err.lower() or "duplicate" in err.lower() or "idempotency key" in err.lower():
            print(f"[executor] 卡已存在（幂等），跳过: {err.strip()[:150]}")
            return 0
        print(f"[executor] 建卡失败 rc={r.returncode}: {err[-300:]}")
        return r.returncode
    print(f"[executor] 建卡成功: {(r.stdout or r.stderr or '').strip()[:200]}")
    print(f"[executor] 下一步: kanban dispatch 自动 spawn（cron/daemon 驱动）")
    return 0


def main() -> int:
    return main_with_args(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())

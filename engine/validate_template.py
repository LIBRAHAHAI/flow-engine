"""validate_template.py — 流程模板校验器（M1.5, 2026-08-16；v1.1 支持 roadmap 模板）。

写错模板即报（pre_write_check 同款理念）：引擎跑之前先校验模板结构，
错误模板在写文件阶段就被拦截，而不是跑到一半才发现。

校验项（flow 模板）：
  1. YAML 可解析 + 顶层有 name/steps
  2. steps 非空 + 每步有 id/type
  3. 步骤 id 唯一
  4. type ∈ {dispatch, verify, decision, wait, done}
  5. 有 next 的步骤，next 指向存在的步骤 id（或 done/None 终态）
  6. on_reject/on_fail/on_timeout 指向存在的步骤 id
  7. decision 有 question；options 各含 key/label，key 唯一
  8. verify/wait 至少一个 artifact 或 artifacts
  9. max_iterations（若有）> 0

校验项（roadmap 模板，v1.1 新增：顶层 stages 而非 steps）：
  a. 顶层有 name + stages（非空列表）
  b. 每阶段有 id/name/action/desc
  c. 阶段 id 唯一
  d. 有 next 的阶段，next 指向存在的阶段 id（或 done 终态）

用法：
  python validate_template.py flows/032-mem0-hmem.yaml   # 单文件
  python validate_template.py --all                      # 校验 flows/ 全部
exit 0 = 通过；exit 1 = 有错（打印每个错误）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

FLOWS_DIR = Path(__file__).resolve().parent / "flows"
VALID_TYPES = {"dispatch", "dispatch_group", "verify", "decision", "wait", "done", "discuss"}
TERMINAL_TYPES = {"done"}


def validate_roadmap(fp: Path, data: dict) -> list[str]:
    """校验 roadmap 模板（gen_project.py 消费：stages → ROADMAP.md + flow 步骤生成）。

    与 flow 模板不同：顶层是 stages（阶段）而非 steps（步骤），
    每阶段映射为 flow 模板的 [dispatch → wait → verify] 三元组。
    """
    errors: list[str] = []
    if not data.get("name"):
        errors.append(f"{fp.name}: 缺 name")
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        return errors + [f"{fp.name}: stages 必须是非空列表"]

    ids = []
    for i, s in enumerate(stages):
        tag = f"{fp.name} stages[{i}]"
        if not isinstance(s, dict):
            errors.append(f"{tag}: 阶段必须是 mapping")
            continue
        sid = s.get("id")
        if not sid:
            errors.append(f"{tag}: 缺 id")
        for k in ("name", "action", "desc"):
            if not s.get(k):
                errors.append(f"{tag}: 缺 {k}")
        if sid:
            if sid in ids:
                errors.append(f"{tag}: 重复 id={sid!r}")
            ids.append(sid)

    # next 指向存在性
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            continue
        nxt = s.get("next")
        if nxt and nxt not in ids:
            errors.append(f"{fp.name} stages[{i}]: next={nxt!r} 指向不存在的阶段")
    return errors


def validate_file(fp: Path) -> list[str]:
    """校验单个模板文件，返回错误列表（空 = 通过）。"""
    errors: list[str] = []
    try:
        data = yaml.safe_load(fp.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"{fp.name}: YAML 解析失败: {e}"]

    if not isinstance(data, dict):
        return [f"{fp.name}: 顶层必须是 mapping（含 name/steps 或 name/stages）"]

    # roadmap 模板（顶层 stages）走独立校验
    if "stages" in data and "steps" not in data:
        return validate_roadmap(fp, data)

    if not data.get("name"):
        errors.append(f"{fp.name}: 缺 name")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return errors + [f"{fp.name}: steps 必须是非空列表"]

    ids = []
    for i, s in enumerate(steps):
        tag = f"{fp.name} steps[{i}]"
        if not isinstance(s, dict):
            errors.append(f"{tag}: 步骤必须是 mapping")
            continue
        sid = s.get("id")
        stype = s.get("type")
        if not sid:
            errors.append(f"{tag}: 缺 id")
        if not stype:
            errors.append(f"{tag}: 缺 type")
        elif stype not in VALID_TYPES:
            errors.append(f"{tag}: 非法 type={stype!r}（合法: {sorted(VALID_TYPES)}）")
        if sid:
            if sid in ids:
                errors.append(f"{tag}: 重复 id={sid!r}")
            ids.append(sid)
        # next 指向
        nxt = s.get("next")
        if nxt and stype not in TERMINAL_TYPES:
            pass  # 存在性检查放 ids 收集后统一做
        # 分支指向
        for bk in ("on_reject", "on_fail", "on_timeout"):
            v = s.get(bk)
            if v is not None and not isinstance(v, str):
                errors.append(f"{tag}: {bk} 必须是步骤 id 字符串")
        # 类型专属校验
        if stype == "decision":
            if not s.get("question"):
                errors.append(f"{tag}: decision 缺 question")
            opts = s.get("options") or []
            opt_keys = []
            for o in opts:
                if not o.get("key") or not o.get("label"):
                    errors.append(f"{tag}: options 项缺 key/label")
                elif o["key"] in opt_keys:
                    errors.append(f"{tag}: options key 重复 {o['key']!r}")
                opt_keys.append(o.get("key"))
                if o.get("next") is not None and not isinstance(o["next"], str):
                    errors.append(f"{tag}: options[{o['key']}].next 必须是步骤 id")
        if stype == "wait":
            # t_a1f33cfc：wait 锚点 = artifact 或 card_done 其一（artifact 优先）
            if not s.get("artifact") and not s.get("artifacts") and not s.get("card_done"):
                errors.append(f"{tag}: wait 至少需要 artifact/artifacts 或 card_done 之一")
        elif stype == "verify":
            if not s.get("artifact") and not s.get("artifacts"):
                errors.append(f"{tag}: {stype} 至少需要一个 artifact 或 artifacts")

    # next/分支指向存在性（收集完 ids 后）
    for i, s in enumerate(steps):
        tag = f"{fp.name} steps[{i}]"
        if not isinstance(s, dict):
            continue
        for ref in ("next", "on_reject", "on_fail", "on_timeout"):
            v = s.get(ref)
            if v and v not in ids:
                errors.append(f"{tag}: {ref}={v!r} 指向不存在的步骤")
        for o in (s.get("options") or []):
            n = o.get("next")
            if n and n not in ids:
                errors.append(f"{tag}: options[{o.get('key')}].next={n!r} 指向不存在的步骤")

    mi = data.get("max_iterations")
    if mi is not None and (not isinstance(mi, int) or mi <= 0):
        errors.append(f"{fp.name}: max_iterations 必须是正整数")

    # D5/S5（2026-08-31 enginefix）：相邻两个 dispatch 步骤之间必须有屏障
    # （wait/verify/decision），否则 R5 屏障被绕过（impl→patrol 背靠背建卡实证）。
    # 2026-09-10：dispatch_group（并行组）语义——组内成员卡并行，组本身视作
    # dispatch（组前需屏障），组后 next 必须直接指向屏障（wait/verify/decision），
    # 组屏障 wait 须用 artifacts 列表覆盖全部成员产物（P5 素材五路并行根治）。
    barrier_types = {"wait", "verify", "decision"}
    prev_dispatch: str | None = None
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        sid, stype = s.get("id"), s.get("type")
        if stype == "dispatch_group":
            members = s.get("members") or []
            if not members:
                errors.append(f"{fp.name} steps[{i}]: dispatch_group {sid} 缺 members")
            else:
                for j, m in enumerate(members):
                    if not isinstance(m, dict) or not m.get("id"):
                        errors.append(f"{fp.name} steps[{i}]: dispatch_group {sid} members[{j}] 缺 id")
            # 组成员 step id 必须真实存在（成员卡由 executor 以 member id 建卡，
            # 派工表 step_assignee 按 member id 查键）——但成员不是模板步骤，
            # 允许不在 steps 里；这里只校验组后屏障。
            nxt = s.get("next")
            nxt_step = next((x for x in steps if isinstance(x, dict) and x.get("id") == nxt), None)
            if nxt_step is None or nxt_step.get("type") not in barrier_types:
                errors.append(
                    f"{fp.name} steps[{i}]: dispatch_group {sid} next={nxt!r} 必须直接指向 "
                    f"wait/verify/decision 屏障步骤（并行组屏障，R5）")
            if prev_dispatch is not None:
                errors.append(
                    f"{fp.name} steps[{i}]: 相邻 dispatch 屏障缺失——"
                    f"{prev_dispatch} → {sid} 两个 dispatch/group 步骤之间必须隔 "
                    f"wait/verify/decision 屏障步骤（R5）")
            prev_dispatch = sid
        elif stype == "dispatch":
            if prev_dispatch is not None:
                errors.append(
                    f"{fp.name} steps[{i}]: 相邻 dispatch 屏障缺失——"
                    f"{prev_dispatch} → {sid} 两个 dispatch 步骤之间必须隔 "
                    f"wait/verify/decision 屏障步骤（R5）")
            prev_dispatch = sid
        elif stype in barrier_types:
            prev_dispatch = None

    return errors


def main() -> int:
    args = sys.argv[1:]
    if "--all" in args:
        files = sorted(FLOWS_DIR.glob("*.yaml"))
    else:
        files = [Path(a) for a in args if a != "--all"]
    if not files:
        print("用法: validate_template.py <flow.yaml> ... | --all")
        return 1

    all_errors: list[str] = []
    for fp in files:
        if not fp.exists():
            all_errors.append(f"{fp}: 文件不存在")
            continue
        errs = validate_file(fp)
        if errs:
            all_errors.extend(errs)
        else:
            print(f"✅ {fp.name} 模板校验通过")

    if all_errors:
        print("\n❌ 模板校验失败:")
        for e in all_errors:
            print(f"  - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

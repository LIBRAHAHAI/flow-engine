"""gen_project.py — "开引擎 <项目名>" 一键生成器（M5, 2026-08-16）。

用户说"开引擎 <项目名>" → 本脚本自动生成项目全套：
  1. 项目目录 + exchange 结构
  2. roadmap.md（按 template-roadmap.yaml 阶段生成）
  3. flow 模板（flows/<项目>.yaml，按 roadmap stages 生成步骤）
  4. 注册流程实例（instances/<项目>.json）

用法：
  python gen_project.py <项目名> [--project-dir <项目根>/<项目名>]
  python gen_project.py <项目名> --force   # 显式覆盖已存在项目（危险）

生成后：引擎从 charter 步骤接入 → 自动跑立项→评审→拍板→实施→验收→收官。

2026-08-20 缺陷修复（flow-engine-frontend-v2 事故）：
  原实现对已存在项目盲目覆盖——重跑同名项目会把定制流程模板 +
  实例历史全部清零（实测：v2 项目 37 条历史 + 13 步定制模板一夜归零）。
  新增存在性保护：flows/<项目>.yaml 或 instances/<项目>.json 已存在
  时拒绝执行（exit 2），仅 --force 可显式覆盖（且覆盖前自动备份）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import yaml

import cron_coupling  # 生命周期耦合（2026-10-10）：实例落盘 → 心跳 cron 对齐

ENGINE_DIR = Path(__file__).resolve().parent
FLOWS_DIR = ENGINE_DIR / "flows"
INSTANCES_DIR = ENGINE_DIR / "instances"
# 可迁移部署（2026-08-24 工具包整合）：环境变量覆盖，缺省 = 原生布局
PROJECT_ROOT = Path(os.environ.get("FLOW_PROJECTS_ROOT", "."))

TEMPLATE_FLOW = FLOWS_DIR / "template-new-project.yaml"
TEMPLATE_ROADMAP = FLOWS_DIR / "template-roadmap.yaml"


def register_whitelist(project: str) -> None:
    """把新项目 exchange 目录登记进 exchange_dirs.py 白名单（自动化，防漏）。

    2026-08-16 双白名单教训：必须同时登记 _ALL_EXCHANGES（SCAN 继承）+
    WAKE_EXCHANGES（唤醒链路），否则 monitor 能看到但 wake 拒绝（路径不在
    受监控目录内）。
    """
    ed = ENGINE_DIR.parent.parent / "multi-agents" / "scripts" / "exchange_dirs.py"
    if not ed.exists():
        print(f"  ⚠️ exchange_dirs.py 不存在: {ed}")
        return
    src = ed.read_text(encoding="utf-8")
    if project in src:
        print(f"  ⏭ {project} 已在白名单")
        return
    # ① 登记 _ALL_EXCHANGES（memory-hierarchy 行后）
    anchor_all = '    f"{_PROJECT}/memory-hierarchy/exchange",     # 记忆分层（若启用）'
    entry_all = f'\n    f"{{_PROJECT}}/{project}/exchange",     # 引擎开项目 ({project})'
    if anchor_all in src:
        src = src.replace(anchor_all, anchor_all + entry_all)
    # ② 登记 WAKE_EXCHANGES（flow-engine 行后）
    anchor_wake = '    f"{_PROJECT}/flow-engine/exchange",'
    entry_wake = f'\n    f"{{_PROJECT}}/{project}/exchange",  # 引擎开项目 ({project})'
    if anchor_wake in src and f'f"{{_PROJECT}}/{project}/exchange"' not in src.split("WAKE_EXCHANGES")[1].split("]")[0]:
        src = src.replace(anchor_wake, anchor_wake + entry_wake)
    ed.write_text(src, encoding="utf-8")
    print(f"  ✅ {project} 已登记 exchange_dirs.py（_ALL + WAKE 双白名单）")


def gen(project: str, project_dir: Path | None = None,
        force: bool = False, parent: str | None = None, iron_rules: str | None = None,
                  skill_pool: str | None = None) -> int:
    """生成项目全套文件。

    存在性保护（2026-08-20 事故修复）：flows/<项目>.yaml 或
    instances/<项目>.json 已存在 → 拒绝执行（exit 2）。
    --force 可显式覆盖，但覆盖前自动把旧文件备份为
    <name>.overwritten-<时间戳>，防止定制模板/实例历史无声清零。

    项目层级（2026-08-23）：--parent <父项目> 表示本项目是父项目的
    子项目（改进项目）。实例 JSON 写入 "parent": "<父项目>" 字段，
    面板按此字段渲染上下级树。父项目必须已存在（instances 校验）。
    """
    flow_fp = FLOWS_DIR / f"{project}.yaml"
    inst_fp = INSTANCES_DIR / f"{project}.json"
    # 父项目存在性校验（双保险：api.py 已查，CLI 直调也拦）。
    # 存在性 = 引擎实例 JSON 或 Project 根下目录（flow-engine 等早期项目
    # 无实例但真实存在，与 api.py PROJECT_ROOT 判定同源）
    # C1 约束（iter4）：charter §1.4 引擎自修改项目 doker_enabled=false ——
    # --parent 指向的父项目若是引擎自修改项目（实例 JSON doker_enabled=false），
    # 子项目默认继承 false；否则保持 True（2026-09-01 user 拍板）。
    parent_doker_off = False
    if parent:
        parent_fp = INSTANCES_DIR / f"{parent}.json"
        if not parent_fp.exists() and not (PROJECT_ROOT / parent).is_dir():
            print(f"❌ 父项目不存在: {parent}（子项目必须挂在已有项目下）")
            return 3
        try:
            _parent_inst = json.loads(parent_fp.read_text(encoding="utf-8"))
        except Exception:
            _parent_inst = {}  # 仅目录的早期项目无实例 JSON → 按默认 True
        if isinstance(_parent_inst, dict) \
                and _parent_inst.get("doker_enabled") is False:
            parent_doker_off = True
    existing = [p for p in (flow_fp, inst_fp) if p.exists()]
    if existing and not force:
        print(f"❌ 项目已存在，拒绝覆盖（防误重跑清零）: {project}")
        for p in existing:
            print(f"   已存在: {p}")
        print(f"   确认要重开同名项目 → 加 --force（旧文件会自动备份）")
        return 2
    if existing and force:
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        for p in existing:
            bak = p.with_name(f"{p.name}.overwritten-{stamp}")
            shutil.copy2(p, bak)
            print(f"⚠️ --force 覆盖: {p.name} → 备份 {bak.name}")

    pd = project_dir or (PROJECT_ROOT / project)
    ex = pd / "exchange"
    ex.mkdir(parents=True, exist_ok=True)
    # 需求素材包（flow-engine-reqmate 2026-09-05）：materials/ 承载建项目时
    # 用户上传的素材，索引 = materials/MANIFEST.md（面板素材端点写入/追加）。
    # --parent 改进项目同样生效（本函数是两条路径的唯一骨架生成入口）。
    materials = pd / "materials"
    materials.mkdir(parents=True, exist_ok=True)
    (materials / ".gitkeep").touch()
    register_whitelist(project)

    # 1. roadmap.md（按标准阶段）
    rd = yaml.safe_load(TEMPLATE_ROADMAP.read_text(encoding="utf-8"))
    roadmap_lines = [
        f"# ROADMAP — {project} 实施路线图（引擎派发依据, 标准模板生成）",
        "",
        f"> 项目：{project} | 生成：引擎 gen_project.py | 阶段数：{len(rd['stages'])}",
        "",
        "| 阶段 | 动作 | 说明 |",
        "|---|---|---|",
    ]
    for st in rd["stages"]:
        roadmap_lines.append(f"| {st['id']} | {st['action']} | {st['desc']} |")
    (ex / "ROADMAP.md").write_text("\n".join(roadmap_lines), encoding="utf-8")

    # 2. flow 模板（替换占位符）
    flow_src = TEMPLATE_FLOW.read_text(encoding="utf-8")
    flow = (flow_src
            .replace("PROJECT_PLACEHOLDER", project)
            .replace("REVIEW_PLACEHOLDER", f"004-xuanwu-tech-review-{project}")
            .replace("RESULT_PLACEHOLDER", f"*{project}-implement-report")
            .replace("name: new-project", f"name: {project}")
            .replace("title: \"新项目全流程（标准模板）\"", f"title: \"{project}（引擎生成）\""))
    flow_fp.write_text(flow, encoding="utf-8")

    # 2.5 AGENTS.md（工程铁律，2026-08-29 user 拍板：规则刻进工程，不靠 agent 记性）
    # 默认通用纪律 + {{IRON_RULES}} 占位（--iron-rules 传入项目专属铁律；无则提示待 charter 段补）
    agents_tmpl = (Path(__file__).parent / "templates" / "AGENTS.md.template")
    iron = iron_rules or "（立项访谈/charter 阶段补充项目专属铁律；默认仅通用纪律）"
    pool = skill_pool or "（立项访谈/charter 阶段补充；每行一条 `- 环节: skill分类/skill名`）"
    agents_md = (agents_tmpl.read_text(encoding="utf-8")
                 .replace("{{PROJECT}}", project)
                 .replace("{{IRON_RULES}}", iron)
                 .replace("{{SKILL_POOL}}", pool)
                 .replace("{{PROJECT_DIR}}", str(pd)))
    (pd / "AGENTS.md").write_text(agents_md, encoding="utf-8")

    # 3. 实例（起始步骤 = 模板第一个步骤；标准模板 = discuss 需求访谈）
    # 2026-08-21 返工修复（P1a）：原硬编码 step=charter 跳过 discuss，
    # 新建项目永远不出现 grill-me 访谈窗口。改为读模板 steps[0].id。
    flow_obj = yaml.safe_load(flow)
    first_step = ""
    for _s in flow_obj.get("steps", []):
        if _s.get("id"):
            first_step = _s["id"]
            break
    first_step = first_step or "charter"
    inst = {"flow": project, "step": first_step, "iteration": 0,
            "history": [{"step": "system", "action": "init",
                         "detail": f"开引擎 {project} (gen_project.py)"}],
            "status": "running"}
    # 2026-09-01 user 拍板：新项目默认 docker 运行（flow-engine-doker 005 验收通过后落实）
    # 引擎侧契约：doker_enabled=true + doker_project_dir → _run_in_env 容器内执行；
    # 缺 dir 会 RuntimeError（P4 失败显式），所以两字段成对写入。
    # C1 约束（iter4）：约束来源 charter §1.4 引擎自修改项目 doker_enabled=false
    # → 父项目为引擎自修改项目时子项目默认继承 false，否则保持 True。
    if parent_doker_off:
        inst["doker_enabled"] = False
    else:
        inst["doker_enabled"] = True
    inst["doker_project_dir"] = str(pd)
    inst["doker_service"] = "main"
    if parent:
        inst["parent"] = parent
        inst["history"][0]["detail"] = (
            f"开引擎 {project} (gen_project.py, 改进项目 → 父: {parent})")
    inst_fp.write_text(json.dumps(inst, ensure_ascii=False, indent=2), encoding="utf-8")
    # 生命周期耦合（2026-10-10）：新实例 status=running（活动实例）→ 保证心跳在跑。
    # 面板 create_flow 经 subprocess 调本脚本建实例，挂钩在此一处即覆盖两条路径。
    cron_coupling.reconcile_silent()

    # 3.5 docker 目录（Dockerfile + compose.yaml 从模板生成，2026-09-01 默认 docker 化）
    docker_dir = pd / "docker"
    docker_dir.mkdir(parents=True, exist_ok=True)
    tmpl = Path(__file__).parent / "templates"
    (docker_dir / "Dockerfile").write_text(
        (tmpl / "Dockerfile.template").read_text(encoding="utf-8"), encoding="utf-8")
    (docker_dir / "compose.yaml").write_text(
        (tmpl / "compose.template.yaml").read_text(encoding="utf-8")
        # 2026-09-10 修复：Windows 反斜杠路径进 YAML 双引号标量 = 非法转义序列
        # （\A → "found unknown escape character"，we-write-engine 首航实证）。
        # as_posix() 统一正斜杠，Docker Desktop 两平台均接受。
        .replace("{{ENGINE_DIR_MOUNT}}", f"{ENGINE_DIR.as_posix()}:/engine:ro")
        .replace("{{HERMES_HOME_MOUNT}}",
                 f"{(Path.home() / 'AppData' / 'Local' / 'hermes').as_posix()}:/host-hermes:ro"),
        encoding="utf-8")
    scripts_dir = pd / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for _s in ("doker-up.sh", "doker-down.sh", "doker-shell.sh"):
        _src = tmpl / "scripts" / _s
        if _src.exists():
            _dst = scripts_dir / _s
            _dst.write_text(_src.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"✅ 开引擎 {project} 完成：")
    print(f"  项目目录: {pd}")
    print(f"  ROADMAP:  {ex / 'ROADMAP.md'}（{len(rd['stages'])} 阶段）")
    print(f"  FLOW:     {flow_fp}")
    print(f"  实例:     {inst_fp}（step={first_step}, 引擎已就绪）")
    print(f"  下一步: 引擎从 {first_step} 步骤接入，自动跑 立项→双审→拍板→实施→验收→收官")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目名（英文 slug）")
    ap.add_argument("--project-dir", type=Path, default=None)
    ap.add_argument("--force", action="store_true",
                    help="已存在项目也强制覆盖（覆盖前自动备份；默认拒绝）")
    ap.add_argument("--skill-pool", default=None, help="项目 Skill 池（AGENTS.md 内嵌，charter 敲定）")
    ap.add_argument("--iron-rules", default=None, help="项目专属工程铁律（AGENTS.md 内嵌）")
    ap.add_argument("--parent", default=None,
                    help="父项目名（提供时 = 改进现有项目的子项目）")
    args = ap.parse_args()
    if not args.project or not args.project.replace("-", "").isalnum():
        print("❌ 项目名必须为英文 slug（如 my-project）")
        return 1
    return gen(args.project, args.project_dir, force=args.force, parent=args.parent,
               iron_rules=args.iron_rules, skill_pool=args.skill_pool)


if __name__ == "__main__":
    sys.exit(main())

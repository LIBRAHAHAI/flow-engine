"""interview.py — 立项讨论引擎 v2（grill-me 模式, 2026-08-16；2026-08-22 grillme-deep 扩展）。

借鉴 mattpocock/skills grill-me（GitHub 219K stars）的"设计树访谈"机制：
立项不是草草建卡，而是沿设计树逐轮追问，直到 user 确认共识。

核心机制（grilling）：
  1. 设计树：每个决策分支出一串依赖决策
  2. 前沿(frontier)：前置已定的决策 = 当前可问的问题集合
  3. 轮(round)：一轮问整个前沿（互不依赖），user 按编号回答
  4. 问题格式：❓ 编号+标题+正文 / ➡️ 推荐答案（帮 user 快速决策）
  5. 事实 vs 决策：能查的事实 agent 自己查，决策必须等 user
  6. 确认门：前沿空 ≠ 结束，user 说"共识达成"才算完，确认前不推进

2026-08-22 grillme-deep 扩展（002 设计契约，engine.py 零改动）：
  - 节点内 LLM 追问：generate_followup() 异步纯函数（不自行落盘）
    * 配置读 hermes config.yaml providers.custom（base_url/model），
      INTERVIEW_LLM_MODEL / INTERVIEW_LLM_TIMEOUT 环境变量可覆盖
    * api_key 固定 env HERMES_CUSTOM_CUSTOM_API_KEY（严禁落盘/下发前端/进日志）
    * INTERVIEW_LLM_ENABLED=false → 全量骨架化，不发起任何 LLM 调用
    * 单次超时 25s；四路径降级 disabled / no_api_key / timeout|api_error /
      empty_response（空/超 200 字/解析失败）——降级不阻塞访谈
    * LLM 只生成问题文本；[NODE_DONE] 标记 = 本节点已充分覆盖
  - INTERVIEW.json 向后兼容扩展：followups / stopped / llm_degraded
    （旧文件 load_state 用 .get() 默认值兜底，结构断言见 002 §5 C7）
  - 面板数据层（api.py discuss 分支消费）：
    build_view / answer_node / answer_followup / skip_node /
    stop_interview / resume_interview
    ——所有落盘由确定性代码执行（charter D3），LLM 只产问题文本
  - gen_spec 扩展：未答节点输出「（未讨论，采用推荐）」+ 推荐答案；
    已答节点若有已答追问 → 小节追加「追问澄清：」列表
  - CLI ask/answer/gen-spec/confirm 行为向后兼容；DESIGN_TREE 7 节点骨架不动

用法（引擎 discuss 步骤调用）：
  python interview.py --project-dir <dir> ask          # 输出当前轮问题
  python interview.py --project-dir <dir> answer --text "1 是 2 选B"  # 记录回答
  python interview.py --project-dir <dir> gen-spec     # 生成 spec.md（draft）
  python interview.py --project-dir <dir> confirm      # user 确认共识 → confirmed
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:  # CLI 环境无 httpx 时仅 LLM 追问不可用，骨架功能不依赖
    httpx = None

# 立项讨论的标准设计树（根决策 → 依赖决策）
# 每节点：key / question(含推荐答案) / depends_on(前置决策 key) / children
DESIGN_TREE = [
    {
        "key": "purpose", "depends_on": [],
        "question": "这个项目立项的缘由是什么？要解决什么问题、做出什么东西？",
        "recommend": "一句话说清核心价值（如：做一个可玩的坦克大战 HTML5 小游戏，验证引擎驱动开发）",
    },
    {
        "key": "audience", "depends_on": ["purpose"],
        "question": "给谁用？使用场景是什么？",
        "recommend": "个人使用/学习验证/给特定用户",
    },
    {
        "key": "features", "depends_on": ["purpose"],
        "question": "核心功能有哪些？（第一版必须做）",
        "recommend": "列出 3-5 个核心功能（如：移动/射击/敌方AI/基地/计分）",
    },
    {
        "key": "constraints", "depends_on": ["features"],
        "question": "有什么硬性约束？（技术栈/平台/时间/资源/明确不做什么）",
        "recommend": "如：零依赖单文件 HTML5 / 桌面浏览器 / 不做网络对战",
    },
    {
        "key": "success", "depends_on": ["features", "constraints"],
        "question": "怎样算成功？可验证指标？（验收标准）",
        "recommend": "如：打开即玩/10分钟不崩/玩法完整/全流程引擎驱动",
    },
    {
        "key": "scope", "depends_on": ["success"],
        "question": "范围边界？第一版做哪些、明确不做哪些？",
        "recommend": "第一版核心玩法；明确不做：音效美术外包/移动端/网络",
    },
    {
        "key": "timeline", "depends_on": ["scope"],
        "question": "时间预期？（何时要看到结果）",
        "recommend": "尽快出可玩版本（如本周内）",
    },
]

# spec 小节标题（gen_spec 使用；键序 = DESIGN_TREE 序）
_SPEC_SECTIONS = {
    "purpose": "一、立项缘由（为什么做）",
    "audience": "二、受众与场景",
    "features": "三、核心功能（第一版必须做）",
    "constraints": "四、约束（必须遵守）",
    "success": "五、成功指标（可验证）",
    "scope": "六、范围边界（做/不做）",
    "timeline": "七、时间预期",
}

# 跳过类词（与 CLI answer 语义一致）
_SKIP_WORDS = ("跳过", "skip", "推荐", "默认")

# LLM 追问系统提示词（002 §2.2：引用已答内容、禁泛问、[NODE_DONE] 标记、纯问题输出）
_FOLLOWUP_SYSTEM_PROMPT = (
    "你是立项访谈专家。请根据用户已回答的内容，针对当前设计树节点提出一个具体追问。\n"
    "要求：\n"
    "1. 必须引用用户已答内容中的具体信息，并就该信息追问；"
    "禁止泛问（如「能多说点吗？」「还有其他吗？」）。\n"
    "2. 若已答内容已充分覆盖该节点的讨论维度，只输出标记 [NODE_DONE]。\n"
    "3. 只输出问题文本本身：无编号、无前后缀、无解释。\n"
    "4. 问题长度不超过 200 字。"
)

# LLM 追问输出长度上限（002 §2.2 校验）
_FOLLOWUP_MAX_CHARS = 200
# 单节点追问软收敛阈值（002 §2.6：≥10 轮提示词追加倾向 [NODE_DONE]，不硬截断）
_SOFT_CONVERGE_ROUNDS = 10

# hermes 主配置路径（读 providers.custom.base_url / model；env 可覆盖，测试用）。
# 2026-08-30 grc 修复：改为函数动态读取——原模块导入期常量导致 conftest/
# monkeypatch 运行时设置 INTERVIEW_HERMES_CONFIG 不生效，direct 模式单测
# 隐式依赖宿主 config.yaml（providers.custom 清空后 4 用例级联失败，
# NA-266 同款病）。env 未设时行为不变。
def _hermes_config_path() -> Path:
    return Path(os.environ.get(
        "INTERVIEW_HERMES_CONFIG",
        str(Path.home() / "AppData/Local/hermes/config.yaml")))


# ── 状态读写（INTERVIEW.json 单真源） ─────────────────────


def load_state(project_dir: Path) -> dict:
    fp = project_dir / "exchange" / "INTERVIEW.json"
    if fp.exists():
        try:
            st = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            st = {}
    else:
        st = {}
    if not isinstance(st, dict):
        st = {}
    # 默认结构 + grillme-deep 扩展字段（旧文件无新字段 → 默认值兜底，002 §2.4）
    st.setdefault("project", project_dir.name)
    st.setdefault("answers", {})
    st.setdefault("rounds", [])
    st.setdefault("status", "active")
    st.setdefault("stopped", False)
    st.setdefault("followups", {})
    st.setdefault("llm_degraded", False)
    st.setdefault("llm_degraded_reason", "")
    st.setdefault("chat_messages", [])
    st.setdefault("sideline_rounds", {})
    st.setdefault("chat_error_streak", 0)
    return st


def save_state(project_dir: Path, st: dict) -> None:
    fp = project_dir / "exchange" / "INTERVIEW.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _dep_settled(node: dict, answers: dict) -> bool:
    return all(dep in answers for dep in node.get("depends_on", []))


def frontier(st: dict) -> list[dict]:
    """当前前沿：前置已定且自身未答的问题（一轮可问的集合）。"""
    answers = st.get("answers", {})
    return [n for n in DESIGN_TREE
            if n["key"] not in answers and _dep_settled(n, answers)]


def _node_by_key(key: str) -> dict | None:
    for n in DESIGN_TREE:
        if n["key"] == key:
            return n
    return None


def focus_node(st: dict) -> dict | None:
    """面板「当前节点」（002 §1.2/§1.3 的「当前节点」语义）。

    frontier() 是 CLI 顺序语义（answers 落盘即推进，不动）；面板当前节点
    多一层：最近已答节点若仍有未答追问，则停留在该节点（追问流未关闭），
    否则取前沿首节点。追问轮次不设上限（charter G2），[NODE_DONE]/skip
    关闭追问流后自然推进。树全覆盖且无未答追问 → None。
    """
    answers = st.get("answers", {})
    followups = st.get("followups", {})
    # DESIGN_TREE 序的最后一个已答节点 = 最近回答的节点（CLI/面板均顺序作答）
    last_answered = next((n for n in reversed(DESIGN_TREE)
                          if n["key"] in answers), None)
    if last_answered is not None:
        fu_list = followups.get(last_answered["key"], [])
        if any(not f.get("answered", False) for f in fu_list):
            return last_answered
    f = frontier(st)
    return f[0] if f else None


# ── CLI 四动作（行为向后兼容，不触发 LLM） ────────────────


def ask(project_dir: Path) -> str:
    """输出当前问题（一次只问 1 个——grill-me 顺序模式，避免中文数字解析歧义）。"""
    st = load_state(project_dir)
    f = frontier(st)
    if not f:
        answered = len(st.get("answers", {}))
        return (f"【立项讨论】设计树已全部覆盖（{answered} 个决策已回答）。"
                f"说'生成 spec' 生成立项文档，或'确认' 达成共识。")
    node = f[0]
    return (f"【立项讨论 第 {len(st.get('rounds', [])) + 1} 问】\n"
            f"❓ [{node['key']}] {node['question']}\n"
            f"➡️ 推荐：{node['recommend']}\n"
            f"（直接回答即可；说'跳过'用推荐答案）")


def answer(project_dir: Path, text: str) -> str:
    """记录 user 回答（一次一个；'跳过'用推荐答案）。"""
    st = load_state(project_dir)
    f = frontier(st)
    if not f:
        return "【立项讨论】设计树已覆盖。说'生成 spec' 或 '确认'。"
    assigned = 0
    node = f[0]
    val = text.strip()
    if val in _SKIP_WORDS:
        val = node["recommend"]
    if val:
        st["answers"][node["key"]] = val
        assigned = 1
    st["rounds"].append({"round": len(st.get("rounds", [])) + 1,
                         "answers": dict(st["answers"]),
                         "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")})
    save_state(project_dir, st)
    if assigned == 0:
        return "⚠️ 没解析到回答。请按编号回答（如：1 做一个可玩的坦克大战）"
    nxt = frontier(st)
    if not nxt:
        return (f"✅ 已记录 {assigned} 个回答，设计树已覆盖。"
                f"说'生成 spec' 生成立项文档。")
    return f"✅ 已记录 {assigned} 个回答。下一轮问题：\n{ask(project_dir)}"


def _materials_constraints_section(project_dir: Path) -> str:
    """gen-spec 的需求素材约束内容（iter4 C5）。

    MANIFEST 表格逐条引用（每条一行：类型/来源/路径，`\\|` 还原为 `|`）；
    brief 存在时追加其要点（去 frontmatter，前 500 字符）；
    两文件都缺 → 「（无素材登记）」。
    """
    pd = Path(project_dir)
    lines: list[str] = []
    header_skipped = False
    for ln in _read_text_stripped(pd / "materials" / "MANIFEST.md").splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|")
                 for c in re.split(r"(?<!\\)\|", s.strip("|"))]
        if all(re.fullmatch(r":?-{2,}:?", c or "--") for c in cells):
            continue  # 分隔行
        if not header_skipped:
            header_skipped = True  # 首个表格行 = 表头（类型/来源/…）
            continue
        if len(cells) >= 3:
            lines.append(f"- {cells[0]} | {cells[1]} | {cells[2]}")
    brief_txt = re.sub(
        r"^---\n.*?\n---\n?", "",
        _read_text_stripped(pd / "exchange" / "000-requirements-brief.md"),
        flags=re.S).strip()
    if not lines and not brief_txt:
        return "（无素材登记）"
    body = "\n".join(lines) if lines else "（MANIFEST 表格无登记行）"
    if brief_txt:
        body += f"\n\n需求简报要点（前 500 字符）：\n\n{brief_txt[:500]}"
    return body


def gen_spec(project_dir: Path) -> str:
    """基于已答决策生成 spec.md。

    grillme-deep 扩展（002 §3.1，frontmatter/sections/讨论记录段结构不变）：
      - 未答节点 → 「（未讨论，采用推荐）」+ 推荐答案文本（原行为是「（待讨论）」）
      - 已答节点含已答追问 → 小节内追加「追问澄清：」列表
    iter4 C5：「## 八、讨论记录」前新增「## 需求素材约束」小节
    （MANIFEST 逐条引用 + brief 要点；两文件都缺 → 「（无素材登记）」）。
    """
    st = load_state(project_dir)
    answers = st.get("answers", {})
    followups = st.get("followups", {})
    project = st.get("project", project_dir.name)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")
    body = f"""---
title: "{project} 立项 spec（grill-me 设计树访谈产物）"
project: {project}
artifact_type: spec
status: draft  # user 确认后 → confirmed
created_at: "{now}"
updated_at: "{now}"
author: hermes (立项讨论引擎 v2 / grill-me)
assignee: "user (确认共识 → 正式立项)"
---

# {project} 立项 spec

> 由 grill-me 设计树访谈生成。user 确认后，引擎据此起草 charter 四件套。

"""
    for key, title in _SPEC_SECTIONS.items():
        val = answers.get(key)
        node = _node_by_key(key)
        if val is None:
            # 未答节点：未讨论 → 采用推荐（G3 停止冻结语义的 spec 侧落点）
            val = f"（未讨论，采用推荐）\n{node['recommend']}" if node else "（未讨论）"
        body += f"## {title}\n\n{val}\n"
        # 已答追问 → 「追问澄清：」段（002 §3.1 + C8）
        clarified = [f for f in followups.get(key, [])
                     if f.get("answered") and (f.get("answer") or "").strip()]
        if clarified:
            body += "\n追问澄清：\n"
            for f in clarified:
                body += f"- Q: {f['question']}\n  A: {f['answer']}\n"
        body += "\n"
    # iter4 C5：需求素材约束小节（讨论记录之前）
    body += ("## 需求素材约束"
             "（源自 materials/MANIFEST.md 与 000-requirements-brief.md）\n\n"
             + _materials_constraints_section(project_dir) + "\n\n")
    body += "## 八、讨论记录\n\n"
    for r in st.get("rounds", []):
        body += f"- 第 {r['round']} 轮: {json.dumps(r['answers'], ensure_ascii=False)}\n"

    spec_fp = project_dir / "exchange" / "spec.md"
    spec_fp.write_text(body, encoding="utf-8")
    st["status"] = "spec_draft"
    st["spec_file"] = str(spec_fp)
    save_state(project_dir, st)
    return (f"✅ 立项 spec 已生成: {spec_fp}\n"
            f"请审阅后说'确认' → 正式立项（引擎起草 charter）")


def confirm(project_dir: Path) -> str:
    """user 确认共识 → spec confirmed → 引擎可推进。"""
    st = load_state(project_dir)
    spec_fp = project_dir / "exchange" / "spec.md"
    if spec_fp.exists():
        txt = spec_fp.read_text(encoding="utf-8")
        txt = txt.replace("status: draft  # user 确认后 → confirmed",
                          "status: confirmed")
        spec_fp.write_text(txt, encoding="utf-8")
    st["status"] = "confirmed"
    save_state(project_dir, st)
    return ("✅ 共识已确认，正式立项。下一步：引擎起草 charter 四件套 "
            "（基于 spec，说'起草 charter'）")


# ── LLM 追问模块（002 §2，G2） ────────────────────────────


@dataclass
class FollowupResult:
    """generate_followup 返回值（002 §2.2）。

    kind: 'followup'（question 有效）| 'node_done'（本节点已充分覆盖）
          | 'degraded'（降级，reason=disabled/no_api_key/timeout/api_error/
            empty_response）
    """
    kind: str
    question: str = ""
    reason: str = ""


def _llm_enabled() -> bool:
    return os.environ.get("INTERVIEW_LLM_ENABLED", "true").strip().lower() \
        not in ("0", "false", "no")


def _followup_mode() -> str:
    """追问模式（2026-08-29 user 拍板）：
    'agent'（默认）= subprocess 调 default agent（模型跟随全局轮换，
        不再绑单一供应商额度——超算 429 事故根因消解）；
    'direct' = 旧路径 httpx 直连 providers.custom（保留兼容）。
    """
    return os.environ.get("INTERVIEW_FOLLOWUP_MODE", "agent").strip().lower() or "agent"


def _llm_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("INTERVIEW_LLM_TIMEOUT") or 25.0))
    except (TypeError, ValueError):
        return 25.0


def _llm_config() -> tuple[str, str, str]:
    """读 hermes config.yaml providers.custom → (base_url, model, api_key)。

    api_key 固定取 env HERMES_CUSTOM_CUSTOM_API_KEY（002 §2.1 D1：
    严禁进日志/下发前端/写入 INTERVIEW.json）。INTERVIEW_LLM_MODEL 可覆盖模型。
    """
    base_url, model = "", ""
    try:
        import yaml
        cfg = yaml.safe_load(_hermes_config_path().read_text(encoding="utf-8")) or {}
        custom = (cfg.get("providers") or {}).get("custom") or {}
        base_url = str(custom.get("base_url") or "").strip()
        model = str(custom.get("model") or "").strip()
    except Exception:
        pass  # 配置不可读 → 空值 → 降级
    model = os.environ.get("INTERVIEW_LLM_MODEL", "").strip() or model
    api_key = os.environ.get("HERMES_CUSTOM_CUSTOM_API_KEY", "")
    return base_url, model, api_key


def _strip_think(text: str) -> str:
    """剥离 <think>...</think> 推理段（D2/S2b，2026-08-31 enginefix）。

    MiniMax-M3 等推理模型响应含 think 段：不剥离会把正文挤过 200 字上限
    （误判 empty_response）或把推理原文当追问下发。think 段不完整（无闭合）
    时按无 think 处理，返回原文。
    """
    if "<think>" in text and "</think>" in text:
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    return text.strip()


# 需求素材上下文上限（flow-engine-reqmate 2026-09-05）：防 prompt 膨胀。
# iter4 C5：Unicode 字符上限（len() 计数），不宣称 token 等价。
_MATERIALS_CONTEXT_MAX = 4000
# brief 单独上限（iter4 C5 brief 优先）：超限截断时先保 brief 全量，
# 剩余预算给 MANIFEST（合计仍 ≤ _MATERIALS_CONTEXT_MAX）。
_MATERIALS_BRIEF_MAX = 2500


def _read_text_stripped(fp: Path) -> str:
    """读文本并 strip；不存在/读失败 → ""。"""
    try:
        if fp.exists():
            return fp.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        pass
    return ""


def _materials_context(project_dir: Path) -> str:
    """读项目需求素材上下文（建项目时用户提供的描述与素材清单）。

    数据源 = exchange/000-requirements-brief.md + materials/MANIFEST.md。
    iter4 C5 brief 优先：拼接顺序 brief 在前、MANIFEST 在后；超限截断时
    先保 brief 全量（brief 单独上限 2500 字符），剩余预算给 MANIFEST
    （合计仍 ≤ _MATERIALS_CONTEXT_MAX）；两文件都缺/空 → 返回 ""。
    """
    pd = Path(project_dir)
    brief_txt = _read_text_stripped(pd / "exchange" / "000-requirements-brief.md")
    manifest_txt = _read_text_stripped(pd / "materials" / "MANIFEST.md")
    parts: list[str] = []
    if brief_txt:
        if len(brief_txt) > _MATERIALS_BRIEF_MAX:
            brief_txt = brief_txt[:_MATERIALS_BRIEF_MAX - 8] + "\n…(截断)"
        parts.append(f"[需求简报 exchange/000-requirements-brief.md]\n{brief_txt}")
    if manifest_txt:
        label = "[素材清单 materials/MANIFEST.md]"
        # 剩余预算 = 总上限 − brief 块 − 两节间 "\n\n" 分隔
        budget = _MATERIALS_CONTEXT_MAX - ((len(parts[0]) + 2) if parts else 0)
        if budget >= len(label) + 10:  # 预算太小则整节省略（防碎片截断）
            block = f"{label}\n{manifest_txt}"
            if len(block) > budget:
                # 8 ≥ len("\n…(截断)")，保守预留截断标记余量
                keep = budget - len(label) - 1 - 8
                block = f"{label}\n{manifest_txt[:keep]}\n…(截断)"
            parts.append(block)
    return "\n\n".join(parts)


_MATERIALS_SECTION_TITLE = (
    "项目需求素材（建项目时用户提供的描述与素材清单，提问须基于这些内容）：")
# iter4 C5：素材节追加指令——首问/提问须引用素材唯一标识（文件名或 URL）
_MATERIALS_CITE_RULE = (
    "首问/提问中须至少引用 1 个素材唯一标识（文件名或 URL）")


def _build_followup_prompt(node_key: str, node_question: str,
                           qa_pairs: list[dict],
                           project_context: dict,
                           materials_context: str = "") -> str:
    """拼装 user 提示词（引用已答内容；≥10 轮软收敛提示）。

    materials_context（flow-engine-reqmate）：非空时注入需求素材节，
    追问须基于素材内容（002 §T4 注入点 2）。
    """
    lines = [f"项目：{project_context.get('project', '')}",
             f"当前节点：[{node_key}] {node_question}"]
    if materials_context:
        lines.append(_MATERIALS_SECTION_TITLE)
        lines.append(materials_context)
        lines.append(_MATERIALS_CITE_RULE)
    others = project_context.get("answered") or {}
    if others:
        lines.append("其他已定决策（背景）：")
        for k, v in others.items():
            lines.append(f"- {k}: {str(v)[:200]}")
    lines.append("本节点已答内容：")
    for qa in qa_pairs:
        lines.append(f"Q: {qa['question']}")
        lines.append(f"A: {str(qa['answer'])[:300]}")
    n_followups = max(0, len(qa_pairs) - 1)
    if n_followups >= _SOFT_CONVERGE_ROUNDS:
        lines.append(f"（本节点已追问 {n_followups} 轮，"
                     f"若信息已充分，倾向于输出 [NODE_DONE]）")
    return "\n".join(lines)


def _generate_followup_via_agent(node_key: str, node_question: str,
                                 qa_pairs: list[dict],
                                 project_context: dict,
                                 timeout: float,
                                 materials_context: str = "") -> FollowupResult:
    """agent 模式追问：subprocess 调 default agent（模型跟随全局轮换）。

    提示词与 direct 模式同源（_build_followup_prompt），输出约定一致：
    [NODE_DONE] / 单条追问文本。超时/失败/空响应 → 原 degraded 路径，
    调用方按 node_done 对待，骨架问题兜底（§2.3 语义不变）。
    materials_context 非空时注入需求素材节（002 §T4 注入点 2）。
    """
    import subprocess as _sp
    # agent 模式超时：hermes CLI 冷启动 + 模型思考，25s 不够（实测 ~83s）；
    # 上限取 max(传入timeout, 120s)，面板 discuss 是异步调用不阻塞主循环
    timeout = max(timeout, 120.0)
    prompt = _build_followup_prompt(node_key, node_question, qa_pairs,
                                    project_context, materials_context)
    try:
        r = _sp.run(
            ["hermes", "-z", prompt, "--no-restore-cwd"],
            capture_output=True, text=True, timeout=timeout,
        )
    except _sp.TimeoutExpired:
        return FollowupResult("degraded", reason="timeout")
    except Exception:
        return FollowupResult("degraded", reason="api_error")
    content = (r.stdout or "").strip()
    if r.returncode != 0 and not content:
        return FollowupResult("degraded", reason="api_error")
    if "[NODE_DONE]" in content.upper():
        return FollowupResult("node_done")
    content = content.strip().strip('"').lstrip("❓ [").strip()
    if not content or len(content) > _FOLLOWUP_MAX_CHARS:
        return FollowupResult("degraded", reason="empty_response")
    return FollowupResult("followup", question=content)


async def generate_followup(
    node_key: str,
    node_question: str,
    qa_pairs: list[dict],
    project_context: dict,
    timeout: float | None = None,
    project_dir: Path | None = None,
) -> FollowupResult:
    """针对当前节点生成一条 LLM 追问（002 §2.2，纯函数不自行落盘）。

    qa_pairs: 本节点已答 [{question, answer}, ...]（首问 + 历史追问，按时序）
    project_context: {project: 项目名, answered: {其他节点: 已答摘要}}
    project_dir（flow-engine-reqmate）：提供时读需求素材上下文注入追问
    prompt（002 §T4 注入点 2；缺省 = 旧行为不注入）。
    降级四路径（§2.3）：disabled / no_api_key / timeout|api_error /
    empty_response（空/超 200 字/解析失败）。降级不抛异常，调用方按
    node_done 对待（不阻塞访谈）。
    """
    if timeout is None:
        timeout = _llm_timeout()
    materials_context = _materials_context(project_dir) \
        if project_dir is not None else ""
    if not _llm_enabled():
        return FollowupResult("degraded", reason="disabled")
    if _followup_mode() == "agent":
        return _generate_followup_via_agent(node_key, node_question,
                                            qa_pairs, project_context, timeout,
                                            materials_context)
    if httpx is None:
        return FollowupResult("degraded", reason="api_error")
    base_url, model, api_key = _llm_config()
    if not api_key:
        return FollowupResult("degraded", reason="no_api_key")
    if not base_url or not model:
        return FollowupResult("degraded", reason="api_error")

    messages = [
        {"role": "system", "content": _FOLLOWUP_SYSTEM_PROMPT},
        {"role": "user",
         "content": _build_followup_prompt(node_key, node_question,
                                           qa_pairs, project_context,
                                           materials_context)},
    ]
    url = base_url.rstrip("/") + "/chat/completions"
    # D2 fallback 链（2026-08-31 enginefix / S2a）：primary 失败 → 自动 fallback
    # 第二 provider（默认本地 Ollama gemma4:12b，charter §3 D2 拍板）。
    # 最多两层（端木 011 §6.2 边界：只两层，扩展须 escalate user）。
    fallback = os.environ.get(
        "INTERVIEW_LLM_FALLBACK",
        "http://localhost:11434/v1|gemma4:12b|ollama").split("|")
    fb_url = fallback[0].strip().rstrip("/") + "/chat/completions" if fallback[0].strip() else ""
    fb_model = fallback[1].strip() if len(fallback) > 1 else ""
    fb_key = fallback[2].strip() if len(fallback) > 2 else ""
    attempts = [{"url": url, "model": model, "key": api_key}]
    if fb_url and fb_model:
        attempts.append({"url": fb_url, "model": fb_model, "key": fb_key})
    content = ""
    last_reason = "api_error"
    for i, att in enumerate(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    att["url"],
                    headers={"Authorization": f"Bearer {att['key']}"},
                    json={"model": att["model"], "messages": messages,
                          "temperature": 0.7, "max_tokens": 300},
                )
            if resp.status_code // 100 != 2:
                last_reason = "api_error"
                continue  # 非 2xx → 尝试 fallback
            data = resp.json()
            content = str(data["choices"][0]["message"]["content"] or "")
            break
        except Exception as e:
            # 超时与网络错误分流（httpx.TimeoutException 是独立异常类）
            last_reason = ("timeout"
                           if httpx is not None and isinstance(e, httpx.TimeoutException)
                           else "api_error")
            continue  # primary 挂 → fallback
    else:
        return FollowupResult("degraded", reason=last_reason)
    if i > 0:
        print(f"[interview] primary 失败，fallback 第 {i} provider 成功", flush=True)

    if "[NODE_DONE]" in content.upper():
        return FollowupResult("node_done")
    content = _strip_think(content).strip()
    if not content or len(content) > _FOLLOWUP_MAX_CHARS:
        return FollowupResult("degraded", reason="empty_response")
    return FollowupResult("followup", question=content)


# ── 面板数据层（api.py discuss 分支消费；落盘全部确定性执行） ──


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _project_context(st: dict, node_key: str) -> dict:
    answers = st.get("answers", {})
    return {
        "project": st.get("project", ""),
        "answered": {k: v for k, v in answers.items() if k != node_key},
    }


def build_view(project_dir: Path) -> dict:
    """构建 grill-context 的 interview 对象（002 §1.2，纯读 + 一次性降级信号）。

    树全覆盖时 nodeKey=None（前端渲染「生成 spec / 确认共识」区）。
    llm_degraded 为一次性信号：本次读出后即清除（避免反复弹降级提示）。
    """
    st = load_state(project_dir)
    node = focus_node(st)
    answers = st.get("answers", {})
    key = node["key"] if node else None
    fu_list = st.get("followups", {}).get(key, []) if key else []
    view = {
        "nodeKey": key,
        "nodeQuestion": node["question"] if node else None,
        "recommendedAnswer": node["recommend"] if node else None,
        "followups": [
            {"index": i, "question": fu.get("question", ""),
             "answered": bool(fu.get("answered", False)),
             "answer": fu.get("answer")}
            for i, fu in enumerate(fu_list)
        ],
        "nodeAnswered": (key in answers) if node else None,
        "progress": {"answered": len(answers), "total": len(DESIGN_TREE)},
        "stopped": bool(st.get("stopped", False)),
        "llmDegraded": bool(st.get("llm_degraded", False)),
        "degradedReason": st.get("llm_degraded_reason", "") or "",
        "status": st.get("status", "active"),
    }
    # 一次性降级信号：读后清除（002 §2.3）
    if st.get("llm_degraded") or st.get("llm_degraded_reason"):
        st["llm_degraded"] = False
        st["llm_degraded_reason"] = ""
        save_state(project_dir, st)
    return view


def _ensure_active(st: dict) -> None:
    """stopped 冻结后禁止 answer_*（002 §2.6）。"""
    if st.get("stopped"):
        raise ValueError("访谈已停止，请确认 spec（如需继续访谈请先复位）")


def _ensure_frontier_key(st: dict, node_key: str) -> dict:
    """节点 key 必须与当前焦点节点一致（防串会话/过期请求）。"""
    node = focus_node(st)
    if node is None:
        raise ValueError("设计树已全部覆盖，无可答节点")
    if not node_key or node["key"] != node_key:
        raise ValueError(
            f"节点 key 与当前前沿不符: 收到 {node_key!r}，"
            f"当前应为 {node['key']!r}")
    return node


async def _apply_followup_result(project_dir: Path, st: dict,
                                 node: dict, res: FollowupResult) -> str:
    """LLM 追问结果落盘：追问入列 / node_done 不动 / 降级写一次性信号。"""
    key = node["key"]
    if res.kind == "followup":
        st.setdefault("followups", {}).setdefault(key, []).append(
            {"question": res.question, "answer": None,
             "answered": False, "ts": _now()})
        save_state(project_dir, st)
        return ""
    if res.kind == "degraded":
        st["llm_degraded"] = True
        st["llm_degraded_reason"] = res.reason
        save_state(project_dir, st)
        return res.reason
    return ""  # node_done：本节点追问关闭，frontier 自然推进


async def answer_node(project_dir: Path, node_key: str,
                      user_text: str) -> dict:
    """回答当前节点首问（002 §1.3 answer_node）。

    跳过类词 → 推荐答案（复用 CLI 语义）；同 key 重复提交 = 覆盖 +
    followups 重置（§1.3 幂等）；随后触发 LLM 追问（≤25s）。
    """
    st = load_state(project_dir)
    _ensure_active(st)
    node = _ensure_frontier_key(st, node_key)
    val = (user_text or "").strip()
    if val in _SKIP_WORDS:
        val = node["recommend"]
    if not val:
        raise ValueError("回答内容为空")
    st["answers"][node["key"]] = val
    st.setdefault("followups", {})[node["key"]] = []  # 覆盖语义：重置追问序列
    st.setdefault("rounds", []).append(
        {"round": len(st.get("rounds", [])) + 1,
         "answers": dict(st["answers"]), "ts": _now()})
    save_state(project_dir, st)

    qa_pairs = [{"question": node["question"], "answer": val}]
    res = await generate_followup(
        node["key"], node["question"], qa_pairs,
        _project_context(st, node["key"]), project_dir=project_dir)
    reason = await _apply_followup_result(project_dir, st, node, res)
    return {"view": build_view(project_dir),
            "treeComplete": not frontier(st),
            "degradedReason": reason}


async def answer_followup(project_dir: Path, node_key: str,
                          followup_index: int, user_text: str) -> dict:
    """回答第 followup_index 条追问（002 §1.3 answer_followup）。

    轮次不设上限；答后再次触发 LLM 追问（LLM 可判 node_done）。
    """
    st = load_state(project_dir)
    _ensure_active(st)
    node = _ensure_frontier_key(st, node_key)
    key = node["key"]
    fu_list = st.get("followups", {}).get(key, [])
    if followup_index is None or followup_index < 0 \
            or followup_index >= len(fu_list):
        raise ValueError(
            f"followupIndex 越界: {followup_index}（共 {len(fu_list)} 条）")
    val = (user_text or "").strip()
    if not val:
        raise ValueError("回答内容为空")
    fu = fu_list[followup_index]
    fu["answer"] = val
    fu["answered"] = True
    fu["ts"] = _now()
    save_state(project_dir, st)

    qa_pairs = [{"question": node["question"],
                 "answer": st["answers"].get(key, "")}]
    qa_pairs += [{"question": f.get("question", ""), "answer": f.get("answer")}
                 for f in fu_list if f.get("answered") and f.get("answer")]
    res = await generate_followup(
        key, node["question"], qa_pairs, _project_context(st, key),
        project_dir=project_dir)
    reason = await _apply_followup_result(project_dir, st, node, res)
    return {"view": build_view(project_dir),
            "treeComplete": not frontier(st),
            "degradedReason": reason}


def skip_node(project_dir: Path, node_key: str) -> dict:
    """跳过当前节点：采用推荐答案，不触发 LLM（002 §1.3 skip_node）。

    契约「推进到下一节点」的两重保证：
      - 未答节点 → answers[key]=推荐答案（原语义）；已答节点保留用户原答
        （跳过仅用于关闭挂起追问流，不破坏已落盘的答复）
      - 无论何种情况都重置该节点追问流（否则未答追问挂起时焦点原地死循环，
        活体冒烟实证：LLM 连续追问时 skip 无法推进）
    """
    st = load_state(project_dir)
    _ensure_active(st)
    node = _ensure_frontier_key(st, node_key)
    key = node["key"]
    if key not in st.get("answers", {}):
        st["answers"][key] = node["recommend"]
        st.setdefault("rounds", []).append(
            {"round": len(st.get("rounds", [])) + 1,
             "answers": dict(st["answers"]), "ts": _now()})
    # 关闭追问流（含已答节点的挂起追问）→ focus_node 自然推进
    st.setdefault("followups", {})[key] = []
    save_state(project_dir, st)
    return {"view": build_view(project_dir),
            "treeComplete": not frontier(st),
            "degradedReason": ""}


def stop_interview(project_dir: Path) -> dict:
    """停止追问（G3）：冻结问题流 + 生成 spec（未答节点采用推荐）。

    幂等（002 §1.4）：已停止且 spec 存在 → 直接返回已有路径，不重复生成。
    """
    st = load_state(project_dir)
    spec_fp = project_dir / "exchange" / "spec.md"
    if st.get("stopped") and spec_fp.exists():
        return {"specFile": str(spec_fp), "alreadyStopped": True,
                "summary": "访谈已冻结，返回已生成的 spec（幂等）"}
    st["stopped"] = True
    save_state(project_dir, st)
    gen_spec(project_dir)  # 扩展版：未答节点「（未讨论，采用推荐）」
    st = load_state(project_dir)
    unanswered = len(DESIGN_TREE) - len(st.get("answers", {}))
    summary = ("已冻结问题流，" +
               (f"未答 {unanswered} 节点采用推荐" if unanswered > 0
                else "全部节点已讨论"))
    return {"specFile": str(spec_fp), "alreadyStopped": False,
            "summary": summary}


def resume_interview(project_dir: Path) -> dict:
    """继续访谈（002 §3.3）：驳回回 discuss 后复位 stopped，重开问题流。

    answers/followups 保留（续谈不丢已答）；spec_draft 状态复位 active。
    """
    st = load_state(project_dir)
    st["stopped"] = False
    if st.get("status") == "spec_draft":
        st["status"] = "active"
    save_state(project_dir, st)
    return {"view": build_view(project_dir)}


# ── 实时对话层（grc M1，2026-08-30 grillme-conversation 002 方案） ──
#
# hermes chat CLI 会话复用（spike 实证 2026-08-30）：
#   - `hermes chat -q <text> -Q` 新建会话，stdout 含 `session_id: <id>` 行
#   - `hermes chat --resume <id> -q <text> -Q` 续接历史（-z/--continue 均不注入历史，勿用）
#   - 每轮 ~5-8s（S1 ≤10s 达标）；worker 内嵌套调用须清 HERMES_KANBAN_* env（否则挂死）
#   - `-Q` 输出末两行 = `session_id: ...` + 回复正文

# 会话层每轮超时（秒）：spike 实测 5-8s，留余量
_CHAT_TURN_TIMEOUT = 60.0
# 环境变量黑名单：kanban worker 内嵌套 hermes 会挂死（t_b4ebc051 spike 实证 20min 零输出）
_CHAT_ENV_STRIP = ("HERMES_KANBAN_TASK", "HERMES_KANBAN_WORKSPACE",
                   "HERMES_MAX_ITERATIONS")


def parse_reply(text: str) -> dict:
    """解析 agent 会话回复的三类标记（002 方案 §2.2）。

    返回 {kind, payload}：
      kind='next'        payload=节点 key（主线推进语义）
      kind='node_done'   payload=''（本节点充分，skip_node 语义）
      kind='plain'       payload=原文（支线追问/纯文本）
    容错：[NEXT:未知节点] 不在本树 → 按 plain 处理（防幻觉节点名断链）。
    [NODE_DONE] 允许出现在句中（模型习惯性追加说明），优先级低于 [NEXT:]。
    """
    t = (text or "").strip()
    import re
    m = re.search(r"\[NEXT:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]", t)
    if m:
        key = m.group(1).lower()
        if _node_by_key(key) is not None:
            return {"kind": "next", "payload": key}
        return {"kind": "plain", "payload": t}
    if "[NODE_DONE]" in t.upper():
        return {"kind": "node_done", "payload": ""}
    return {"kind": "plain", "payload": t}


def _chat_env() -> dict:
    """子进程 env：剥离 HERMES_KANBAN_*（嵌套挂死防护，spike 实证）。"""
    env = {k: v for k, v in os.environ.items() if k not in _CHAT_ENV_STRIP}
    return env


def _run_chat(text: str, session_id: str | None) -> tuple[str, str]:
    """调一次 hermes chat；返回 (session_id, 回复正文)。

    新建（session_id=None）：`hermes chat -q <text> -Q`
    续接：`hermes chat --resume <sid> -q <text> -Q`
    正文提取：stdout 最后一个 `session_id: ...` 行之后的全部内容。
    subprocess 异常向上抛（调用方负责重试/降级状态机）。
    """
    import subprocess as _sp
    cmd = ["hermes", "chat", "-Q", "-q", text]
    if session_id:
        cmd += ["--resume", session_id]
    r = _sp.run(cmd, capture_output=True, text=True,
                timeout=_CHAT_TURN_TIMEOUT, env=_chat_env())
    out = (r.stdout or "").strip()
    if r.returncode != 0 and not out:
        raise RuntimeError(f"hermes chat 失败 rc={r.returncode}: "
                           f"{(r.stderr or '')[-200:]}")
    # 管道模式下（capture_output）：正文走 stdout，`session_id:` 行走 stderr
    # （TTY 合并流时两者同流，spike 观测到的是合并形态）
    merged = f"{r.stderr or ''}\n{r.stdout or ''}"
    sid = ""
    for line in merged.splitlines():
        ls = line.strip()
        if ls.startswith("session_id:"):
            sid = ls.split(":", 1)[1].strip()
    body = out
    if not sid:
        raise RuntimeError("hermes chat 输出未包含 session_id 行（格式变更?）: "
                           f"{(r.stderr or '')[-120:]} / {out[-120:]}")
    if not body:
        raise RuntimeError("hermes chat 正文为空")
    return sid, body


class AgentSession:
    """常驻 hermes 会话管理器（grc M1，002 方案 §2.1）。

    .session 落盘于 <project_dir>/exchange/.session（json: {session_id}）；
    死亡恢复 revive() 注入摘要起新会话覆写。
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.session_fp = self.project_dir / "exchange" / ".session"

    def _read(self) -> dict:
        try:
            d = json.loads(self.session_fp.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _write(self, session_id: str) -> None:
        self.session_fp.parent.mkdir(parents=True, exist_ok=True)
        self.session_fp.write_text(
            json.dumps({"session_id": session_id,
                        "updated_at": _now()}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    def start(self, opening_prompt: str) -> str:
        """首轮：起会话 + 落盘 session_id。返回 agent 回复。"""
        sid, body = _run_chat(opening_prompt, None)
        self._write(sid)
        return body

    def resume(self) -> dict:
        """读 .session。返回 {session_id} 或 {}（无落盘）。"""
        return self._read()

    def revive(self, summary_prompt: str) -> str:
        """会话死亡恢复：注入摘要起新会话，覆写 .session。"""
        return self.start(summary_prompt)

    def send(self, text: str) -> str:
        """续接会话发一条消息。返回 agent 回复；会话失效抛异常。"""
        sid = self._read().get("session_id")
        if not sid:
            raise RuntimeError("无 .session，需先 start()")
        _, body = _run_chat(text, sid)
        return body


def _chat_opening_prompt(project_dir: Path, st: dict) -> str:
    """开场提示词：角色 + 设计树节点清单 + 标记协议（002 方案 §2.1）。

    需求素材注入点 1（flow-engine-reqmate，002 §T4）：有素材上下文时
    加「项目需求素材」节，agent 首问/提问须基于这些内容。
    """
    import json as _json
    tree_lines = []
    for n in DESIGN_TREE:
        tree_lines.append(f"- [{n['key']}] {n['question']}（推荐：{n['recommend']}）")
    answered = st.get("answers", {})
    answered_lines = [f"- [{k}] 已答：{str(v)[:200]}"
                      for k, v in answered.items()] or ["- （暂无）"]
    project = st.get("project", project_dir.name)
    mctx = _materials_context(project_dir)
    materials_block = (
        f"\n\n{_MATERIALS_SECTION_TITLE}\n{mctx}\n{_MATERIALS_CITE_RULE}"
        if mctx else "")
    return (
        f"你现在是「{project}」项目的立项访谈主持人，与项目发起人实时对话。\n"
        f"你的任务：沿下面的设计树逐节点访谈，每个节点追问到信息充分后推进下一节点；"
        f"同时可就回答内容自由支线追问挖深度。\n"
        f"\n设计树节点：\n" + "\n".join(tree_lines) +
        f"\n\n已回答节点：\n" + "\n".join(answered_lines) +
        materials_block +
        "\n\n标记协议（严格遵守）：\n"
        "- 本节点访谈充分、要推进到指定节点时，回复正文末尾加 [NEXT:节点key]\n"
        "- 本节点已充分但下一节点由系统指定时，只回复 [NODE_DONE]\n"
        "- 支线追问时输出纯文本问题（不用标记），一次最多一个问题，不超过 200 字\n"
        "- 开场请直接问候并开始第一个未答节点（纯文本 + 问题）\n")


def _chat_summary_prompt(st: dict, project_dir: Path) -> str:
    """revive 摘要提示词：设计树位置 + 已答 QA + 支线额度（002 方案 §2.1）。

    iter4 C5：传真实 project_dir（原 Path(".") 占位导致素材上下文读不到，
    恢复后的会话丢失需求素材注入）。
    """
    p = _chat_opening_prompt(project_dir, st)  # 复用树/已答/协议描述
    return p + ("\n（注：这是会话恢复，直接从当前未答节点继续提问即可）\n")


async def chat_turn(project_dir: Path, text: str) -> dict:
    """实时对话编排入口（grc M1，002 方案 §2.5）。

    text 为空 = 首轮（start + agent 开场）。
    返回 {messages: [{role, text, ts}], reply, parse, mode}：
      - messages 同时落盘 INTERVIEW.json `chat_messages`（向后兼容新字段）
      - parse.kind='next' → 主线推进（落盘答案由调用方或本函数按节点走 answer 语义）
      - parse.kind='node_done' → skip_node 语义
      - parse.kind='plain' → 支线（落盘 chat_messages，轮次计数）
    异常降级（002 方案 §2.4）：单轮重试 1 次 → 骨架降级记 llm_degraded_reason
    → error_streak ≥3 时 revive 注入摘要重启会话。
    mode != 'agent' 时本函数不应被调用（api 层路由 direct/skeleton 旧路径）。
    """
    import asyncio
    st = load_state(project_dir)
    st.setdefault("chat_messages", [])
    st.setdefault("sideline_rounds", {})   # {node_key: 本节点支线轮数}
    st.setdefault("chat_error_streak", 0)
    first = not text.strip()

    async def _call() -> str:
        # subprocess 阻塞调用放线程池，不卡事件循环
        sess = AgentSession(project_dir)
        loop = asyncio.get_event_loop()
        if first:
            st_live = load_state(project_dir)
            return await loop.run_in_executor(
                None, sess.start, _chat_opening_prompt(project_dir, st_live))
        sid = sess.resume().get("session_id")
        if not sid:
            st_live = load_state(project_dir)
            return await loop.run_in_executor(
                None, sess.start, _chat_opening_prompt(project_dir, st_live))
        return await loop.run_in_executor(None, sess.send, text)

    reply, degraded = "", ""
    try:
        try:
            reply = await _call()
        except Exception:
            # 单轮重试 1 次
            reply = await _call()
    except Exception as e:
        degraded = f"chat_error: {type(e).__name__}"
        st["chat_error_streak"] = st.get("chat_error_streak", 0) + 1
        if st["chat_error_streak"] >= 3:
            # 连 3 轮异常：revive 注入摘要重启会话
            # （iter4 C5：传真实 project_dir，恢复会话保留素材上下文注入）
            try:
                AgentSession(project_dir).revive(
                    _chat_summary_prompt(st, project_dir))
                st["chat_error_streak"] = 0
                degraded += " (session_revived)"
            except Exception:
                pass  # revive 也失败 → 保持 streak，下轮再试
        if first:
            # 首轮就失败 → 完全骨架降级信号（调用方切静态路径）
            st["llm_degraded"] = True
            st["llm_degraded_reason"] = degraded
        save_state(project_dir, st)
        return {"messages": st["chat_messages"][-20:], "reply": "",
                "parse": {"kind": "degraded", "payload": degraded},
                "degraded": degraded}

    st["chat_error_streak"] = 0
    parsed = parse_reply(reply)

    if first:
        st["chat_messages"].append({"role": "agent", "text": reply,
                                    "ts": _now(), "node": None})
    else:
        # 支线/主线轮：user 消息 + agent 回复落盘 + 边界计数
        node = focus_node(st)
        nk = node["key"] if node else "_free"
        if parsed["kind"] == "plain":
            st.setdefault("sideline_rounds", {})[nk] = \
                st.get("sideline_rounds", {}).get(nk, 0) + 1
        st.setdefault("chat_messages", []).append(
            {"role": "user", "text": text.strip(), "ts": _now(), "node": nk})
        st["chat_messages"].append({"role": "agent", "text": reply,
                                    "ts": _now(), "node": nk})
        # 主线推进语义（002 方案 §1）：[NEXT:]/[NODE_DONE] 时本轮 user 文本
        # 即当前节点的最终答复 → 走确定性落盘（复用 answer/skip 语义）
        if parsed["kind"] in ("next", "node_done") and node is not None \
                and (text or "").strip():
            if nk not in st.get("answers", {}):
                st["answers"][nk] = text.strip()
                st.setdefault("rounds", []).append(
                    {"round": len(st.get("rounds", [])) + 1,
                     "answers": dict(st["answers"]), "ts": _now()})
            if parsed["kind"] == "node_done":
                st.setdefault("followups", {})[nk] = []  # 关闭追问流
    save_state(project_dir, st)
    return {"messages": st.get("chat_messages", [])[-20:], "reply": reply,
            "parse": parsed, "degraded": ""}


def chat_messages_view(project_dir: Path) -> list[dict]:
    """读 chat_messages（grill-context 的 chat.messages 数据源，纯读）。"""
    return load_state(project_dir).get("chat_messages", [])


def chat_mode() -> str:
    """INTERVIEW_FOLLOWUP_MODE 三值（agent=实时对话默认 / direct / skeleton）。"""
    return _followup_mode()


# ── CLI 入口（不变） ──────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", required=True)
    ap.add_argument("--action", choices=["ask", "answer", "gen-spec", "confirm"],
                    default="ask")
    ap.add_argument("--text", default="")
    args = ap.parse_args()

    pd = Path(args.project_dir)
    if args.action == "ask":
        print(ask(pd))
    elif args.action == "answer":
        print(answer(pd, args.text))
    elif args.action == "gen-spec":
        print(gen_spec(pd))
    elif args.action == "confirm":
        print(confirm(pd))
    return 0


if __name__ == "__main__":
    sys.exit(main())

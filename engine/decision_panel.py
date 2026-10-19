"""decision_panel.py — 拍板面板本地服务（v0.1, 2026-08-16）。

用户看不到拍板 → 提供可视化面板：
- 显示所有引擎实例状态（当前步骤/等待什么/历史）
- 待拍板项醒目展示 + 拍板按钮（同意/驳回）
- 点击按钮 → 写 <flow>.decision 文件 → 引擎心跳下轮读取推进

用法：
  python decision_panel.py            # 默认 0.0.0.0:8650
  python decision_panel.py --port 8651

依赖：标准库 http.server（零安装）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENGINE_DIR = Path(os.environ.get(
    "FLOW_ENGINE_DIR", str(Path(__file__).resolve().parent)))
INSTANCES_DIR = ENGINE_DIR / "instances"
FLOWS_DIR = ENGINE_DIR / "flows"
ENGINE_PY = ENGINE_DIR / "engine.py"

# 已终态（不显示为待办）
CLOSED = {"completed", "failed", "idle"}


def _load_instance(name: str) -> dict | None:
    fp = INSTANCES_DIR / f"{name}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_flow_title(name: str) -> str:
    fp = FLOWS_DIR / f"{name}.yaml"
    if not fp.exists():
        return name
    try:
        import yaml
        d = yaml.safe_load(fp.read_text(encoding="utf-8"))
        return d.get("title", name)
    except Exception:
        return name


def _load_step(name: str, step: str) -> dict | None:
    """从流程模板查完整步骤定义（含 options）。"""
    fp = FLOWS_DIR / f"{name}.yaml"
    if not fp.exists():
        return None
    try:
        import yaml
        d = yaml.safe_load(fp.read_text(encoding="utf-8"))
        for s in d.get("steps", []):
            if s.get("id") == step:
                return s
    except Exception:
        pass
    return None


def _load_step_type(name: str, step: str) -> str:
    """从流程模板查步骤类型（正确判定拍板点，而非步骤名含 decision）。"""
    s = _load_step(name, step)
    return s.get("type", "") if s else ""


def _collect_state() -> list[dict]:
    """汇总所有引擎实例状态。"""
    rows = []
    if not INSTANCES_DIR.is_dir():
        return rows
    for fp in sorted(INSTANCES_DIR.glob("*.json")):
        name = fp.stem
        st = _load_instance(name)
        if not st:
            continue
        step = st.get("step", "?")
        rows.append({
            "name": name,
            "title": _load_flow_title(name),
            "status": st.get("status", "?"),
            "step": step,
            "step_type": _load_step_type(name, step),
            "step_def": _load_step(name, step) or {},
            "iteration": st.get("iteration", 0),
            "history": st.get("history", [])[-6:],
        })
    return rows


def _pending_decisions() -> list[dict]:
    """扫描待 user 拍板（引擎实例在 decision 步骤 = 待拍板；含 exchange 卡）。"""
    pending = []
    for r in _collect_state():
        if r["status"] not in CLOSED:
            pending.append(r)
    return pending


def _html() -> str:
    rows = _collect_state()
    pending = [r for r in rows if r["status"] == "running" and r.get("step_type") == "decision"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _row_card(r: dict) -> str:
        step = r["step"]
        status = r["status"]
        badge = ("🟡" if status == "running" else "⚪" if status == "idle" else "🔴")
        hist = "".join(
            f"<div class='h'>{h.get('action','?')} "
            f"#{h.get('iteration','')} — {str(h.get('detail') or h.get('log') or h.get('decision') or '')[:80]}</div>"
            for h in r["history"][-4:]) or "<div class='h dim'>（无历史）</div>"
        # 拍板按钮：仅当流程 running 且当前步骤类型为 decision（查模板，非步骤名）
        decision_btns = ""
        context_html = ""
        if status == "running" and r.get("step_type") == "decision":
            step_def = _load_step(r["name"], step)
            opts = (step_def or {}).get("options") or []
            # 拍板上下文：step_def.context_files 指向的立项/评审文件摘要
            ctx_files = (step_def or {}).get("context_files") or []
            for cf in ctx_files[:4]:
                p = Path(str(cf))
                if not p.exists():
                    continue
                txt = p.read_text(encoding="utf-8", errors="ignore")
                fm = __import__("re").match(r"^---\n(.*?)\n---", txt, __import__("re").S)
                block = fm.group(1) if fm else ""
                title_m = __import__("re").search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", block, __import__("re").M)
                title = title_m.group(1) if title_m else p.stem
                # 取正文前 400 字（去 frontmatter）
                body = txt[len(fm.group(0)):] if fm else txt
                body = __import__("re").sub(r"\s+", " ", body).strip()[:400]
                context_html += (
                    f"<div class='ctx'>"
                    f"<div class='ctx-title'>📄 {title[:60]}</div>"
                    f"<div class='ctx-body'>{body}</div>"
                    f"</div>")
            if context_html:
                context_html = f"<div class='ctx-box'><div class='ctx-hdr'>📋 拍板上下文（立项/评审要点）</div>{context_html}</div>"
            if opts:
                # 选项模式：A/B/C 各一个按钮
                btn_html = ""
                for opt in opts:
                    key = str(opt.get("key", ""))
                    label = str(opt.get("label", key))[:38]
                    btn_html += (
                        f"<button onclick='decide(\"{r['name']}\",\"{key}\")' "
                        f"class='opt' title='{label}'>{key}. {label}</button>")
                decision_btns = f"<div class='btns'>{btn_html}</div>"
            else:
                decision_btns = (
                    "<div class='btns'>"
                    f"<button onclick='decide(\"{r['name']}\",\"同意\")' class='ok'>✅ 同意</button>"
                    f"<button onclick='decide(\"{r['name']}\",\"驳回\")' class='no'>❌ 驳回</button>"
                    "</div>")
        return (
            f"<div class='card'>"
            f"<h3>{badge} {r['title']} <span class='id'>{r['name']}</span></h3>"
            f"<div class='meta'>状态 <b>{status}</b> · 当前步骤 <b>{step}</b> · 迭代 {r['iteration']}</div>"
            f"<div class='hist'>{hist}</div>"
            f"{context_html}"
            f"{decision_btns}"
            f"</div>")

    cards = "".join(_row_card(r) for r in rows) or "<div class='dim'>暂无引擎实例</div>"

    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>引擎拍板面板</title>
<style>
 body{{font-family:system-ui,sans-serif;background:#0f1220;color:#e8eaf0;margin:0;padding:20px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#8a90a5;font-size:13px;margin-bottom:16px}}
 .card{{background:#1a1e30;border:1px solid #2a2f45;border-radius:10px;padding:14px 16px;margin-bottom:12px}}
 h3{{margin:0 0 6px;font-size:16px}} .id{{color:#6b7bd8;font-size:12px;margin-left:8px}}
 .meta{{color:#aab;font-size:13px;margin-bottom:8px}}
 .hist{{font-size:12px;color:#9aa;background:#12152a;border-radius:6px;padding:8px;max-height:90px;overflow:auto}}
 .h{{padding:2px 0}} .dim{{color:#667;font-style:italic}}
 .btns{{margin-top:10px;display:flex;gap:10px}}
 button{{border:none;border-radius:8px;padding:9px 18px;font-size:14px;font-weight:600;cursor:pointer}}
 .ok{{background:#1f6f43;color:#fff}} .ok:hover{{background:#27865a}}
 .no{{background:#7a2530;color:#fff}} .no:hover{{background:#96303c}}
 .opt{{background:#2a3a6b;color:#fff}} .opt:hover{{background:#35508f}}
 .ctx-box{{background:#131a2e;border:1px solid #2a3a5a;border-radius:8px;padding:10px 12px;margin-top:10px}}
 .ctx-hdr{{font-size:12px;color:#8ab;margin-bottom:6px;font-weight:600}}
 .ctx{{background:#0e1224;border-radius:6px;padding:8px 10px;margin-bottom:6px}}
 .ctx-title{{font-size:12px;color:#9ab;margin-bottom:3px}}
 .ctx-body{{font-size:12px;color:#889;line-height:1.5;max-height:70px;overflow:auto}}
 .flash{{position:fixed;top:16px;right:16px;background:#223;border:1px solid #446;padding:10px 16px;border-radius:8px;display:none}}
 .pending-banner{{background:#3a2d14;border:1px solid #6b5218;border-radius:10px;padding:10px 16px;margin-bottom:14px;font-size:14px}}
</style></head><body>
<h1>⚙️ 引擎拍板面板</h1>
<div class="sub">刷新于 {now} · 每 10 秒自动刷新 · <a href="/" style="color:#6b7bd8">手动刷新</a></div>
{"".join(f"<div class='pending-banner'>⏸ <b>待拍板：{r['title']}</b>（{r['name']} @ {r['step']}）</div>" for r in pending)}
{cards}
<div id="flash" class="flash"></div>
<script>
async function decide(flow, decision){{
  const r = await fetch('/decide?flow='+encodeURIComponent(flow)+'&decision='+encodeURIComponent(decision));
  const t = await r.text();
  const f = document.getElementById('flash');
  f.textContent = t; f.style.display='block';
  setTimeout(()=>{{f.style.display='none'; location.reload();}}, 800);
}}
setInterval(()=>location.reload(), 10000);
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, ctype: str = "text/html; charset=utf-8", code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/decide"):
            import urllib.parse
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            flow = q.get("flow", [""])[0]
            decision = q.get("decision", [""])[0]
            if not flow or not decision:
                self._send("参数缺失 flow/decision", code=400)
                return
            # 仅允许在 decision 步骤拍板（防误写）——查模板步骤类型，非步骤名
            st = _load_instance(flow)
            if not st:
                self._send(f"流程实例不存在: {flow}", code=404)
                return
            step_type = _load_step_type(flow, str(st.get("step", "")))
            if st.get("status") != "running" or step_type != "decision":
                self._send(f"当前不在拍板点（step={st.get('step')}, type={step_type or '非decision'}），拍板已忽略", code=400)
                return
            # 写决策文件（引擎心跳下轮读取推进）
            dfp = INSTANCES_DIR / f"{flow}.decision"
            try:
                INSTANCES_DIR.mkdir(parents=True, exist_ok=True)
                dfp.write_text(decision, encoding="utf-8")
            except Exception as e:
                self._send(f"写拍板失败: {e}", code=500)
                return
            # 立即触发一次心跳（不必等 cron）
            try:
                subprocess.run([sys.executable, str(ENGINE_PY), "--flow", flow],
                               capture_output=True, text=True, timeout=120)
            except Exception:
                pass
            self._send(f"✅ 已拍板「{decision}」→ 引擎已推进")
            return
        self._send(_html())

    def log_message(self, *a):
        pass  # 静默访问日志


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8650)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"拍板面板 http://localhost:{args.port}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())

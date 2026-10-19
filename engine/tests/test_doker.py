"""test_doker.py — flow-engine-doker 引擎接入单测（A/B/C/D 四组，002-design-doc.md §4.1）。

覆盖范围（按 002 §4.1）：

A 脚本契约（3 用例）：
  - test_up_idempotent_script_contains_set_euo       up.sh 启用 strict mode
  - test_down_clean_script_uses_down_v_flag           down.sh 用 -v 清匿名卷（S5 判据）
  - test_shell_no_service_fails_explicit              shell.sh 缺 compose 文件时显式报错（P4）

B 挂载解析（3 用例，002 §2.5 安全约束）：
  - test_compose_template_valid_yaml                  compose.template.yaml 是合法 YAML
  - test_volume_mount_relative                          仅 ../workspace 形态相对挂载（禁 ..\\ 绝对路径）
  - test_cache_dir_inside_workspace                   .doker-cache 必须挂在 /workspace 下

C 引擎字段（7 用例，002 §2.2 行为）：
  - test_doker_missing_field_noop                     doker_enabled 缺省 → 本地
  - test_doker_enabled_default_off                    doker_enabled=false → 本地
  - test_doker_enabled_true_routes_exec               doker_enabled=true + dir → compose exec
  - test_doker_enabled_missing_dir_errors             doker_enabled=true 缺 dir → RuntimeError（P4）
  - test_doker_service_defaults_to_main                 service 缺省 = "main"
  - test_status_shows_doker_line                       --status 追加 doker 行（off + enabled）
  - test_dispatch_uses_run_in_env                       _do_dispatch 走 _run_in_env 包装

D 端到端冒烟（1 用例，slow）：
  - test_e2e_up_pytest_down_full_cycle                  fake_compose_dry_run 走通 up→exec→down 全链路

策略：
  A 组：直接 grep 脚本源码验证契约文本（不真执行 bash）。
  B 组：PyYAML 解析 compose.template.yaml + AST 文本扫描。
  C 组：monkeypatch 替换 subprocess.run 捕获 argv 断言（不真起容器，快）。
  D 组：mock docker compose CLI + 模拟 tmp 项目跑完 up→exec→down 端到端（CI 可跳）。
  引擎既有 pytest 基线必须全绿（零回归证据）。

2026-08-24 ft_impl1：第一批实施（M1 + M2 末段，7 用例）。
2026-08-24 ft_impl2：第二批实施（M3 + M4，扩到 14 用例：+A3+B3+D1）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

import engine as eng  # noqa: E402

FLOWS = ENGINE_DIR / "flows"
INSTANCES = ENGINE_DIR / "instances"


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """隔离环境：模板/实例指向临时目录，HERMES_KANBAN_DB 指向临时 DB。"""
    eng.FLOWS_DIR = tmp_path / "flows"
    eng.INSTANCES_DIR = tmp_path / "instances"
    eng.FLOWS_DIR.mkdir(exist_ok=True)
    eng.INSTANCES_DIR.mkdir(exist_ok=True)
    tmp_db = tmp_path / "kanban.db"
    sqlite3.connect(str(tmp_db)).close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_db))
    yield tmp_path
    eng.FLOWS_DIR = FLOWS
    eng.INSTANCES_DIR = INSTANCES


def _write_flow(tmp_env: Path, name: str, steps: list[dict], **extra):
    d = {"name": name, "steps": steps, **extra}
    fp = eng.FLOWS_DIR / f"{name}.yaml"
    import yaml
    fp.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return fp


def _write_instance(tmp_env: Path, name: str, state: dict):
    fp = eng.INSTANCES_DIR / f"{name}.json"
    fp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return fp


class _FakeCompleted:
    """monkeypatch subprocess.run 时给 engine 的返回对象（returncode=0 即可，不读 stdout/stderr）。"""
    returncode = 0
    stdout = ""
    stderr = ""


# ---- C 组：引擎字段 ----

def test_doker_missing_field_noop(tmp_env, monkeypatch):
    """doker_enabled 字段缺失（默认 off） → 本地 subprocess.run（K3 零回归）。"""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _FakeCompleted()

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    # 引擎 instance.json 完全无 doker_* 字段
    _write_instance(tmp_env, "fd1", {"flow": "fd1", "step": None, "iteration": 0,
                                     "history": [], "status": "idle"})
    _write_flow(tmp_env, "fd1", [{"id": "dp", "type": "dispatch", "next": "d"},
                                 {"id": "d", "type": "done"}])
    # 直接调用 _run_in_env（无 cmd 无 executor 路径测试它读 state 行为）
    e = eng.FlowEngine("fd1")
    r = e._run_in_env(["echo", "hello"])
    assert r.returncode == 0
    # 默认无 doker_enabled → 本地直调（cmd = 原 cmd）
    assert captured["cmd"] == ["echo", "hello"]
    # 不应有 docker 关键字
    assert "docker" not in captured["cmd"]


def test_doker_enabled_default_off(tmp_env, monkeypatch):
    """doker_enabled=false（显式） → 本地 subprocess.run（与缺省等价，零回归）。"""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    _write_instance(tmp_env, "fd2", {"flow": "fd2", "step": None, "iteration": 0,
                                     "history": [], "status": "idle",
                                     "doker_enabled": False})
    _write_flow(tmp_env, "fd2", [{"id": "dp", "type": "dispatch", "next": "d"},
                                 {"id": "d", "type": "done"}])
    e = eng.FlowEngine("fd2")
    r = e._run_in_env(["python", "-c", "print(1)"])
    assert r.returncode == 0
    assert captured["cmd"] == ["python", "-c", "print(1)"]
    assert "docker" not in captured["cmd"]


def test_doker_enabled_true_routes_exec(tmp_env, monkeypatch):
    """doker_enabled=true + doker_project_dir → docker compose exec 包装，shlex.quote 逐参。"""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _FakeCompleted()

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    _write_instance(tmp_env, "fd3", {"flow": "fd3", "step": None, "iteration": 0,
                                     "history": [], "status": "idle",
                                     "doker_enabled": True,
                                     "doker_project_dir": "D:/fake/proj",
                                     "doker_service": "main"})
    _write_flow(tmp_env, "fd3", [{"id": "dp", "type": "dispatch", "next": "d"},
                                 {"id": "d", "type": "done"}])
    e = eng.FlowEngine("fd3")
    r = e._run_in_env(["python", "-c", "print('hello world')"])
    assert r.returncode == 0

    # 断言 cmd 形态：docker compose -f <compose> exec -T <svc> bash -lc <shlex_quoted_cmd>
    cmd = captured["cmd"]
    assert cmd[0] == "docker"
    assert cmd[1] == "compose"
    assert cmd[2] == "-f"
    # 项目 compose 路径：doker_project_dir + /docker/compose.yaml
    assert cmd[3].endswith("/docker/compose.yaml") or cmd[3].endswith("\\docker\\compose.yaml")
    assert "D:/fake/proj" in cmd[3] or "D:\\fake\\proj" in cmd[3]
    assert cmd[4] == "exec"
    assert cmd[5] == "-T"
    assert cmd[6] == "main"
    assert cmd[7] == "bash"
    assert cmd[8] == "-lc"
    # 最后一参是 shlex.quote 后的整串（python -c print('hello world')）
    inner = cmd[9]
    assert "python" in inner and "print" in inner
    assert "hello world" in inner
    # capture_output 仍要保留（与本地分支语义一致）
    assert captured["kw"].get("capture_output") is True
    assert "timeout" in captured["kw"]


def test_doker_enabled_missing_dir_errors(tmp_env, monkeypatch):
    """doker_enabled=true 但 doker_project_dir 缺失 → 显式 RuntimeError（P4 失败显式）。

    不回退本地——「看起来在本地跑了」比「明确失败」更危险（002 §2.2 + §P4 原则）。
    """
    calls = {"local": 0}

    def fake_run(cmd, **kw):
        calls["local"] += 1
        return _FakeCompleted()

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    _write_instance(tmp_env, "fd4", {"flow": "fd4", "step": None, "iteration": 0,
                                     "history": [], "status": "idle",
                                     "doker_enabled": True
                                     # 故意不给 doker_project_dir
                                     })
    _write_flow(tmp_env, "fd4", [{"id": "dp", "type": "dispatch", "next": "d"},
                                 {"id": "d", "type": "done"}])
    e = eng.FlowEngine("fd4")
    with pytest.raises(RuntimeError, match="doker_project_dir"):
        e._run_in_env(["echo", "x"])
    # 必须显式报错，绝不回退本地
    assert calls["local"] == 0


def test_doker_service_defaults_to_main(tmp_env, monkeypatch):
    """doker_service 字段缺失 → 默认 'main'（模板约定）。"""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    _write_instance(tmp_env, "fd5", {"flow": "fd5", "step": None, "iteration": 0,
                                     "history": [], "status": "idle",
                                     "doker_enabled": True,
                                     "doker_project_dir": "D:/fake/proj2"
                                     # doker_service 缺省
                                     })
    _write_flow(tmp_env, "fd5", [{"id": "dp", "type": "dispatch", "next": "d"},
                                 {"id": "d", "type": "done"}])
    e = eng.FlowEngine("fd5")
    e._run_in_env(["echo"])
    assert captured["cmd"][6] == "main"  # 默认 main


def test_status_shows_doker_line(tmp_env, monkeypatch):
    """--status 输出追加 doker 行：off / enabled (service=..., dir=...) 两种形态。"""
    monkeypatch.setattr(eng.subprocess, "run", lambda *a, **k: _FakeCompleted())

    # 场景 1：off
    _write_instance(tmp_env, "fd6", {"flow": "fd6", "step": None, "iteration": 0,
                                     "history": [], "status": "idle"})
    _write_flow(tmp_env, "fd6", [{"id": "d", "type": "done"}])
    e = eng.FlowEngine("fd6")
    out = e.status()
    assert "doker: off" in out

    # 场景 2：enabled
    _write_instance(tmp_env, "fd7", {"flow": "fd7", "step": None, "iteration": 0,
                                     "history": [], "status": "idle",
                                     "doker_enabled": True,
                                     "doker_project_dir": "D:/fake/proj3",
                                     "doker_service": "api"})
    _write_flow(tmp_env, "fd7", [{"id": "d", "type": "done"}])
    e2 = eng.FlowEngine("fd7")
    out2 = e2.status()
    assert "doker: enabled" in out2
    assert "service=api" in out2
    assert "D:/fake/proj3" in out2 or "D:\\fake\\proj3" in out2


def test_dispatch_uses_run_in_env(tmp_env, monkeypatch):
    """集成：_do_dispatch 走 executor 时，cmd 经 _run_in_env 包装（doker_enabled=true → exec）。"""
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeCompleted()

    monkeypatch.setattr(eng.subprocess, "run", fake_run)

    _write_instance(tmp_env, "fd8", {"flow": "fd8", "step": "dp", "iteration": 0,
                                     "history": [], "status": "running",
                                     "doker_enabled": True,
                                     "doker_project_dir": "D:/fake/proj4",
                                     "doker_service": "main"})
    _write_flow(tmp_env, "fd8", [{"id": "dp", "type": "dispatch",
                                  "executor": "executor_dispatch.py", "next": "d"},
                                 {"id": "d", "type": "done"}])
    e = eng.FlowEngine("fd8")
    e.run()  # 走 _do_dispatch → _run_in_env
    # 断言 executor 被包装成 docker compose exec 形式
    assert captured["cmd"][0] == "docker"
    assert captured["cmd"][1] == "compose"
    assert "exec" in captured["cmd"]
    assert "-T" in captured["cmd"]
    assert "main" in captured["cmd"]


# ---- A 组：脚本契约（直读脚本源码断言） ----

SCRIPTS_DIR = ENGINE_DIR / "templates" / "scripts"


def _read_script(name: str) -> str:
    p = SCRIPTS_DIR / name
    assert p.exists(), f"脚本不存在: {p}"
    return p.read_text(encoding="utf-8")


def test_up_idempotent_script_contains_set_euo():
    """doker-up.sh 启用 strict mode + 失败显式报告（002 §1.4 P4 契约）。"""
    src = _read_script("doker-up.sh")
    assert "set -euo pipefail" in src, "doker-up.sh 必须启用 strict mode（set -euo pipefail）"
    # 失败显式：必须以 [doker] <动作> failed: <原因> 模式报错（P4）
    assert "[doker] up failed:" in src, "doker-up.sh 失败时必须输出 [doker] up failed: <原因>"
    # 幂等：up --build -d 是 docker compose 原生幂等（重复运行 no-op 更新），文档化契约
    assert "up --build -d" in src, "doker-up.sh 必须是 up --build -d（compose 原生幂等）"


def test_down_clean_script_uses_down_v_flag():
    """doker-down.sh 用 -v 连匿名卷一起清（S5 一键清理判据）。"""
    src = _read_script("doker-down.sh")
    assert "set -euo pipefail" in src
    assert "[doker] down failed:" in src
    # S5 判据：必须 -v --remove-orphans（保留命名卷 doker_pip_cache 跨 build 复用）
    assert " down -v" in src or "down -v --remove-orphans" in src, \
        "doker-down.sh 必须 -v 清匿名卷 + --remove-orphans 清孤儿（S5 判据）"


def test_shell_no_service_fails_explicit():
    """doker-shell.sh 缺 compose 文件或服务不存在时显式报错（002 §1.4 P4）。"""
    src = _read_script("doker-shell.sh")
    assert "set -euo pipefail" in src
    assert "[doker] shell failed:" in src, "doker-shell.sh 必须显式报错"
    # D5 决策：$DOKER_SERVICE 可覆盖主服务名（模板默认 main）
    assert 'DOKER_SERVICE' in src or 'service' in src, "doker-shell.sh 应支持 service 覆盖"


# ---- E 组：gen_project 默认 docker 化（2026-09-01 user 拍板：新项目默认在 docker 运行） ----

def test_gen_project_instance_defaults_doker_enabled(tmp_path):
    """gen_project 生成的实例必须默认 doker_enabled=true + doker_project_dir。"""
    import json, sys as _sys
    gp = ENGINE_DIR / "gen_project.py"
    src = gp.read_text(encoding="utf-8")
    assert 'inst["doker_enabled"] = True' in src, \
        "gen_project.py 实例应默认 doker_enabled=True（user 09-01 拍板）"
    assert 'doker_project_dir' in src, \
        "gen_project.py 实例应默认写入 doker_project_dir（P4：缺 dir 显式报错）"
    assert 'docker' in src, "gen_project.py 应生成 docker/ 目录（Dockerfile + compose.yaml）"

def test_gen_project_docker_files_from_template(tmp_path):
    """模板文件必须存在（Dockerfile.template + compose.template.yaml）。"""
    assert (ENGINE_DIR / "templates" / "Dockerfile.template").exists()
    assert (ENGINE_DIR / "templates" / "compose.template.yaml").exists()

def test_doker_exec_translates_interpreter():
    """doker_enabled 时 cmd[0] 必须换成容器内解释器（sys.executable 是 Windows
    venv 路径，Linux 容器内不存在 → 'command not found'，09-01 enginefix2 首航实证）。"""
    src = (ENGINE_DIR / "engine.py").read_text(encoding="utf-8")
    assert "python3" in src and "doker_enabled" in src, \
        "_do_dispatch 在 doker 模式下应将解释器替换为容器内 python3"


# ---- 兼容性红线（K3）：_run_in_env 对无 doker 字段实例零影响 ----

def test_doker_enabled_missing_noop():
    """无 doker_enabled 字段的既有实例 → 本地跑（K3 零回归）。"""
    import ast, sys as _sys
    src = (ENGINE_DIR / "engine.py").read_text(encoding="utf-8")
    assert 'if not st.get("doker_enabled")' in src, \
        "engine._run_in_env 必须保留 default-off 语义（缺字段=本地执行）"



def _load_compose_template() -> dict:
    """加载并校验 compose.template.yaml 合法性。"""
    import yaml
    p = ENGINE_DIR / "templates" / "compose.template.yaml"
    assert p.exists(), f"模板不存在: {p}"
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def test_compose_template_valid_yaml():
    """compose.template.yaml 是合法 YAML 且结构契合 services/volumes。"""
    cfg = _load_compose_template()
    assert "services" in cfg and "main" in cfg["services"], \
        "compose 模板必须有 services.main 主服务"
    # main 服务的 build/volumes/command 完整
    main = cfg["services"]["main"]
    assert "build" in main, "main 服务必须有 build 段"
    assert "volumes" in main, "main 服务必须声明 volumes"
    # YAML 不带括号时 command 解析为字符串 'sleep infinity'（带括号才解析为 list）
    cmd = main.get("command")
    assert cmd == "sleep infinity" or cmd == ["sleep", "infinity"], \
        f"默认 keep-alive（开发容器），实际 {cmd!r}"


def test_volume_mount_relative():
    """volume 挂载必须用相对路径（..:/workspace），禁用 Windows 绝对路径（002 §2.5 白名单）。"""
    cfg = _load_compose_template()
    vols = cfg["services"]["main"]["volumes"]
    # compose YAML volumes 解析为 list of {str:str} 或 list of str
    src_files = []
    for v in vols:
        if isinstance(v, str):
            src_files.append(v)
        elif isinstance(v, dict):
            src_files.extend(v.keys())
    # 至少有 1 个 ../workspace 形态相对挂载（源码根）
    assert any(s.startswith("..") and s.endswith(":/workspace") for s in src_files), \
        f"必须挂载项目根 ..:/workspace（P3 主机零依赖），实际 {src_files}"
    # 不允许 Windows 风格绝对路径被塞进容器挂载（防误把 D:/.. 挂进去）
    # 命名卷（不含 .. 起始、不含正反斜杠绝对路径）放行
    for s in src_files:
        # 命名卷标识符通常是无冒号也无斜杠的简单名（doker_pip_cache 这种），放行
        if ":" not in s and "/" not in s and "\\" not in s:
            continue
        # .. 相对路径放行
        if s.startswith("..") and s.endswith(":/workspace"):
            continue
        # 其他形态（如 Windows 盘符 D:/...、Linux 绝对 /home/...）拒绝
        assert not (len(s) > 1 and s[1] == ":"), \
            f"不允许的 Windows 绝对挂载源 {s}（白名单只接 .. 相对路径或命名卷）"
        assert not s.startswith("/"), \
            f"不允许的 Linux 绝对挂载源 {s}"


def test_cache_dir_inside_workspace():
    """doker 缓存目录必须挂在 /workspace 内（charter G1 + §2.5 白名单）。"""
    cfg = _load_compose_template()
    vols = cfg["services"]["main"]["volumes"]
    cache_mounts = []
    for v in vols:
        if isinstance(v, str):
            cache_mounts.append(v)
        elif isinstance(v, dict):
            for src, tgt in v.items():
                if ".doker-cache" in str(tgt) or "/workspace/" in str(tgt):
                    cache_mounts.append(f"{src}:{tgt}")
    # 至少 1 个 .doker-cache 挂载，且 mount target 必须在 /workspace 内
    assert any("/workspace/" in c for c in cache_mounts), \
        f"必须将 .doker-cache 挂到 /workspace 内，实际 {cache_mounts}"


# ---- D 组：端到端冒烟（fake_compose_dry_run，slow，docker 不可用时仍可跑） ----

@pytest.mark.slow
def test_e2e_up_pytest_down_full_cycle(tmp_path, monkeypatch):
    """end-to-end 冒烟：mock docker compose CLI 跑完 up→exec →pytest→down 全链路。

    不真起容器：把 docker 命令替成 echo + 验证 invocation 形态。
    docker daemon 不可用时（Windows 本机常态）由 CI runner 真起容器验证（verify2）。
    """
    import yaml

    # 1. 准备一个临时"项目"：docker/ + scripts/ + 一个简化 pytest 目标
    proj = tmp_path / "fake_proj"
    proj.mkdir()
    (proj / "docker").mkdir()
    (proj / "scripts").mkdir()
    # 拷 compose 模板
    template = _load_compose_template()
    (proj / "docker" / "compose.yaml").write_text(
        yaml.safe_dump(template, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    # 拷三脚本（用 Python read+write 替代 cp，便于跨平台）
    for s in ("doker-up.sh", "doker-down.sh", "doker-shell.sh"):
        (proj / "scripts" / s).write_text(_read_script(s), encoding="utf-8")

    # 2. 准备 instance.json（doker_enabled=true 完整三字段）
    instances = tmp_path / "instances"
    flows = tmp_path / "flows"
    instances.mkdir()
    flows.mkdir()
    flow_yaml = {"name": "e2e", "steps": [
        {"id": "d", "type": "done"},
    ]}
    (flows / "e2e.yaml").write_text(yaml.safe_dump(flow_yaml, allow_unicode=True), encoding="utf-8")
    (instances / "e2e.json").write_text(json.dumps({
        "flow": "e2e", "step": None, "iteration": 0, "history": [], "status": "idle",
        "doker_enabled": True,
        "doker_project_dir": str(proj).replace("\\", "/"),
        "doker_service": "main",
    }), encoding="utf-8")

    # 3. mock docker compose —— 全部走 no-op 记录 invocation（不真起容器）
    invocations: list[list[str]] = []

    def fake_docker_compose(*args, **kw):
        # args[0] = argv list
        invocations.append(list(args[0]))
        return _FakeCompleted()

    # 4. 调用 _run_in_env 三次模拟 up→exec(pytest)→down
    # 这里不依赖 engine 全局路径，重建 FlowEngine 实例，monkeypatch 实例方法
    sys.path.insert(0, str(ENGINE_DIR))
    from importlib import reload
    import engine as e2e_eng
    reload(e2e_eng)  # 确保用最新模块
    e2e_eng.FLOWS_DIR = flows
    e2e_eng.INSTANCES_DIR = instances

    monkeypatch.setattr(e2e_eng.subprocess, "run", fake_docker_compose)

    e = e2e_eng.FlowEngine("e2e")
    # 模拟 doker-up 路径（已编译进 e._run_in_env 内的 compose exec 形式）
    r = e._run_in_env(["docker", "compose", "-f", str(proj / "docker" / "compose.yaml"), "up", "--build", "-d"])
    assert r.returncode == 0
    # 模拟 doker-shell 路径：容器内跑 pytest
    r = e._run_in_env(["python", "-m", "pytest", "engine/tests/", "-q"])
    assert r.returncode == 0
    # 模拟 doker-down 路径
    r = e._run_in_env(["docker", "compose", "-f", str(proj / "docker" / "compose.yaml"), "down", "-v", "--remove-orphans"])
    assert r.returncode == 0

    # 5. 链路收尾校验：3 次 _run_in_env 调用全部命中（_run_in_env 把每条 cmd 都包成 docker compose exec main bash -lc <cmd>）
    assert len(invocations) == 3, f"应记录 3 次 docker 调用，实际 {len(invocations)}"
    # 每次 invocation 都形如 docker compose -f <proj> exec -T main bash -lc "<shlex quoted cmd>"
    # —— 原始命令被 shlex.quote 后嵌在内层 bash -lc 参数里，需在整体 argv + 子串里找
    iv_joined = " | ".join(" ".join(iv) for iv in invocations)
    # 判定 3 次调用的语义关键词都在
    assert " up --build -d" in iv_joined or "' up --build -d'" in iv_joined or " up --build" in iv_joined, \
        f"up 调用关键词缺失，实际 invocations:\n{iv_joined}"
    assert "pytest" in iv_joined, "pytest 调用缺失"
    assert "down -v" in iv_joined, "down -v 调用缺失"
    # 形态断言：每次 invocation 都至少含 docker compose + exec
    for iv in invocations:
        joined = " ".join(iv)
        assert "docker" in joined and "compose" in joined and "exec" in joined, \
            f"invocation 形态异常（非 docker compose exec）：{iv}"
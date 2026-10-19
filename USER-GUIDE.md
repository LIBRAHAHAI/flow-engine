# USER-GUIDE — Flow Engine 用户指南（你视角）

> 版本：v2.1（2026-08-24 新增 §七 doker 容器化运行） | 配套：charter 001 + 路线图 002
> 锚点：002-design-doc.md（flow-engine-doker 设计 v1.0.1）§3
>
> v2.0 变更（2026-08-23）：①「流程」统一改名「项目」（新建项目/项目总览/管理项目）；
> ② 项目分父子层级，支持「改进项目」在已有项目下建子项目；③ 新增流程模板库
> （立项后选执行流程：bugfix / feature / research / 标准）。
>
> v2.1 变更（2026-08-24）：新增 §七「doker 容器化运行」——你视角的需求/用法/排障两行/已开启项目。

## 一、这是什么

**Flow Engine = 流程驱动引擎**：把项目的生命周期流程（立项→评审→拍板→实施→验收→收官）变成**代码状态机自动推进**。你只需要在拍板点按按钮，其余全部由引擎驱使 agent 干活。

**核心承诺**：新项目从"开引擎"到收官，你只需 2-3 次拍板。

## 二、怎么用（三步）

### 1. 开引擎（你的一句话 或 面板按钮）

```
对 hermes 说：开引擎 <项目名>
或面板左栏点「+ 新建项目」
```

引擎自动：
- 建项目 exchange 目录 + charter 四件套模板
- 注册项目实例（flows/<项目>.yaml）
- 挂心跳 cron（每 5 分钟自动推进）

### 1b. 改进现有项目（v2.0 新增）

面板左栏「改进项目…」→ 选择要改进的父项目 → 输入子项目名 → 创建。
子项目实例带 `parent` 字段，「管理项目」页按上下级树形展示。
例：flow-engine 下已有 4 个改进子项目（frontend / frontend-v2 /
mechanism-fix-engine-verify-wait / grillme-deep）。

### 2. 拍板（你只需做这个）

**拍板点到达时，引擎推送给你**（两种方式任选）：
- **拍板面板**（推荐）：预览窗格打开 `http://localhost:8650`，看项目状态卡片 + 点按钮
- **会话回复**：直接说"同意" / "A" / "驳回"

**你只有两类拍板**：
| 拍板点 | 内容 |
|---|---|
| 立项 | 项目方案是否接受 |
| 收官 | 最终成果是否可交付 |

### 2b. 选执行流程模板（v2.0 新增，立项后做）

面板左栏「流程模板」页 → 选一个模板 → 「绑定到项目…」。
之后该项目的任务分派、监察、验收均按所选模板推进：

| 模板 | 适用场景 |
|---|---|
| 新项目全流程（标准） | 从零起步的新项目 |
| 缺陷修复流程 | 已知缺陷定点修复（复现→根因→修复→回归） |
| 功能迭代流程 | 在已有产品上叠新功能（设计→两批实施→集成验收） |
| 调研验证流程 | 技术选型/竞品分析/可行性验证 |

绑定即生效、可覆盖；也可对任意项目说「开引擎 <名>」后由 hermes 代绑
（POST /api/flows/<id>/template）。

### 3. 看进度（随时）

- **拍板面板**：所有项目状态卡片（当前步骤/历史/待拍板项）
- 引擎自动投递关键节点（【引擎心跳】消息）

## 三、引擎会自动做什么

```
你拍立项 → 引擎：
  起草 charter → 双审(玄武技术审+端木运营审) → 等你拍板
  → 实施派发(建卡→dispatcher spawn→agent 干活)
  → 验收(代码检查，不合格自动打回重做)
  → 下一轮 → …… → 收官等你拍板 → 完成
```

## 四、规则

| 项 | 规则 |
|---|---|
| 拍板 | 只有 2 类（立项/收官），引擎推送到你面前 |
| 验收 | 代码检查（非 agent 自报），不合格自动打回 |
| 状态 | instances/<flow>.json 唯一事实源 |
| 层级 | 实例 parent 字段 = 父项目 id；树形展示在管理项目页 |
| 回滚 | ROLLBACK.md R1-R4 预案，随时可停 |
| 模板 | flows/template-new-project.yaml（标准）；032/flow-engine-build 为已跑通实例；template-bugfix/feature/research 为 v2.0 流程模板库 |

## 五、排障（很少需要）

|| 现象 | 处理 |
|---|---|---|
| 引擎不动 | 看面板卡片是否在 wait（等产物）；心跳 cron 是否在跑（cron list） |
| 拍板按钮没出现 | 当前不在拍板点（显示当前步骤，非 decision） |
| 想停某个项目 | 编辑 instances/<flow>.json status=paused；或问 hermes |
| agent 不响应 | 查该 agent 的 Token Plan 配额（曾遇玄武 429） |
| 改了后端代码不生效 | 杀掉 8650 旧进程重启（改共享代码必须重启服务进程） |

## 六、当前实例

| 项目 | 状态 |
|---|---|
| 032-mem0-hmem | ✅ completed（mem0 退役 + HMEM 启用 + 1682 条记忆迁移） |
| flow-engine-build | 🔄 M5 完成后收官（本引擎自举） |
| flow-engine（含 4 个改进子项目） | 🔄 子项目详见管理项目页 |

## 七、doker 容器化运行（v2.1 新增）

> 锚点：002-design-doc.md §3（M3 文档）+ charter §一 G1「一次构建环境复现」+ D6 文档先行

### 7.1 这是什么 & 什么时候用

**doker** = 让一个项目的 agent 干活跑在 Docker 容器里，而不是你主机的 Python 环境。
什么时候有用：

- **换机零迁移**：新机器装好 Docker + clone 项目 → 直接 `doker-up.sh` → 立刻跑项目测试，不用逐个装依赖
- **环境干净隔离**：容器内是干净 Ubuntu（系统 Python + Node + git/curl），不污染你主机的 site-packages
- **可复现**：Charter G1——同样的 `docker/`，任何人机器上跑结果一致（不是「在我电脑能跑」）

什么时候**不需要**：临时调试、demo 用、依赖非常定制——容器化反而拖慢节奏。doker 字段缺省时引擎一切照旧（§2 K3 零回归）。

### 7.2 三脚本用法

任意项目只要自带 `<项目>/docker/compose.yaml` + `<项目>/scripts/doker-*.sh`（gen_project 开项目时由模板复制），你就可以从项目根或者任意 cwd 调三个脚本：

```bash
# 1. 启动项目容器（构建镜像 + 后台跑 main 服务；幂等，重复调用是 no-op 刷新）
./scripts/doker-up.sh

# 2. 进容器内交互 shell（开发/调试用，有 TTY）
./scripts/doker-shell.sh

# 3. 一键清理：容器 + 命名卷 + 孤儿（不留垃圾）
./scripts/doker-down.sh
```

调用顺序：在容器里**跑测试**或**日常开发**的典型链路是 `doker-up.sh` → （doker-shell.sh 进容器手操） → `doker-down.sh`。
引擎已经在该项目的 instance json 里写 `doker_enabled=true` 后，agent 心跳执行器链会自动用 `docker compose exec` 跑容器内的命令——你不用手动管。

### 7.3 已开启 doker 的项目清单

| 项目 | 用途 | 启用时机 |
|---|---|---|
| flow-engine-grillme-deep | M1 试水项目（S1-S3 证据） | ft_impl1 阶段 1.5 |
| flow-engine（doker 自身 dogfood） | M3 S6：本项目自身用容器跑测试 | ft_impl2 阶段 4b |

后续每个新项目都按 §四 D4「渐进式」原则手动开启，不会一刀切全量开。

### 7.4 排障两行

| 现象 | 一眼看 | 一行解 |
|---|---|---|
| doker-up 卡住 / 报 `[doker] up failed` | `set -euo pipefail` 已显式失败原因到 stderr | 90% 是 Docker Desktop 未启动（Windows）/ 端口冲突 / compose.yaml 语法错。`docker ps -a` + `docker compose -f docker/compose.yaml logs` 看具体行 |
| 启用 doker 后 agent 心跳日发生成 `[doker] ... failed` | engine.py 选容器化执行但 instance.json `doker_project_dir` 路径错（Windows 路径转 compose 不通） | 编辑该实例 json 确认 `doker_project_dir` 是 Windows 绝对路径，最稳是把项目根放在固定数据盘 |

charter §十 R1-R4 是 doker 的总开关预案：关掉一个项目的 doker 等价改回旧引擎路径（零残留），详见 `flow-engine-doker/exchange/ROLLBACK.md`。

—— 本节对应：002-design-doc.md §3 M3 §七 | athena 设计 + hermes v2.1 实施，2026-08-24

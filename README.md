# Flow Engine — 流程驱动的多智能体项目引擎

**把项目生命周期的推进逻辑交给代码状态机，让 AI agent 只负责干活。**

Flow Engine 把一个项目的完整生命周期（立项 → charter → 实施 → 验收 → 拍板 → 收官）编码成 YAML 流程模板，由引擎心跳自动推进：

- **派发即干**：引擎按流程模板直接向执行者派任务（dispatch），不靠 agent"自觉"响应
- **验收客观**：verify 步骤由代码检查交付物（产物存在 + frontmatter 格式双重校验 + 状态达标），不信 agent 自报
- **不合格自动打回**：验收 rejected → on_fail 全链回退 → 重做 → 复验，机械执行，最大迭代数防死循环
- **拍板到人**：decision 步骤暂停并推送给你，一句话拍板，回复即续跑
- **单一事实源**：每个流程一个持久化实例文件（`engine/instances/<flow>.json`），幂等可重启
- **自举**：引擎的建设与修复本身也由引擎驱动（本项目 12 项机制缺陷全部经"以引擎修引擎"闭环根治）

## 它解决什么问题

多智能体协作最常见的失败模式：任务在角色之间接力时断链、交付物撞号、验收靠干活的自己写"我完成了"。根因是**把确定性流程外包给了大模型的自觉**。Flow Engine 的答案：裁判权收回给代码，模型降级回纯执行者。

## 仓库结构

```
engine/     引擎核心：状态机 / 派发执行器 / 深度需求访谈 / 项目生成器 / 流程校验
  flows/          内置流程模板：bugfix / feature / research / design / new-project / roadmap
  config/         派工表 dispatch_profiles.yaml（谁执行哪步、步骤目标、技能挂载）
  templates/      项目脚手架模板（AGENTS.md / Dockerfile / compose）
  tests/          pytest 测试基线
panel/      拍板面板（React 前端 + FastAPI 后端）：流程状态卡片 + 一键拍板 + 需求素材包
docker/     容器化运行（Dockerfile / compose，可选）
scripts/    心跳 wrapper（自动发现运行中实例并推进）+ doker 启停脚本
USER-GUIDE.md  用户指南（含排障速查）
```

## 快速开始

依赖：Python 3.11+，一个能被你调度的 agent 执行环境（本项目生于 Hermes Agent 生态，但状态机与模板语言是通用的——任何能被脚本拉起的 CLI agent 都可以作为执行者）。

```bash
# 1. 克隆
git clone https://github.com/LIBRAHAHAI/flow-engine.git
cd flow-engine

# 2. 跑测试基线，确认引擎健康
python -m pytest engine/tests/ -q

# 3. 开第一个项目（生成项目骨架 + 注册流程实例）
python engine/gen_project.py my-first-project

# 4. 心跳推进（每跑一次推进一步；有决策时会在输出里提示拍板）
python engine/engine.py --flow my-first-project

# 可选：图形拍板面板
cd panel/backend && python start_backend.py
# 可选：容器化运行
bash scripts/doker-up.sh
```

日常使用通常配一个定时心跳（cron 每 5 分钟跑 `scripts/engine_heartbeat_wrapper.py`），引擎即可全程自动推进，人只在拍板点出现。

## 流程模板怎么写

一个模板 = 一串步骤，六种类型：

| 类型 | 作用 |
|---|---|
| `dispatch` | 派任务给执行者（assignee + 步骤目标） |
| `wait` | 屏障：等上游卡完成（防背靠背过早派发） |
| `verify` | 代码级验收：产物存在 + frontmatter 状态达标，不达标自动打回 |
| `decision` | 拍板点：暂停等人的 A/B 选择 |
| `discuss` | 交互式深度访谈（grill-me 需求树） |
| `done` | 终态 |

参考 `engine/flows/template-bugfix.yaml`（缺陷修复全流程：拍板 → charter → 双审 → TDD 复现 → 修复 → 回归 → 二次验收 → 收官）。

## 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FLOW_PROJECTS_ROOT` | `.` | 项目根目录 |
| `FLOW_ENGINE_DIR` | 脚本所在目录 | 引擎目录（instances/flows 所在处） |
| `FLOW_ENGINE_EXWRITE` | `exwrite.py` | 交换协议写侧取号脚本路径（可选） |
| `HERMES_HOME` | `~/AppData/Local/hermes` | 执行者 profile 配置根 |
| `FLOW_KANBAN_DB` | 平台默认 | 看板 SQLite 路径 |

## 设计原则

1. **确定性的事不外包给大模型**：流程推进、编号、验收、状态记录全部代码化
2. **缺陷是跑出来的**：尽快造一个低成本的"第一用户"项目把引擎跑起来，再集中修复
3. **修复要有根因锚定表**：每个缺陷钉到文件/函数/行号 + 可量化验收标准
4. **打回是质量成本，不是故障**：rejected 是流程的一等公民
5. **验收与实施分离**：verify 步骤的执行者不应是实施者自己

## License

见 [LICENSE](LICENSE)。

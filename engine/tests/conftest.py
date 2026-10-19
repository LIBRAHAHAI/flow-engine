"""conftest.py — pytest 全局配置（flow-engine-doker 第二批加）。

注册自定义 marker：
  - slow: 端到端冒烟，跑时间较长，CI 默认可跳。本地跑全部用例，或 -m slow 单独跳。

2026-08-30 grillme-conversation (grc) 增补：
  - INTERVIEW_HERMES_CONFIG 自动注入：指向 tests/_testdata/hermes.config.yaml
    （providers.custom 齐全的假配置）。修复 direct 路径单测隐式依赖宿主
    config.yaml 的缺陷（NA-266 同款病：宿主 providers.custom 08-28 AFS
    清场后被清空 → _llm_config() 拿到空值 → generate_followup 提前返回
    api_error → 4 个 FakeClient 单测到不了 mock 分支）。
    入口在 interview.py HERMES_CONFIG（L125），测试需覆盖时用
    monkeypatch.setenv("INTERVIEW_HERMES_CONFIG", ...) 晚于本 autouse
    fixture 执行，天然生效。
"""
from __future__ import annotations

import os

from pathlib import Path

import pytest

# 生命周期耦合（2026-10-10）：engine._save_state / gen_project 的 reconcile 挂钩
# 在回归中一律静默跳过——pytest 绝不触达真实 cron job（不得 pause 真实心跳）。
os.environ.setdefault("CRON_COUPLING_DISABLE", "1")

_TESTDATA_CONFIG = Path(__file__).resolve().parent / "_testdata" / "hermes.config.yaml"


@pytest.fixture(autouse=True)
def _interview_test_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有用例默认拿到确定性的假 hermes 配置（隔离宿主 config.yaml）。"""
    monkeypatch.setenv("INTERVIEW_HERMES_CONFIG", str(_TESTDATA_CONFIG))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "slow: 端到端冒烟类用例（跑时间较长或需外部资源），CI 默认可跳，本机用 -m 'not slow' 跳过"
    )

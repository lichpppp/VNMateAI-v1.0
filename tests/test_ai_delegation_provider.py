"""
tests/test_ai_delegation_provider.py
====================================
Ủy quyền chuyên gia (core.skills.ai_delegation) dùng provider LLM chung từ Phase 5:
thử lần lượt specialist_models, nhớ model hỏng, timeout dài cho phân tích sâu.
Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.llm_provider as lp  # noqa: E402
import core.skills.ai_delegation as deleg  # noqa: E402


@pytest.fixture
def fake(monkeypatch):
    calls = []
    state = {"bad": set()}

    class FakeAsyncOpenAI:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kw):
            calls.append(kw)
            if kw["model"] in state["bad"]:
                raise RuntimeError("down")
            msg = SimpleNamespace(content=f"phân tích từ {kw['model']}")
            return SimpleNamespace(model=kw["model"], choices=[SimpleNamespace(message=msg)])

    monkeypatch.setattr(deleg, "AsyncOpenAI", FakeAsyncOpenAI)
    monkeypatch.setattr(deleg, "_get_llm_config", lambda: {
        "base_url": "http://x/v1", "api_key": "k",
        "specialist_model": "spec-a", "specialist_models": ["spec-a", "spec-b"],
    })
    monkeypatch.setattr(deleg, "trigger_delegation_reflex", lambda: None)
    lp._model_down_until.clear()
    yield calls, state
    lp._model_down_until.clear()


async def test_falls_back_and_uses_long_timeout(fake, monkeypatch):
    calls, state = fake
    state["bad"] = {"spec-a"}
    seen_timeouts = []
    real_wait_for = lp.asyncio.wait_for

    async def spy_wait_for(aw, timeout):
        seen_timeouts.append(timeout)
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(lp.asyncio, "wait_for", spy_wait_for)
    res = await deleg.delegate_to_specialist_async("chẩn đoán lỗi")
    assert res["status"] == "success"
    assert res["specialist_model"] == "spec-b"
    assert [c["model"] for c in calls] == ["spec-a", "spec-b"]
    assert all(t == 180.0 for t in seen_timeouts)
    assert "extra_body" not in calls[-1]


async def test_all_models_fail_returns_error_not_exception(fake):
    calls, state = fake
    state["bad"] = {"spec-a", "spec-b"}
    res = await deleg.delegate_to_specialist_async("chẩn đoán lỗi")
    assert res["status"] == "error" and res["error"] == "ALL_SPECIALIST_MODELS_FAILED"

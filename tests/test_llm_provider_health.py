"""
tests/test_llm_provider_health.py
=================================
Provider LLM chung (core.llm_provider.NineRouterLLMProvider) nhớ model hỏng.

Trước Phase 5 mỗi lượt thử lại từ đầu danh sách model: đo được 6 model hỏng/chậm
× tới 5 s trước khi gặp model chạy được (chữ đầu tiên sau ~22 s). Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.llm_provider as lp  # noqa: E402


class FakeClient:
    def __init__(self, bad):
        self.bad = set(bad)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw["model"])
        if kw["model"] in self.bad:
            raise RuntimeError(f"{kw['model']} is down")
        if kw.get("stream"):
            async def gen():
                delta = SimpleNamespace(content="ok", tool_calls=None, reasoning=None, reasoning_content=None)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason="stop")])
            return gen()
        return SimpleNamespace(model=kw["model"])


@pytest.fixture(autouse=True)
def clean_health():
    lp._model_down_until.clear()
    yield
    lp._model_down_until.clear()


async def test_failed_model_tried_last_next_time():
    client = FakeClient(bad={"dead-a", "dead-b"})
    p = lp.NineRouterLLMProvider(client, "dead-a", ["dead-b", "good"])
    await p.complete(messages=[])
    assert client.calls == ["dead-a", "dead-b", "good"]
    client.calls.clear()
    await p.complete(messages=[])
    assert client.calls == ["good"], "lượt sau phải đi thẳng tới model đang chạy"


async def test_stream_marks_health_too():
    client = FakeClient(bad={"dead"})
    p = lp.NineRouterLLMProvider(client, "dead", ["good"])
    out = [c.content async for c in p.stream(messages=[])]
    assert out == ["ok"]
    client.calls.clear()
    _ = [c async for c in p.stream(messages=[])]
    assert client.calls == ["good"]
    assert "dead" in lp.model_health()


async def test_placeholder_model_never_called():
    client = FakeClient(bad=set())
    p = lp.NineRouterLLMProvider(client, "YOUR_MODEL_NAME_HERE", ["good"])
    await p.complete(messages=[])
    assert client.calls == ["good"]


async def test_all_cooling_still_tried():
    client = FakeClient(bad={"a", "b"})
    p = lp.NineRouterLLMProvider(client, "a", ["b"])
    with pytest.raises(RuntimeError):
        await p.complete(messages=[])
    client.bad.clear()
    resp = await p.complete(messages=[])  # cả hai đang bị xếp cuối nhưng vẫn được thử
    assert resp.model == "a"
    assert lp.model_health() == {"b": pytest.approx(lp.MODEL_COOLDOWN_S, abs=1)}

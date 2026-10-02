"""
scripts/bench_voice.py
======================
Đo latency đường voice — chạy lặp lại được, dùng làm baseline trước/sau mỗi
bước refactor Voice (docs/migration/production-refactor-plan.md, Phase 1).

Phần A — trong tiến trình (không cần server):
  * fast_path   : FastCommandRouter.dispatch (không tổng hợp audio)
  * sentence    : SentenceBuffer tách câu từ luồng token giả lập
  * tts_first   : TTSStreamEngine.stream -> chunk audio đầu tiên (gọi mạng thật,
                  mỗi câu khác nhau để không trúng cache)

Phần B — đầu-cuối qua WebSocket thật (khi có --ws):
  đăng nhập -> /ws/v1/voice-stream -> gửi lệnh -> đo tới sự kiện đầu tiên,
  text_delta đầu tiên, frame audio đầu tiên, session_ended.

Mọi thời gian đo phía client bằng time.perf_counter(). Số nào không đo được thì
ghi lý do, không điền số.

Ví dụ:
  python scripts/bench_voice.py
  python scripts/bench_voice.py --ws wss://localhost --user admin --password admin123
  python scripts/bench_voice.py --skip-tts --out bench.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import ssl
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAST_COMMANDS = ["mấy giờ rồi", "kiểm tra cpu", "xem ram", "xin chào", "ping"]
LLM_QUERY = "Giải thích ngắn gọn RAID 1 là gì trong hai câu."


def _stats(samples_ms: List[float]) -> Dict[str, Any]:
    if not samples_ms:
        return {"n": 0}
    s = sorted(samples_ms)

    def pct(p: float) -> float:
        k = (len(s) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    return {
        "n": len(s),
        "p50_ms": round(pct(0.50), 3),
        "p95_ms": round(pct(0.95), 3),
        "max_ms": round(s[-1], 3),
        "mean_ms": round(statistics.fmean(s), 3),
    }


# ── Phần A ───────────────────────────────────────────────────────────────

async def bench_fast_path(iterations: int) -> Dict[str, Any]:
    from core.fast_command_router import fast_command_router

    per_cmd: Dict[str, Any] = {}
    all_ms: List[float] = []
    for cmd in FAST_COMMANDS:
        await fast_command_router.dispatch(cmd, synthesize_audio=False)  # làm nóng
        samples = []
        matched = True
        for _ in range(iterations):
            t0 = time.perf_counter()
            res = await fast_command_router.dispatch(cmd, synthesize_audio=False)
            samples.append((time.perf_counter() - t0) * 1000)
            matched = matched and bool(res and res.is_matched)
        per_cmd[cmd] = {**_stats(samples), "matched": matched}
        all_ms.extend(samples)
    return {"overall": _stats(all_ms), "per_command": per_cmd}


def bench_sentence_buffer(iterations: int) -> Dict[str, Any]:
    from mateai.application.voice.sentence_buffer import SentenceBuffer

    text = (
        "Dạ, em đã kiểm tra máy chủ 192.168.1.27 lúc 10.30 sáng. CPU đang ở mức 32,5%. "
        "RAM còn trống 4.2 GB, ổ đĩa C:\\Users\\Admin còn 120 GB. Phiên bản hệ điều hành là v10.0.19045. "
        "Anh có muốn em gửi báo cáo chi tiết qua Telegram không ạ?"
    )
    tokens = [text[i:i + 4] for i in range(0, len(text), 4)]
    samples, sentences = [], 0
    for _ in range(iterations):
        buf = SentenceBuffer(min_chars=8)
        t0 = time.perf_counter()
        out = []
        for tok in tokens:
            out.extend(buf.add_token(tok))
        out.extend(buf.flush())
        samples.append((time.perf_counter() - t0) * 1000)
        sentences = len(out)
    return {"per_stream": _stats(samples), "tokens": len(tokens), "sentences": sentences}


async def bench_tts_first_chunk(rounds: int) -> Dict[str, Any]:
    from mateai.infrastructure.tts.tts_stream_engine import TTSStreamEngine

    engine = TTSStreamEngine()
    samples, errors = [], []
    for i in range(rounds):
        # Câu khác nhau mỗi lần (mã ngẫu nhiên) để không trúng cache RAM/đĩa.
        text = f"Đây là câu đo thử số {i + 1}, mã {uuid.uuid4().hex[:6]}."
        t0 = time.perf_counter()
        first = None
        try:
            async for chunk in engine.stream(text):
                if chunk:
                    first = (time.perf_counter() - t0) * 1000
                    break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if first is None:
            errors.append("không nhận được chunk audio nào")
        else:
            samples.append(first)
    return {"ttfa_engine": _stats(samples), "errors": errors}


# ── Phần B ───────────────────────────────────────────────────────────────

async def _login(base_http: str, user: str, password: str, ctx: Optional[ssl.SSLContext]) -> str:
    import httpx

    async with httpx.AsyncClient(verify=False if ctx else True, timeout=15.0) as client:
        r = await client.post(f"{base_http}/api/v1/login", json={"username": user, "password": password})
        r.raise_for_status()
        data = r.json()
        return data.get("access_token") or data.get("token") or ""


async def _ws_turn(ws_url: str, token: str, query: str, ctx, timeout_s: float) -> Dict[str, Any]:
    import websockets

    out: Dict[str, Any] = {"query": query}
    t_connect = time.perf_counter()
    async with websockets.connect(f"{ws_url}/ws/v1/voice-stream?token={token}", ssl=ctx,
                                  max_size=None, open_timeout=10) as ws:
        out["connect_ms"] = round((time.perf_counter() - t_connect) * 1000, 2)
        # Bỏ qua các gói chào trước khi gửi lệnh.
        try:
            while True:
                await asyncio.wait_for(ws.recv(), timeout=0.5)
        except asyncio.TimeoutError:
            pass

        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "query", "query": query, "request_id": uuid.uuid4().hex[:10]}))
        first_event = first_text = first_audio = None
        server_metrics = None
        audio_bytes = 0
        while True:
            remaining = timeout_s - (time.perf_counter() - t0)
            try:
                if remaining <= 0:
                    raise asyncio.TimeoutError
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                out["error"] = f"quá {timeout_s}s chưa xong lượt"
                break
            now = (time.perf_counter() - t0) * 1000
            if first_event is None:
                first_event = now
            if isinstance(msg, (bytes, bytearray)):
                audio_bytes += len(msg)
                if first_audio is None:
                    first_audio = now
                continue
            evt = json.loads(msg)
            etype = evt.get("type") or evt.get("event_type")
            if etype == "text_delta" and first_text is None:
                first_text = now
            if etype == "error":
                out["error"] = evt.get("message") or evt.get("payload") or evt
                break
            if etype == "session_ended":
                server_metrics = evt.get("metrics") or (evt.get("payload") or {}).get("metrics")
                out["total_ms"] = round(now, 2)
                break
        out.update({
            "first_event_ms": round(first_event, 2) if first_event is not None else None,
            "first_text_ms": round(first_text, 2) if first_text is not None else None,
            "first_audio_ms": round(first_audio, 2) if first_audio is not None else None,
            "audio_bytes": audio_bytes,
            "server_metrics": server_metrics,
        })
    return out


async def bench_ws(ws_base: str, user: str, password: str, rounds: int, timeout_s: float) -> Dict[str, Any]:
    secure = ws_base.startswith("wss://")
    ctx = None
    if secure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # chứng chỉ tự ký của máy dev
    base_http = ("https://" if secure else "http://") + ws_base.split("://", 1)[1]
    try:
        token = await _login(base_http, user, password, ctx)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"đăng nhập thất bại: {exc}"}

    result: Dict[str, Any] = {}
    for label, queries in (("fast_path", FAST_COMMANDS[:2]), ("llm", [LLM_QUERY])):
        turns = []
        for i in range(rounds):
            q = queries[i % len(queries)]
            try:
                turns.append(await _ws_turn(ws_base, token, q, ctx, timeout_s))
            except Exception as exc:  # noqa: BLE001
                turns.append({"query": q, "error": f"{type(exc).__name__}: {exc}"})
        ok = [t for t in turns if "error" not in t]
        result[label] = {
            "first_event": _stats([t["first_event_ms"] for t in ok if t.get("first_event_ms") is not None]),
            "first_text": _stats([t["first_text_ms"] for t in ok if t.get("first_text_ms") is not None]),
            "first_audio": _stats([t["first_audio_ms"] for t in ok if t.get("first_audio_ms") is not None]),
            "total": _stats([t["total_ms"] for t in ok if t.get("total_ms") is not None]),
            "errors": [t["error"] for t in turns if "error" in t],
            "turns": turns,
        }
    return result


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iterations", type=int, default=200, help="số lần đo cho fast path / sentence buffer")
    ap.add_argument("--tts-rounds", type=int, default=3)
    ap.add_argument("--skip-tts", action="store_true", help="bỏ đo TTS (gọi mạng)")
    ap.add_argument("--ws", help="gốc WebSocket của server đang chạy, vd wss://localhost")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="admin123")
    ap.add_argument("--ws-rounds", type=int, default=3)
    ap.add_argument("--ws-timeout", type=float, default=60.0)
    ap.add_argument("--out", help="ghi kết quả JSON ra file")
    args = ap.parse_args()

    import logging
    logging.disable(logging.WARNING)

    report: Dict[str, Any] = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "platform": f"{platform.system()} {platform.release()} / Python {platform.python_version()}",
        "clock": "time.perf_counter",
    }
    report["fast_path"] = await bench_fast_path(args.iterations)
    report["sentence_buffer"] = bench_sentence_buffer(args.iterations)
    report["tts_first_chunk"] = (
        {"skipped": "--skip-tts"} if args.skip_tts else await bench_tts_first_chunk(args.tts_rounds)
    )
    report["websocket"] = (
        {"skipped": "không có --ws"} if not args.ws
        else await bench_ws(args.ws.rstrip("/"), args.user, args.password, args.ws_rounds, args.ws_timeout)
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

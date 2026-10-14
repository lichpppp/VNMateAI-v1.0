# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/application/monitoring/performance_report.py
===================================================
Báo cáo hiệu năng theo THÁNG từ dữ liệu lưu bền (thẩm định thương mại 14/10/2026 P1-15: số đo phải đủ cho SLA, không mất khi
khởi động lại). Nguồn: `voice_traces` (giữ 400 ngày), `op_tasks` (sổ tác vụ AI), `llm_usage` (token / chi phí).

Mỗi tháng: số lượt thoại, trung vị / p90 thời gian trọn lượt, trung vị tới tiếng trả lời đầu, tỉ lệ rơi sang model dự phòng,
độ dài prompt trung bình, tỉ lệ tác vụ AI thành công (COMPLETED / đã kết thúc), token và chi phí (theo giá đã khai).
"""
from __future__ import annotations

import csv
import io
import json
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

FINISHED = {"COMPLETED", "FAILED", "ESCALATED", "BLOCKED", "CANCELLED"}


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    return v[max(0, min(len(v) - 1, round(q * (len(v) - 1))))]


def _months_back(n: int, now: Optional[datetime] = None) -> List[str]:
    now = now or datetime.now()
    y, m = now.year, now.month
    out = []
    for _ in range(max(1, n)):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def monthly(months: int = 13, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from mateai.infrastructure.database.db_manager import db_manager
    keys = _months_back(months, now)
    first = keys[0]
    turns: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    tasks: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    usage: Dict[str, Dict[str, float]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    with db_manager._get_connection() as conn:
        for r in conn.execute("SELECT created_at, data_json FROM voice_traces WHERE created_at >= ?;", (f"{first}-01",)).fetchall():
            try:
                turns[str(r[0])[:7]].append(json.loads(r[1]))
            except (TypeError, ValueError):
                continue
        for r in conn.execute("SELECT created_at, status FROM op_tasks WHERE created_at >= ?;", (f"{first}-01",)).fetchall():
            tasks[str(r[0])[:7]][str(r[1] or "")] += 1
        for r in conn.execute("SELECT day, total_tokens, cost FROM llm_usage WHERE day >= ?;", (f"{first}-01",)).fetchall():
            u = usage[str(r[0])[:7]]
            u["tokens"] += int(r[1] or 0)
            u["cost"] += float(r[2] or 0)
    rows = []
    for k in keys:
        t = turns.get(k, [])
        ttl = [float(x["ttl_ms"]) for x in t if isinstance(x.get("ttl_ms"), (int, float))]
        ttfa = [float(x["ttfa_answer_ms"]) for x in t if isinstance(x.get("ttfa_answer_ms"), (int, float))]
        llm_turns = [x for x in t if x.get("model") and x.get("model_requested")]
        fallback = sum(1 for x in llm_turns if x["model"] != x["model_requested"])
        prompts = [int(x["prompt_chars"]) for x in t if isinstance(x.get("prompt_chars"), int)]
        st = tasks.get(k, {})
        finished = sum(v for s, v in st.items() if s in FINISHED)
        rows.append({
            "month": k, "voice_turns": len(t),
            "turn_ms_p50": _pct(ttl, 0.5), "turn_ms_p90": _pct(ttl, 0.9),
            "first_answer_audio_ms_p50": _pct(ttfa, 0.5),
            "model_fallback_rate": round(fallback / len(llm_turns), 3) if llm_turns else None,
            "prompt_chars_avg": round(statistics.mean(prompts)) if prompts else None,
            "ai_tasks": sum(st.values()), "ai_tasks_finished": finished,
            "ai_success_rate": round(st.get("COMPLETED", 0) / finished, 3) if finished else None,
            "llm_tokens": int(usage[k]["tokens"]) if k in usage else 0,
            "llm_cost": round(usage[k]["cost"], 4) if k in usage else 0.0,
        })
    return rows


def to_csv(rows: List[Dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return ("﻿" + buf.getvalue()).encode("utf-8")

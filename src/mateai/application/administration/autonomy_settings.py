"""
mateai/application/administration/autonomy_settings.py
======================================================
Đổi giới hạn tự trị của AI (kill switch, tác nhân / tool bị tắt, L5, ngân sách,
chính sách email ra ngoài) — prompt Supervisor §95–§96, §70, §128.

Chuyển từ `routers/security.py` (Phase 10): router chỉ xác thực admin, gọi đây,
và phát sự kiện Portal. Mọi thay đổi: kiểm kiểu trước khi ghi, ghi lịch sử cấu hình
(khôi phục được), audit kèm phiên bản chính sách trước / sau.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

_LIST_FIELDS = ("disabled_tools", "never_autonomous_tools", "email_auto_reply_domains")


class AutonomyUpdateError(ValueError):
    pass


def view() -> Dict[str, Any]:
    from mateai.application.security import policy_engine as pe
    from mateai.config.loader import settings
    return {
        "status": "success",
        "autonomy": settings.autonomy.model_dump(),
        "known_agents": list(pe.KNOWN_AGENTS),
        "policy_version": pe.policy_version(),
    }


def update(actor: str, updates: Dict[str, Any], reason: str = "",
           mask: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
    """Áp `updates` (chỉ các trường gửi lên) vào `config.json → autonomy`. Trả `view()`.
    `mask`: hàm che bí mật cho bản lưu lịch sử cấu hình (tầng giao diện cung cấp)."""
    from mateai.application.administration import config_governance as gov
    from mateai.application.security import policy_engine as pe
    from mateai.application.security.safety_guard import security_engine
    from mateai.config.loader import AutonomyConfig, read_raw_config, reload_settings, settings, write_raw_config

    updates = dict(updates or {})
    if not updates:
        raise AutonomyUpdateError("Không có thay đổi nào.")
    for key in _LIST_FIELDS:
        if key in updates:
            updates[key] = sorted({str(x).strip() for x in updates[key] if str(x).strip()})
    if "disabled_agents" in updates:
        unknown = [a for a in updates["disabled_agents"] if a not in pe.KNOWN_AGENTS]
        if unknown:
            raise AutonomyUpdateError(f"Tác nhân không tồn tại: {', '.join(unknown)}")

    before_version = pe.policy_version()
    before_cfg = read_raw_config(strict=True)
    merged = dict(before_cfg)
    section = {**settings.autonomy.model_dump(), **dict(before_cfg.get("autonomy") or {}), **updates}
    AutonomyConfig(**section)  # kiểm tra kiểu trước khi ghi
    merged["autonomy"] = section
    write_raw_config(merged)
    reload_settings()
    try:
        gov.record(before_cfg, merged, actor, "Giới hạn tự trị" + (f": {reason}" if reason else ""),
                   mask or (lambda x: x))
    except Exception as exc:  # noqa: BLE001 — lịch sử hỏng không chặn việc đổi công tắc
        logger.warning("Không ghi được lịch sử cấu hình: %s", exc)
    old = dict(before_cfg.get("autonomy") or {})
    security_engine.log_audit(actor, "autonomy_policy_change", "POLICY", "SUCCESS", {
        "changes": {k: {"before": old.get(k), "after": v} for k, v in updates.items()},
        "reason": reason, "policy_version_before": before_version, "policy_version_after": pe.policy_version(),
    })
    if "kill_switch" in updates:
        logger.warning("[Autonomy] KILL SWITCH %s bởi %s", "BẬT" if updates["kill_switch"] else "TẮT", actor)
    return view()

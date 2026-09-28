"""
core/safety_guard.py
====================
Enterprise Zero-Trust Security Engine & Audit System for VN-MateAI (Phase 9).

Responsibilities:
  1. Data Sanitizer / Masking:
     - mask_sensitive_data(text): Masks LAN IPs, passwords, tokens, DB connection strings before sending to cloud LLMs.
  2. AST Static Code Inspection:
     - inspect_generated_code(code_str): Parses code using Python's AST module to detect dangerous calls (eval, exec,
       os.system, shutil.rmtree, forbidden keywords, protected directory tampering).
  3. Action Risk Assessment:
     - evaluate_action_risk(action_name, params): Returns "SAFE", "NEED_CONFIRM", or "BLOCKED".
  4. Enterprise Audit Logging:
     - log_audit(client_id, action, risk, status, details): Writes structured logs to logs/security_audit.log.
     - get_recent_audit_logs(limit): Retrieves recent audit events for Web Portal inspection.
  5. Backward-compatible SafetyGuard wrapper for existing HITL flows.
"""

from __future__ import annotations

import ast
import collections
import ctypes
import json
import logging
import os
import platform
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.config_loader import settings

logger = logging.getLogger(__name__)

# Ensure logs directory exists
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = _PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG_FILE = LOGS_DIR / "security_audit.log"


# ---------------------------------------------------------------------------
# AST Code Visitor for Deep Static Inspection
# ---------------------------------------------------------------------------

class CodeSecurityVisitor(ast.NodeVisitor):
    """
    AST Visitor that detects dangerous function calls, forbidden attributes,
    and protected system paths.
    """

    FORBIDDEN_CALLS = {"eval", "exec", "__import__", "globals", "locals", "compile"}
    DANGEROUS_OS_CALLS = {"system", "popen", "popen2", "popen3", "popen4", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe"}
    DANGEROUS_SHUTIL_CALLS = {"rmtree"}

    def __init__(self, protected_directories: List[str]) -> None:
        super().__init__()
        self.violations: List[str] = []
        self.protected_directories = [d.lower() for d in protected_directories]

    def visit_Call(self, node: ast.Call) -> None:
        # Check direct calls e.g., eval(...) or exec(...)
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in self.FORBIDDEN_CALLS:
                self.violations.append(f"Cấm sử dụng hàm nguy hiểm: '{func_name}()' tại dòng {node.lineno}")

        # Check attribute calls e.g., os.system(...) or shutil.rmtree(...)
        elif isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            # e.g., os.system
            if isinstance(node.func.value, ast.Name):
                module_name = node.func.value.id
                if module_name == "os" and attr_name in self.DANGEROUS_OS_CALLS:
                    self.violations.append(f"Cấm thực thi shell trực tiếp qua 'os.{attr_name}()' tại dòng {node.lineno}")
                elif module_name == "shutil" and attr_name in self.DANGEROUS_SHUTIL_CALLS:
                    self.violations.append(f"Cấm xóa thư mục hàng loạt qua 'shutil.{attr_name}()' tại dòng {node.lineno}")

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Inspect string literals for references to protected OS directories
        if isinstance(node.value, str):
            val_lower = node.value.lower()
            for pdir in self.protected_directories:
                if pdir and pdir in val_lower:
                    self.violations.append(f"Phát hiện truy cập đường dẫn hệ thống nhạy cảm '{pdir}' tại dòng {node.lineno}")
                    break
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# Core SecurityEngine (Zero-Trust & Audit)
# ---------------------------------------------------------------------------

class SecurityEngine:
    """
    Enterprise Zero-Trust Defense Engine for VN-MateAI.
    """

    def __init__(self) -> None:
        self._audit_cache: collections.deque = collections.deque(maxlen=300)
        self._load_existing_audit_logs()

    def _load_existing_audit_logs(self) -> None:
        """Warm up in-memory audit log cache from file."""
        if not AUDIT_LOG_FILE.exists():
            return
        try:
            lines = AUDIT_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-200:]:
                if line.strip():
                    try:
                        self._audit_cache.append(json.loads(line))
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Không thể nạp log kiểm toán cũ: %s", exc)

    # -----------------------------------------------------------------------
    # 1. Khử Nhiễm Dữ Liệu Đầu Vào (Data Sanitizer / Masking)
    # -----------------------------------------------------------------------

    def mask_sensitive_data(self, text: str) -> str:
        """
        Scan text and mask LAN IP addresses, passwords, secrets, and DB connection strings.
        Prevents internal enterprise secrets from leaking to cloud LLM providers.
        """
        if not text or not isinstance(text, str):
            return text

        masked = text

        # 1. Mask Internal LAN IPv4 Addresses (10.x.x.x, 192.168.x.x, 172.16-31.x.x)
        lan_ip_pattern = re.compile(
            r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"
        )
        masked = lan_ip_pattern.sub("[PROTECTED_IP]", masked)

        # 2. Mask Database Connection Strings (postgres, mysql, mongodb, redis, mssql)
        db_pattern = re.compile(
            r"(?i)\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|mssql):\/\/[^\s\"'>]+",
        )
        masked = db_pattern.sub("[PROTECTED_DB_URI]", masked)

        # 3. Mask Passwords / Passphrases
        pwd_pattern = re.compile(
            r"(?i)\b(password|passwd|pwd)(\s*[:=]\s*['\"]?)([^\s\"',;]+)(['\"]?)",
        )
        masked = pwd_pattern.sub(r"\1\2[PROTECTED_CREDENTIAL]\4", masked)

        # 4. Mask API Keys / Tokens
        key_pattern = re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)(\s*[:=]\s*['\"]?)([^\s\"',;]+)(['\"]?)",
        )
        masked = key_pattern.sub(r"\1\2[PROTECTED_KEY]\4", masked)

        # 5. Mask Bearer Tokens
        bearer_pattern = re.compile(
            r"(?i)\bBearer\s+([a-zA-Z0-9_\-\.]{20,})",
        )
        masked = bearer_pattern.sub("Bearer [PROTECTED_TOKEN]", masked)

        return masked

    # -----------------------------------------------------------------------
    # 2. Kiểm Tra Mã Động (AST Static Code Inspection)
    # -----------------------------------------------------------------------

    def inspect_generated_code(self, code_str: str) -> Tuple[bool, str]:
        """
        Inspect AI-synthesised Python code using Abstract Syntax Tree (AST) analysis
        and Blacklist checks before writing to disk.

        Returns:
            (is_safe: bool, message: str)
        """
        if not code_str or not code_str.strip():
            return False, "Mã nguồn rỗng."

        # Step 1: Check Blacklist / Forbidden Keywords in raw source
        forbidden_keywords = getattr(settings.security, "forbidden_keywords", [])
        code_lower = code_str.lower()
        for kw in forbidden_keywords:
            if kw.lower() in code_lower:
                reason = f"Mã nguồn chứa từ khóa bị cấm trong Danh sách đen (Blacklist): '{kw}'"
                logger.error("Zero-Trust AST Guard: %s", reason)
                self.log_audit("system", "inspect_code", "BLOCKED", "REJECTED", {"reason": reason})
                return False, reason

        # Step 2: Parse Abstract Syntax Tree (AST)
        try:
            tree = ast.parse(code_str)
        except SyntaxError as syn_err:
            reason = f"Mã nguồn sai cú pháp Python: {syn_err}"
            self.log_audit("system", "inspect_code", "BLOCKED", "SYNTAX_ERROR", {"error": str(syn_err)})
            return False, reason

        # Step 3: Walk AST nodes to find dangerous function calls & protected directories
        protected_dirs = getattr(settings.security, "protected_directories", [])
        visitor = CodeSecurityVisitor(protected_dirs)
        visitor.visit(tree)

        if visitor.violations:
            detail_msg = "; ".join(visitor.violations)
            reason = f"Phát hiện mã nguy hiểm qua kiểm toán AST: {detail_msg}"
            logger.error("Zero-Trust AST Guard vi phạm: %s", reason)
            self.log_audit("system", "inspect_code", "BLOCKED", "REJECTED", {"violations": visitor.violations})
            return False, reason

        return True, "Mã nguồn an toàn theo tiêu chuẩn Zero-Trust."

    # -----------------------------------------------------------------------
    # 3. Phân Loại Rủi Ro Lệnh (Risk Assessment: SAFE / NEED_CONFIRM / BLOCKED)
    # -----------------------------------------------------------------------

    def evaluate_action_risk(self, action_name: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Evaluate the execution risk of an action or skill tool.
        Returns:
            "BLOCKED"       — Violates blacklist, must be rejected immediately.
            "NEED_CONFIRM"  — High impact, requires administrator confirmation.
            "SAFE"          — Low risk, proceed automatically.
        """
        forbidden_keywords = getattr(settings.security, "forbidden_keywords", [])
        require_confirm = getattr(settings.security, "require_confirmation_actions", [])

        # Build payload string for blacklist inspection
        payload_str = (action_name + " " + json.dumps(params or {}, ensure_ascii=False)).lower()

        # Check 1: Forbidden keywords -> BLOCKED
        for kw in forbidden_keywords:
            if kw.lower() in payload_str:
                logger.warning("Action '%s' BLOCKED do chứa từ khóa cấm '%s'", action_name, kw)
                return "BLOCKED"

        # Phase 38 Zero-Trust: Low Risk actions are SAFE (Auto-Execute)
        if action_name in ("list_directory", "read_file"):
            return "SAFE"

        # Phase 38 Zero-Trust: High Risk actions require confirmation
        if action_name in ("write_file", "delete_item"):
            return "NEED_CONFIRM"

        # Check 2: Require confirmation actions -> NEED_CONFIRM
        if action_name in require_confirm:
            return "NEED_CONFIRM"

        # Check 3: Generic heuristics for destructive actions
        destructive_verbs = ["delete", "drop", "kill", "format", "wipe", "shutdown", "reboot"]
        if any(v in action_name.lower() for v in destructive_verbs):
            return "NEED_CONFIRM"

        return "SAFE"

    # -----------------------------------------------------------------------
    # 4. Ghi Vết Kiểm Toán An Ninh (Audit Logging)
    # -----------------------------------------------------------------------

    def log_audit(
        self,
        client_id: str,
        action: str,
        risk: str,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a security audit event to logs/security_audit.log and in-memory cache.
        """
        event = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp_epoch": time.time(),
            "client_id": client_id or "local",
            "action": action,
            "risk": risk.upper(),
            "status": status.upper(),
            "details": details or {},
        }

        # Append to in-memory deque
        self._audit_cache.append(event)

        # Append to log file
        try:
            line = json.dumps(event, ensure_ascii=False) + "\n"
            with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as exc:
            logger.error("Lỗi khi ghi security_audit.log: %s", exc)

    def get_recent_audit_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Return the most recent audit logs for the Web Portal.
        """
        all_logs = list(self._audit_cache)
        return all_logs[-limit:][::-1]  # Return newest first


# ---------------------------------------------------------------------------
# Backward-Compatible SafetyGuard (for HITL Dialogs)
# ---------------------------------------------------------------------------

_MB_YESNO = 0x00000004
_MB_ICONWARNING = 0x00000030
_MB_ICONERROR = 0x00000010
_IDYES = 6


class SafetyGuard:
    """
    Backward-compatible SafetyGuard wrapper integrated with SecurityEngine.
    """

    def scan_code(self, code_str: str) -> List[str]:
        is_safe, msg = security_engine.inspect_generated_code(code_str)
        if not is_safe:
            return [msg]
        return []

    def request_user_approval(self, skill_name: str, threats: List[str]) -> bool:
        if getattr(settings, "auto_execute", False) or getattr(settings, "AUTO_EXECUTE_UNVERIFIED_CODE", False):
            logger.info("Auto-executing unverified code enabled; approved '%s'.", skill_name)
            return True

        threat_section = ""
        if threats:
            threat_list = "\n".join(f"  ⚠ {t}" for t in threats)
            threat_section = f"\n\nCẢNH BÁO BẢO MẬT:\n{threat_list}"

        message = (
            f"AI đã tự tạo kỹ năng mới: [{skill_name}]\n\n"
            f"Bạn có cho phép cài đặt và thực thi không?{threat_section}\n\n"
            f"Chọn YES để chấp nhận, NO để từ chối."
        )
        title = "VN-MateAI — Phê Duyệt An Ninh Kỹ Năng Mới"

        if platform.system() == "Windows":
            icon = _MB_ICONERROR if threats else _MB_ICONWARNING
            try:
                res = ctypes.windll.user32.MessageBoxW(0, message, title, _MB_YESNO | icon)  # type: ignore[attr-defined]
                return res == _IDYES
            except Exception:
                pass

        # Không có GUI để hỏi người dùng (macOS/Linux/headless) → KHÔNG tự động duyệt.
        # Trước đây dòng này là `return True`, khiến cờ `auto_execute: false` trở nên
        # vô nghĩa trên mọi nền tảng không phải Windows: mã do AI tự sinh vẫn được
        # cài và chạy ngay. Đây là fail-open.
        logger.warning(
            "Từ chối cài đặt kỹ năng tự sinh '%s': không có giao diện để hỏi phê duyệt "
            "trên nền tảng này. Nếu bạn tin tưởng mã này, hãy bật tường minh "
            "auto_execute=true trong config.json.",
            skill_name,
        )
        return False

    def approve_or_reject(self, code_str: str, skill_name: str) -> Tuple[bool, List[str]]:
        threats = self.scan_code(code_str)
        if threats and not (getattr(settings, "auto_execute", False) or getattr(settings, "AUTO_EXECUTE_UNVERIFIED_CODE", False)):
            logger.warning("Kỹ năng '%s' bị từ chối do vi phạm an ninh: %s", skill_name, threats)
            return False, threats

        approved = self.request_user_approval(skill_name, threats)
        return approved, threats


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

security_engine = SecurityEngine()
safety_guard = SafetyGuard()

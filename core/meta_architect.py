"""
core/meta_architect.py
======================
Dynamic Skill Synthesizer — the self-expanding runtime for VN-MateAI.

Responsibilities:
  - Accept a natural-language intent description + failed-execution context.
  - Craft a specialised code-generation prompt and call the LLM.
  - Strip Markdown fences from the response to obtain raw Python source.
  - Validate syntax with ast.parse (zero-execution safety check).
  - Route through SafetyGuard for HITL approval.
  - Write the approved skill file to skills/ and trigger a hot-reload.

Design Notes:
  - Uses the same OpenAI-compatible SDK as llm_engine but with a separate
    system prompt tuned for code generation, NOT conversation.
  - PluginManager is imported lazily (inside methods) to break circular deps.
"""

from __future__ import annotations

import ast
import logging
import re
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

from core.config_loader import settings
from core.safety_guard import safety_guard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt template for code synthesis
# ---------------------------------------------------------------------------

_CODE_GEN_SYSTEM_PROMPT = textwrap.dedent("""\
    Bạn là một Python Core Engineer chuyên viết automation cho Windows.
    Nhiệm vụ của bạn là tạo ra một Python module ĐỘC LẬP, hoàn chỉnh, không có placeholder.

    YÊU CẦU BẮT BUỘC:
    1. Module phải import `export_skill` từ `core.plugin_manager`.
    2. Mỗi hàm công khai phải được đánh dấu bằng decorator @export_skill(name, description, parameters_schema).
    3. Sử dụng Type Hinting đầy đủ (typing module).
    4. Xử lý exception đúng cách — không để exception truyền thẳng ra ngoài.
    5. Chỉ dùng thư viện trong requirements.txt hoặc thư viện stdlib của Python.
    6. TUYỆT ĐỐI KHÔNG dùng pyautogui, opencv, screenshot, hay bất kỳ thư viện capture màn hình nào.
    7. Trả về DUY NHẤT một code block markdown: ```python ... ```
    8. Không giải thích, không thêm text ngoài code block.

    PLATFORM: Windows 10/11 x64, Python 3.10+
""")

# Regex to extract ```python ... ``` blocks (non-greedy, DOTALL)
_CODE_FENCE_PATTERN = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


# ---------------------------------------------------------------------------
# MetaArchitect
# ---------------------------------------------------------------------------


class MetaArchitect:
    """
    Synthesises new Python skill modules on demand using an LLM.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def synthesize_skill(
        self,
        intent_description: str,
        failed_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Ask the LLM to write a new Python skill module.

        Args:
            intent_description: Human-language description of what the skill must do.
            failed_context: Optional dict with keys like {"error", "attempted_skill",
                            "arguments"} to give the LLM correction context.

        Returns:
            Raw Python source code as a string.

        Raises:
            ValueError: If the LLM response contains no parseable code block.
            RuntimeError: If the LLM API call fails.
        """
        user_message = self._build_user_message(intent_description, failed_context)
        logger.info("MetaArchitect: synthesising skill for intent: '%s'", intent_description[:120])

        base_url = (settings.llm.base_url or "http://localhost:20128/v1").strip()
        api_key = (settings.llm.api_key or "sk-dummy").strip()

        # Build prioritized candidate model list: specialist models first (best for code), then active model, then router models
        candidate_models = []
        spec_models = getattr(settings.llm, "specialist_models", []) or []
        for m in spec_models:
            if m and m not in candidate_models:
                candidate_models.append(m)

        active_model = (settings.llm.model_name or "").strip()
        if active_model and active_model not in candidate_models:
            candidate_models.append(active_model)

        router_models = getattr(settings.llm, "router_models", []) or []
        for m in router_models:
            if m and m not in candidate_models:
                candidate_models.append(m)

        if not candidate_models:
            # Trước đây chỗ này điền cứng 3 model của một provider, nên khi
            # provider đó chết, MetaArchitect vẫn "chạy được" rồi hỏng im
            # lặng. Nay nói thẳng ra để lỗi hiện đúng chỗ.
            raise ValueError(
                "MetaArchitect chưa có model nào để dùng. Cấu hình model ở tab "
                "Quản Lý Trợ Lý AI > Bộ Não & Xử Lý Ngôn Ngữ."
            )

        from core.llm_provider import complete_text_blocking
        try:
            raw_response, used_model = complete_text_blocking(
                base_url, api_key or "sk-dummy", candidate_models,
                [
                    {"role": "system", "content": _CODE_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.2, max_tokens=4096, timeout=60.0,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("MetaArchitect: Tất cả mô hình sinh mã đều thất bại. Lỗi cuối: %s", exc)
            raise RuntimeError(f"Tất cả mô hình sinh mã qua 9router đều thất bại. Lỗi: {exc}") from exc
        logger.debug("MetaArchitect raw LLM response (model: %s):\n%s", used_model, raw_response[:800])

        code_str = self._extract_code(raw_response)
        if not code_str:
            raise ValueError(
                "LLM response did not contain a valid ```python ... ``` code block. "
                f"Raw response (truncated): {raw_response[:300]}"
            )

        return code_str

    def verify_and_install(
        self,
        code_str: str,
        skill_filename: str,
    ) -> bool:
        """
        Validate syntax, run through SafetyGuard, write file, and hot-reload.

        Args:
            code_str:       Raw Python source to install.
            skill_filename: Filename stem (without .py) for the new skill module.

        Returns:
            True if skill was successfully installed and loaded, False otherwise.
        """
        # --- Step 1: AST syntax validation ---
        if not self._validate_syntax(code_str, skill_filename):
            return False

        # --- Step 2: Zero-Trust Deep AST Code Inspection ---
        from core.safety_guard import security_engine
        is_safe, sec_reason = security_engine.inspect_generated_code(code_str)
        if not is_safe:
            logger.error("MetaArchitect: Mã kỹ năng '%s' bị SecurityEngine từ chối: %s", skill_filename, sec_reason)
            return False

        # --- Step 3: Safety / HITL gate ---
        approved, threats = safety_guard.approve_or_reject(code_str, skill_filename)
        if not approved:
            logger.warning(
                "Skill '%s' rejected by user or SafetyGuard (threats=%s).",
                skill_filename,
                threats,
            )
            return False

        # --- Step 3: Write to disk ---
        target_path = self._write_skill_file(code_str, skill_filename)
        if target_path is None:
            return False

        # --- Step 4: Hot-reload via PluginManager ---
        from core.plugin_manager import plugin_manager  # lazy import to avoid circular dep

        try:
            loaded = plugin_manager.load_plugins()
            logger.info(
                "Skill '%s' installed at %s. Total skills in memory: %d",
                skill_filename,
                target_path,
                loaded,
            )
            return True
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Hot-reload failed after installing '%s': %s", skill_filename, exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_user_message(
        self,
        intent_description: str,
        failed_context: Optional[Dict[str, Any]],
    ) -> str:
        """Compose the user-turn message for the code-gen LLM call."""
        lines = [
            "YÊU CẦU:",
            intent_description,
        ]
        if failed_context:
            lines.append("\nBỐI CẢNH THẤT BẠI TRƯỚC ĐÓ (để tránh lặp lại lỗi):")
            for key, value in failed_context.items():
                lines.append(f"  {key}: {value}")
        lines.append("\nViết ngay module Python theo yêu cầu ở trên.")
        return "\n".join(lines)

    @staticmethod
    def _extract_code(raw: str) -> str:
        """
        Extract Python source from a Markdown-fenced code block.
        Returns empty string if no block is found.
        """
        match = _CODE_FENCE_PATTERN.search(raw)
        if match:
            return match.group(1).strip()
        # Fallback: if the entire response looks like raw Python (no fences),
        # return it as-is if it starts with recognisable Python tokens.
        stripped = raw.strip()
        if stripped.startswith(("import ", "from ", "def ", "#", '"""', "class ")):
            logger.debug("MetaArchitect: no code fence found, treating entire response as code.")
            return stripped
        return ""

    @staticmethod
    def _validate_syntax(code_str: str, skill_filename: str) -> bool:
        """
        Attempt to parse code_str with ast.parse.
        Returns True if valid, False (and logs the error) if not.
        """
        try:
            ast.parse(code_str)
            logger.debug("Syntax validation passed for '%s'.", skill_filename)
            return True
        except SyntaxError as exc:
            logger.error(
                "Syntax error in synthesised skill '%s': %s (line %d, col %d)",
                skill_filename,
                exc.msg,
                exc.lineno or -1,
                exc.offset or -1,
            )
            return False

    def _write_skill_file(
        self,
        code_str: str,
        skill_filename: str,
    ) -> Optional[Path]:
        """
        Write the approved code to skills/{skill_filename}.py.
        Returns the Path on success, None on failure.
        """
        skills_dir: Path = settings.SKILLS_DIR  # type: ignore[assignment]
        skills_dir.mkdir(parents=True, exist_ok=True)

        # Sanitise filename: only alphanumeric and underscores
        safe_name = re.sub(r"[^\w]", "_", skill_filename).strip("_") or "auto_skill"
        target: Path = skills_dir / f"{safe_name}.py"

        header = (
            f'"""\n'
            f"Auto-synthesised skill: {safe_name}\n"
            f"Generated by VN-MateAI MetaArchitect.\n"
            f'"""\n\n'
        )
        try:
            target.write_text(header + code_str, encoding="utf-8")
            logger.info("Skill file written: %s (%d bytes)", target, len(code_str))
            return target
        except OSError as exc:
            logger.error("Failed to write skill file '%s': %s", target, exc)
            return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

meta_architect = MetaArchitect()

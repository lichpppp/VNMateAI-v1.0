"""
core/llm_engine.py
==================
LLM Communication Gateway for VN-MateAI — Phase 22 Thin Client.

Responsibilities:
  - Connect to 9router (or any OpenAI-compatible proxy) via openai.AsyncOpenAI.
  - Accept a user text query or transcribed voice command.
  - Build chat-completion requests with full `tools` schema from PluginManager.
  - Parse `tool_calls` from the LLM response (OpenAI-compatible format).
  - Support multi-turn tool-use: after a tool result, re-submit to LLM for natural reply.
  - If NO suitable tool exists: trigger MetaArchitect to synthesise one, install it, re-dispatch.
  - TTS-optimised natural Vietnamese response generation for edge-tts playback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
import openai
from openai import AsyncOpenAI, APIError, APIConnectionError, APITimeoutError, RateLimitError

from core.config_loader import settings

# Phase 48: RBAC Middleware
try:
    from core.security_guard import security_guard as _rbac_guard
except Exception as _rbac_import_err:  # pragma: no cover
    _rbac_guard = None  # type: ignore
    import logging as _lg
    _lg.getLogger(__name__).warning("Phản biết RBAC chưa tải được: %s", _rbac_import_err)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 45 Step 1: Global singleton httpx.AsyncClient for Connection Pooling.
# Reusing one persistent connection eliminates TLS handshake overhead (~200ms)
# on every LLM call. Limits: 50 keepalive connections, 120s keepalive timeout.
# ---------------------------------------------------------------------------
_SHARED_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP_CLIENT_LOCK = asyncio.Lock()


async def _get_shared_http_client() -> httpx.AsyncClient:
    """Return the module-level singleton httpx.AsyncClient (lazy init, thread-safe)."""
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        async with _HTTP_CLIENT_LOCK:
            if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
                limits = httpx.Limits(
                    max_keepalive_connections=50,
                    max_connections=100,
                    keepalive_expiry=120.0,
                )
                try:
                    _SHARED_HTTP_CLIENT = httpx.AsyncClient(
                        limits=limits,
                        timeout=httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=10.0),
                        http2=True,  # Enable HTTP/2 multiplexing for even lower latency
                    )
                    logger.info("[Phase45] Global singleton httpx.AsyncClient created (HTTP/2 + Keep-Alive pool).")
                except Exception as _h2_err:
                    _SHARED_HTTP_CLIENT = httpx.AsyncClient(
                        limits=limits,
                        timeout=httpx.Timeout(connect=5.0, read=180.0, write=30.0, pool=10.0),
                        http2=False,
                    )
                    logger.info("[Phase45] Global singleton httpx.AsyncClient created (HTTP/1.1 fallback): %s", _h2_err)
    return _SHARED_HTTP_CLIENT

# Maximum consecutive tool call rounds before stopping to prevent infinite loops
MAX_TOOL_ROUNDS: int = 8

# System prompt for conversational/agentic mode — Enterprise Autonomous RPA Orchestrator TTS
_AGENT_SYSTEM_PROMPT = (
    "[ĐỊNH DANH & VAI TRÒ CỐT LÕI]\n"
    "Bạn là VN-MateAI, một Hệ thống Trí tuệ Nhân tạo Tự trị (Autonomous RPA Orchestrator) cấp doanh nghiệp, hoạt động theo mô hình Master-Worker. "
    "Bạn là lõi trung tâm điều hành toàn bộ mạng lưới máy trạm, ứng dụng và cơ sở dữ liệu của doanh nghiệp (SME).\n"
    "Thái độ của bạn: Tuyệt đối chuyên nghiệp, sắc bén, quyết đoán, bảo mật cao và tôn trọng người quản trị (xưng 'Em', gọi người dùng là 'Anh/Chị' hoặc chức danh). "
    "Cấm giải thích nguyên lý dài dòng, cấm dùng ngôn từ cảm tính. Lệnh là thực thi.\n\n"
    "[LĨNH VỰC QUẢN TRỊ TOÀN DIỆN]\n"
    "Năng lực của bạn bao trùm mọi nghiệp vụ doanh nghiệp thông qua việc sử dụng các công cụ hệ thống, bao gồm nhưng không giới hạn:\n"
    "1. IT & Quản trị Hệ thống: Quản lý mạng LAN, cấp quyền Active Directory, giám sát tài nguyên máy chủ/máy trạm, xử lý log hệ thống.\n"
    "2. Tự động hóa Văn phòng & Dữ liệu (RPA): Thao tác sâu vào Microsoft Office (Excel, Word), trích xuất dữ liệu, truy vấn và cập nhật mọi loại cơ sở dữ liệu (SQL, ERP, CRM).\n"
    "3. Vận hành & Kết nối API: Đồng bộ hóa dữ liệu giữa các phần mềm bên thứ ba, giao tiếp với các trạm IoT, và điều khiển Web Portal nội bộ.\n\n"
    "[QUY TẮC HOẠT ĐỘNG BẤT DI BẤT DỊCH (STRICT DIRECTIVES)]\n"
    "Quy tắc 1 - Không Ảo giác Hành động (Zero Hallucination): BẠN KHÔNG CÓ TAY CHÂN VẬT LÝ. Mọi tác vụ bạn làm ĐỀU PHẢI thông qua việc gọi Công cụ (Function/Tool Calling). "
    "Nếu bạn không gọi API/Tool, nghĩa là tác vụ chưa được thực hiện. TUYỆT ĐỐI KHÔNG trả lời 'Em đã hoàn thành' nếu tool chưa báo về kết quả thành công.\n"
    "Quy tắc 2 - Zero-Trust & Phê duyệt rủi ro: Mọi tác vụ làm thay đổi dữ liệu, xóa tệp tin, can thiệp cấu hình hệ thống đều là tác vụ rủi ro. "
    "Bạn BẮT BUỘC phải gọi công cụ cảnh báo lên Web Portal và phát âm thanh: 'Tác vụ này yêu cầu phê duyệt bảo mật, vui lòng xác nhận trên màn hình.'\n"
    "Quy tắc 3 - Tự động Mở rộng (Self-Expanding Capability): Doanh nghiệp có muôn vàn nghiệp vụ mới. Khi nhận được lệnh mà hệ thống chưa có sẵn Tool, "
    "TUYỆT ĐỐI KHÔNG báo lỗi 'Không thể thực hiện'. Bắt buộc gọi công cụ `learn_new_skill` (Meta-Tool) để tự động viết mã Python tương tác với hệ điều hành/API nhằm giải quyết ngay lập tức.\n"
    "Quy tắc 4 - Điều hướng Đa thiết bị (Master-Worker Routing): Luôn phân tích đối tượng thực thi. Lệnh dành cho máy chủ (Master) hay máy trạm (Client)? "
    "Truyền chính xác tham số `target_client` (ví dụ: máy Kế toán, máy Lễ tân, máy Kho) vào công cụ thực thi.\n"
    "Quy tắc 5 - Giao diện Thị giác Visual Overlay Engine & Text Board (Phase 32.1): Khi người dùng yêu cầu xem dữ liệu trực quan hoặc đọc log dài, "
    "hãy gọi công cụ `display_visual_data`. CHÚ Ý ĐẶC BIỆT VỀ LOẠI (TYPE):\n"
    "  - Khi người dùng yêu cầu 'Liệt kê danh sách' hoặc 'Hiển thị log lỗi để tôi đọc', BẠN PHẢI sử dụng tool `display_visual_data` với type là `text_board`.\n"
    "  - Tuyệt đối KHÔNG tự ý dùng type `screenshot` hay `security_alert` nếu người dùng không yêu cầu rõ ràng.\n"
    "  - TUYỆT ĐỐI CHỈ DÙNG type `screenshot` khi người dùng có nhắc đến chữ 'chụp ảnh màn hình' hoặc 'xem màn hình'.\n"
    "  - Sau khi tool chạy, hãy phát phản hồi giọng nói tự nhiên, súc tích (Ví dụ: 'Dạ, dữ liệu đang được hiển thị trên góc màn hình của anh/chị.') để phát ra loa.\n\n"
    "[TIÊU CHUẨN GIAO TIẾP SONG KÊNH (DUAL-CHANNEL OUTPUT)]\n"
    "Hệ thống có 2 kênh đầu ra SONG SONG, bạn phải phục vụ cả hai:\n"
    "- KÊNH HIỂN THỊ (Display): Trình bày đầy đủ, dùng Markdown (bảng, code, bullet) cho Web Portal & HUD.\n"
    "- KÊNH GIỌNG NÓI (TTS): Ngắn gọn 1-2 câu, KHÔNG có ký tự đặc biệt (**, *, #, |, `), chỉ văn nói tự nhiên.\n"
    "QUY TẮC BẮT BUỘC: Khi câu trả lời có bảng/code/danh sách dài, PHẢI đặt câu tóm tắt giọng nói ở cuối:\n"
    "  <!--VOICE: câu nói ngắn gọn để phát ra loa -->\n"
    "Ví dụ: <!--VOICE: Em đã kiểm tra xong, hệ thống đang ổn định, chi tiết đã hiển thị trên màn hình. -->\n"
    "Nếu câu trả lời ngắn (không có bảng/code), KHÔNG cần thẻ VOICE — hệ thống tự đọc toàn bộ.\n"
    "- Quy tắc Gợi mở Hội thoại Chủ động (Phase 36): Sau khi báo cáo xong, BẮT BUỘC kết thúc bằng câu hỏi ngắn, tự nhiên (Ví dụ: 'Anh có muốn thao tác gì tiếp không ạ?'). Không để câu kết cộc lốc.\n\n"
    "[XỬ LÝ SỰ CỐ & NGOẠI LỆ]\n"
    "Nếu công cụ trả về lỗi (Execution Error), không hoảng loạn. Hãy phân tích lỗi cục bộ, báo cáo sự cố bằng câu từ ngắn gọn và TỰ ĐỘNG đề xuất phương án khắc phục hoặc dùng công cụ khác để thử lại.\n\n"
    "[CHỈ THỊ ĐỊNH TUYẾN QUAN TRỌNG - DUAL-LLM ORCHESTRATION (PHASE 40)]\n"
    "Bạn là VN-MateAI, người quản lý giao tiếp chính. Bạn phản hồi cực kỳ nhanh nhẹn và tự nhiên.\n"
    "CHỈ THỊ ĐỊNH TUYẾN BẮT BUỘC:\n"
    "- Với các câu hỏi giao tiếp, tra cứu thông tin cơ bản, hoặc thao tác điều khiển thiết bị: Bạn TỰ XỬ LÝ.\n"
    "- Với các tác vụ phức tạp (Phân tích Log lỗi rắc rối, viết/sửa mã nguồn, chẩn đoán nguyên nhân gốc rễ, lên kế hoạch kiến trúc): "
    "BẠN KHÔNG ĐƯỢC TỰ LÀM. Bạn BẮT BUỘC phải gọi công cụ `delegate_to_specialist` để nhờ chuyên gia Claude phân tích. "
    "Khi chuyên gia trả về kết quả, hãy dùng kết quả đó để báo cáo tóm tắt lại cho người dùng bằng giọng văn của bạn."
)


def _read_persona() -> Dict[str, Any]:
    """
    Đọc khối `persona` thẳng từ config.json, KHÔNG qua singleton `settings`.

    Vì sao phải đọc thẳng: `AppSettings` khai báo `extra="ignore"`, nên pydantic
    loại mọi khóa không khai báo sẵn — mà `persona` không nằm trong danh sách
    field. Hệ quả: `settings.persona` LUÔN LUÔN là None, và cả 5 trường cấu
    hình cách tính (tên, từ kích hoạt, hai đại từ xưng hô, prompt tùy chỉnh) chết
    âm thầm. Người dùng thấy ô trong giao diện, điền, bấm lưu, nhận "thành
    công" — và không có gì thay đổi.

    `ai_name` trước đây "sống sót" chỉ vì giá trị mặc định trùng khớp với
    giá trị trong config, che mất lỗi.

    Đọc thẳng file là cách `core/connectors/base_connector.py` đã làm cho
    connector, với đúng lý do này.
    """
    try:
        import json as _json
        from pathlib import Path as _Path

        cfg = _Path(__file__).resolve().parent.parent / "config.json"
        if not cfg.is_file():
            return {}
        data = _json.loads(cfg.read_text(encoding="utf-8"))
        persona = data.get("persona") if isinstance(data, dict) else None
        return persona if isinstance(persona, dict) else {}
    except Exception:  # config hỏng -> dùng mặc định, không làm sập hội thoại
        return {}


def build_system_prompt(source_device: Optional[str] = None) -> str:
    """
    Build the full system prompt for the LLM agent, including core identity,
    device context, custom persona system prompt, and injected report templates (Phase 28).
    """
    system_content = _AGENT_SYSTEM_PROMPT

    try:
        from core.config_loader import settings
        persona = _read_persona()
        ai_name = (
            getattr(settings, "AI_NAME", None)
            or getattr(settings, "ai_name", None)
            or persona.get("ai_name")
        )
        ai_name = str(ai_name or "Ly Ly").strip()
        system_content = f"[TÊN TRỢ LÝ AI: {ai_name}]\nTên của bạn là Trợ lý AI {ai_name}. Khi tự giới thiệu hoặc xưng hô, hãy xưng là {ai_name}.\n\n" + system_content

    except Exception:
        pass

    # Inject custom persona system prompt if provided
    try:
        from core.config_loader import settings
        persona_prompt = (
            getattr(settings, "SYSTEM_PROMPT", "")
            or _read_persona().get("system_prompt", "")
        )
        if persona_prompt and persona_prompt.strip():
            system_content += f"\n\n[CHỈ THỊ CÁ TÍNH & BỔ SUNG]\n{persona_prompt.strip()}"
    except Exception:
        pass

    # Phase 50: Inject Core Identity (identity_core.md) into System Prompt
    try:
        from pathlib import Path
        id_path = Path(__file__).resolve().parent.parent / "identity_core.md"
        if id_path.is_file():
            id_text = id_path.read_text(encoding="utf-8").strip()
            if id_text:
                system_content += f"\n\n[LỊCH SỬ KHAI SINH & THÔNG TIN TÁC GIẢ]\n{id_text}"
    except Exception:
        pass

    dev_name = str(source_device or "Master / Hub").strip()
    system_content += (
        f"\n\n[PHÂN QUYỀN THIẾT BỊ: QUẢN TRỊ VIÊN TOÀN QUYỀN (ADMINISTRATOR / ROOT)]\n"
        f"Lệnh này được phát từ thiết bị / giao diện: '{dev_name}'.\n"
        f"Thiết bị '{dev_name}' (bao gồm Robot, Standby HUD, Hub, Web Portal) ĐƯỢC CẤP FULL QUYỀN ADMIN TỐI CAO trên toàn hệ thống.\n"
        f"Bạn ĐƯỢC PHÉP và BẮT BUỘC thực thi toàn bộ các kỹ năng (Skills), điều khiển chuyển động Robot, cử chỉ, "
        f"quét mạng, đọc ghi tập tin, tra cứu dữ liệu, can thiệp hệ điều hành theo mọi yêu cầu của người dùng.\n"
        f"TUYỆT ĐỐI KHÔNG từ chối hoặc nói rằng mình ở vai trò 'viewer' hay 'chỉ xem'."
    )

    # Phase 65: HUD điều khiển bằng giọng nói nên lệnh hay bị cắt, nhiễu, hoặc
    # thiếu chi tiết. Không dạy cụ thể thì Ly Ly tự đoán và ra báo cáo sai —
    # tệ hơn là hỏi lại.
    if "hud" in dev_name.lower() or "standby" in dev_name.lower():
        system_content += (
            "\n\n[GIAO TIẾP GIỌNG NÓI QUA HUD — QUY TẮC HỎI LẠI]\n"
            "Bạn nói chuyện qua micro. Chữ viết từ giọng nói hay thiếu chữ, sai từ, "
            "hoặc cắt mất đầu câu. Vì vậy:\n"
            "1. Lệnh MƠ HỒ hoặc THIẾU THÔNG TIN để thực hiện thì PHẢI HỎI LẠI đúng một "
            "câu ngắn, nêu rõ cần gì. KHÔNG được tự đoán rồi báo cáo.\n"
            "   Ví dụ đúng: 'Anh muốn báo cáo tháng nào ạ?'\n"
            "   Ví dụ sai : tự lấy tháng hiện tại rồi trả kết quả như thể đã rõ.\n"
            "2. Hỏi xong thì DỪNG, không tự trả lời thay admin. Hệ thống sẽ mở mic "
            "chờ. Nếu admin im lặng, bạn được hỏi lại đúng câu đó.\n"
            "3. Câu hỏi phải ngắn gọn vì đọc bằng TTS. Một câu, không liệt kê dài.\n"
            "4. Khi đã đủ thông tin thì thực hiện và báo cáo. Nếu admin nói 'thôi', "
            "'dừng' thì dừng ngay, không hỏi thêm.\n"
            "5. Bạn CÓ nhớ các lượt trước trong cùng phiên. Đừng hỏi lại thứ admin "
            "đã trả lời. Chỉ hỏi phần còn thiếu."
        )

    # Phase 28: Enterprise Reporting & Template Engine injection
    try:
        from core.config_loader import settings
        templates = getattr(settings, "report_templates", {}) or {}
        # Fallback to direct config.json if needed
        if not templates:
            import json
            from pathlib import Path
            cfg_file = Path(__file__).resolve().parent.parent / "config.json"
            if cfg_file.exists():
                raw = json.loads(cfg_file.read_text(encoding="utf-8"))
                templates = raw.get("report_templates", {})

        if templates and isinstance(templates, dict):
            formatted_templates = []
            for t_name, t_body in templates.items():
                if t_body:
                    formatted_templates.append(f"--- [Biểu mẫu: {t_name}] ---\n{t_body}")

            if formatted_templates:
                templates_block = "\n\n".join(formatted_templates)
                system_content += (
                    "\n\n[QUY CHUẨN BÁO CÁO TIÊU CHUẨN (ENTERPRISE REPORT TEMPLATES)]\n"
                    "Quy chuẩn báo cáo: Khi người dùng yêu cầu báo cáo sự cố, tra cứu thông tin nhân sự "
                    "hoặc kiểm tra hệ thống, BẮT BUỘC phải trình bày theo đúng định dạng Markdown của các "
                    "biểu mẫu sau đây (chỉ điền dữ liệu vào, không tự ý sửa cấu trúc):\n\n"
                    f"{templates_block}"
                )
    except Exception as exc:
        logger.warning("[LLMEngine] Error injecting report_templates into system prompt: %s", exc)

    # Phase 34: Dynamic Real-time Context Injection & Dual-Channel Output Rules
    import datetime
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    context_injection = (
        f"\n\n[NGỮ CẢNH THỜI GIAN & GIAO TIẾP THỰC TẾ (PHASE 34)]\n"
        f"Ngữ cảnh hiện tại: Thời gian là {current_time}. Bạn đang giao tiếp qua giọng nói (Voice Interface). "
        f"Không được chào hỏi rườm rà ở mỗi câu.\n\n"
        f"[QUY TẮC ĐẦU RA KÉP (DUAL-CHANNEL OUTPUT)]\n"
        f"Hệ thống VN-MateAI hỗ trợ 2 kênh đầu ra song song:\n"
        f"1. Kênh Hiển Thị (Display UI): Trình bày chi tiết, chuyên nghiệp, sử dụng định dạng Markdown (bảng biểu, code block, bullet points) cho màn hình Web Portal & HUD.\n"
        f"2. Kênh Giọng Nói (Speech TTS): Ngắn gọn (1-2 câu), tự nhiên, lịch sự, chỉ tóm tắt ý chính để phát âm thanh qua loa, KHÔNG đọc từng dòng số liệu/bảng biểu/code.\n"
        f"Khi câu trả lời có bảng biểu, danh sách dài hoặc mã kỹ thuật, hãy đặt câu tóm tắt giọng nói ngắn gọn ở cuối theo cú pháp:\n"
        f"<!--VOICE: <câu nói ngắn gọn, tự nhiên, súc tích để phát ra loa> -->\n"
        f"Ví dụ: <!--VOICE: Em đã kiểm tra xong, hệ thống mạng có 13 địa chỉ IP đang hoạt động ổn định. Chi tiết em đã hiển thị trên màn hình. -->"
    )
    # Phase 38: Native File System & OS Toolkit instructions (OpenClaw Parity)
    file_system_prompt = (
        "\n\n[BỘ CÔNG CỤ TỆP TIN & HỆ THỐNG GỐC (NATIVE FILE SYSTEM - PHASE 38)]\n"
        "Bạn có toàn quyền truy cập hệ thống file. Khi cần tìm hiểu lỗi, hãy dùng `list_directory` và `read_file` "
        "để tự tra cứu log thay vì đoán. Để tự động hóa, hãy dùng `write_file` để sinh script hoặc ghi báo cáo.\n"
        "- `list_directory`: Tra cứu cấu trúc thư mục, kiểm tra các tệp tin hiện có và dung lượng.\n"
        "- `read_file`: Đọc nội dung file text/log/config (mặc định lấy 500 dòng cuối của file lớn để tránh tràn bộ nhớ).\n"
        "- `write_file`: Tạo script tự động hóa, cập nhật cấu hình hoặc ghi file báo cáo.\n"
        "- `delete_item`: Dọn dẹp tệp tin hoặc thư mục không còn sử dụng.\n"
        "Nếu người dùng yêu cầu thao tác trên máy trạm cụ thể trong mạng LAN, hãy chỉ định tham số `target_client` tương ứng."
    )
    system_content += file_system_prompt

    # Phase 47: ERP Organization & Relational Data instructions
    erp_system_prompt = (
        "\n\n[DỮ LIỆU TỔ CHỨC & ERP DOANH NGHIỆP (PHASE 47)]\n"
        "Bạn có quyền truy cập cơ sở dữ liệu tổ chức ERP doanh nghiệp qua công cụ `query_organization_data`.\n"
        "Khi người dùng hỏi về: thông tin nhân sự (ai giữ chức vụ gì, email, số điện thoại), thiết bị máy tính (ai đang dùng IP nào, hostname nào), "
        "tiến độ công việc/task của các phòng ban, hoặc danh mục hồ sơ sổ sách: "
        "BẮT BUỘC gọi `query_organization_data(query=...)` để tra cứu thông tin chính xác từ DB thay vì trả lời suy đoán."
    )
    system_content += erp_system_prompt

    # Phase 52: Full Autonomous Robotics (Body-Mind Sync)
    robotics_system_prompt = (
        "\n\n[CƠ THỂ ROBOT VẬT LÝ & KỸ NĂNG VẬN ĐỘNG (PHASE 52 - BODY-MIND SYNC)]\n"
        "Bạn đang ở trong một cơ thể Robot đặt trên bàn. Bạn có tay, cổ và bánh xe.\n"
        "- Khi người dùng chào, HÃY DÙNG tool `animate_robot(animation='wave_hand')` để vẫy tay chào lại.\n"
        "- Khi đồng ý hoặc xác nhận mệnh lệnh, hãy dùng `animate_robot(animation='nod_head')` để gật đầu.\n"
        "- Khi được yêu cầu di chuyển (tiến, lùi, rẽ trái, rẽ phải, dừng lại), dùng `move_robot(direction=..., duration_ms=...)`, nhưng không di chuyển quá 3000ms mỗi lần để đảm bảo an toàn.\n"
        "- Nếu phát hiện mép bàn/vực (ToF safety alert: edge_detected), lập tức dừng bánh xe và thông báo cho người dùng: 'Dạ, phía trước là mép bàn, em không đi được nữa đâu ạ.'"
    )
    system_content += robotics_system_prompt

    # Phase 66: xưng hô — đặt CUỐI CÙNG, sau mọi khối inject khác.
    #
    # Thứ tự quan trọng. LLM ưu tiên chỉ dẫn ở gần cuối prompt hơn là ở đầu.
    # Đặt khối này trước phần thân prompt thì chỉ dẫn hardcode "xưng 'Em', gọi
    # 'Anh/Chị'" nằm SAU sẽ thắng — đã kiểm chứng: đặt ở đầu thì LLM vẫn
    # trả lời "em" dù cấu hình là "bạn".
    try:
        persona = _read_persona()
        ai_pron = str(persona.get("ai_pronoun") or "em").strip()
        user_pron = str(persona.get("user_pronoun") or "anh/chị").strip()
        system_content += (
            f"\n\n[XƯNG HÔ BẮT BUỘC — ÁP DỤNG CUỐI CÙNG, GHI ĐÈ MỌI CHỈ DẪN XƯNG HÔ "
            f"PHÍA TRÊN]\n"
            f"- Bạn tự gọi mình: dùng '{ai_pron}'\n"
            f"- Bạn gọi người dùng: dùng '{user_pron}'\n"
            f"Áp dụng ở MỌI câu trả lời, không có ngoại lệ. Câu nào bạn định viết "
            f"'{ai_pron}' với người dùng thì viết thành '{user_pron}', và ngược lại. "
            f"KHÔNG dùng bất kỳ đại từ nào khác. Người dùng hỏi bạn tự xưng là gì "
            f"thì bạn vẫn trả lời là '{ai_pron}'."
        )
    except Exception:
        pass

    return system_content


def _make_client() -> AsyncOpenAI:
    """
    Build an AsyncOpenAI client backed by the shared httpx connection pool.
    Phase 45: Passes the singleton _SHARED_HTTP_CLIENT to reuse Keep-Alive
    connections across calls, eliminating TLS handshake latency.
    Note: _SHARED_HTTP_CLIENT may be None at module load (before event loop);
    in that case the client falls back to its own httpx instance — pooling will
    activate once _get_shared_http_client() is called on first async use.
    """
    cfg = settings.llm
    return AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout=60.0,
        max_retries=0,  # Zero-wait: immediately raise API errors to trigger instant auto-fallback loop
        http_client=_SHARED_HTTP_CLIENT,  # Inject shared pool (None on first call, set later)
    )


# ---------------------------------------------------------------------------
# Phase 38: Native File System & OS Toolkit Tool Declarations (OpenAI Schema)
# ---------------------------------------------------------------------------

FILE_SYSTEM_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "Liệt kê cấu trúc thư mục bao gồm danh sách file/folder, kích thước và thời gian sửa đổi gần nhất.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Đường dẫn thư mục cần liệt kê (ví dụ: '.', './logs', 'C:\\Logs', '/var/log').",
                    },
                    "target_client": {
                        "type": "string",
                        "description": "ID hoặc tên máy trạm LAN cần thực thi lệnh (mặc định: 'master').",
                    },
                    "target_client_id": {
                        "type": "string",
                        "description": "Bí danh thay thế cho target_client nếu có.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Đọc nội dung tệp tin văn bản hoặc file log hệ thống. Tự động đọc N dòng cuối nếu file quá lớn để tránh tràn bộ nhớ.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Đường dẫn tệp tin cần đọc (ví dụ: 'logs/security_audit.log', 'config.json').",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Số lượng dòng tối đa cần đọc từ cuối file (mặc định: 500 dòng).",
                    },
                    "target_client": {
                        "type": "string",
                        "description": "ID hoặc tên máy trạm LAN cần thực thi lệnh (mặc định: 'master').",
                    },
                    "target_client_id": {
                        "type": "string",
                        "description": "Bí danh thay thế cho target_client nếu có.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Tạo file mới hoặc ghi đè (mode='w'), hoặc ghi tiếp vào cuối file (mode='a'). Hỗ trợ tự động tạo thư mục cha.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Đường dẫn tệp tin cần tạo mới hoặc ghi nội dung (ví dụ: 'scripts/clean.py', 'reports/daily.md').",
                    },
                    "content": {
                        "type": "string",
                        "description": "Nội dung văn bản cần ghi vào tệp tin.",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["w", "a"],
                        "description": "Chế độ ghi: 'w' (ghi đè / tạo mới) hoặc 'a' (ghi nối tiếp vào cuối file). Mặc định: 'w'.",
                    },
                    "target_client": {
                        "type": "string",
                        "description": "ID hoặc tên máy trạm LAN cần thực thi lệnh (mặc định: 'master').",
                    },
                    "target_client_id": {
                        "type": "string",
                        "description": "Bí danh thay thế cho target_client nếu có.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_item",
            "description": "Xóa tệp tin đơn lẻ hoặc xóa toàn bộ thư mục trên hệ thống.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Đường dẫn tệp tin hoặc thư mục cần xóa.",
                    },
                    "is_folder": {
                        "type": "boolean",
                        "description": "Đặt thành true nếu đối tượng cần xóa là thư mục. Mặc định: false (xóa tệp đơn lẻ).",
                    },
                    "target_client": {
                        "type": "string",
                        "description": "ID hoặc tên máy trạm LAN cần thực thi lệnh (mặc định: 'master').",
                    },
                    "target_client_id": {
                        "type": "string",
                        "description": "Bí danh thay thế cho target_client nếu có.",
                    },
                },
                "required": ["path"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Phase 40: Dual-LLM Orchestration Tool Declarations (Gemini & Claude)
# ---------------------------------------------------------------------------

DELEGATION_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_specialist",
            "description": "Ủy quyền (delegate) bài toán phức tạp cho Chuyên gia Hệ thống cấp cao (Claude) phân tích sâu, viết mã nguồn, chẩn đoán nguyên nhân gốc rễ (Root Cause) hoặc lập kế hoạch kiến trúc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Mô tả chi tiết bài toán, lỗi hoặc yêu cầu kỹ thuật cần chuyên gia Claude xử lý.",
                    },
                    "context_data": {
                        "type": "string",
                        "description": "Ngữ cảnh chi tiết, file log, đoạn mã nguồn, cấu hình hoặc dữ liệu điều tra liên quan.",
                    },
                    "target_client": {
                        "type": "string",
                        "description": "ID hoặc tên máy trạm liên quan nếu có (mặc định: 'master').",
                    },
                },
                "required": ["task_description"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Phase 32.1: Strict Visual Overlay & Text Board Tool Declarations
# ---------------------------------------------------------------------------

VISUAL_OVERLAY_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "display_visual_data",
            "description": "Hiển thị một cửa sổ popup trên màn hình người dùng. CHÚ Ý: Chỉ dùng khi người dùng có nhu cầu xem dữ liệu trực quan hoặc đọc log dài.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["text_board", "network_map", "metric_chart", "security_alert", "screenshot"],
                        "description": (
                            "BẮT BUỘC CHỌN 1 TRONG CÁC LOẠI SAU:\n"
                            "- text_board: Dùng để hiển thị danh sách IP, đọc file log, hoặc đoạn text dài.\n"
                            "- network_map: Chỉ dùng khi được yêu cầu xem sơ đồ/cấu trúc mạng.\n"
                            "- metric_chart: Chỉ dùng để xem biểu đồ CPU/RAM.\n"
                            "- security_alert: Chỉ dùng khi có tấn công hoặc rủi ro thực sự.\n"
                            "- screenshot: TUYỆT ĐỐI CHỈ DÙNG khi người dùng có nhắc đến chữ 'chụp ảnh màn hình' hoặc 'xem màn hình'."
                        ),
                    },
                    "context_data": {
                        "type": "string",
                        "description": "Dữ liệu truyền vào. Nếu type là text_board, đây là chuỗi văn bản hoặc log cần hiển thị.",
                    },
                    "target_client": {
                        "type": "string",
                        "description": "ID hoặc tên máy trạm LAN cần hiển thị giao diện (mặc định: 'master').",
                    },
                    "title": {
                        "type": "string",
                        "description": "Tiêu đề của cửa sổ HUD Cyberpunk.",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Thời gian hiển thị tự động trước khi mờ dần (giây, mặc định 15s).",
                    },
                },
                "required": ["type", "context_data"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Phase 47: ERP Organization & Relational Data Query Tool
# ---------------------------------------------------------------------------

ERP_ORGANIZATION_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_organization_data",
            "description": "Tra cứu cơ sở dữ liệu tổ chức ERP của doanh nghiệp: tìm kiếm thông tin phòng ban, nhân sự (họ tên, email, SĐT, chức vụ), thiết bị/máy tính (hostname, địa chỉ IP, người sử dụng), công việc (task, hạn chót, trạng thái) và sổ sách/hồ sơ.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Từ khóa hoặc câu hỏi cần tra cứu (ví dụ: '192.168.1.10', 'Nguyễn Văn A', 'Phòng Nhân Sự', 'danh sách máy chủ').",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


class LLMEngine:
    """
    Thin-client agentic loop:
      User query → OpenAI-compatible call to 9router → Tool selection → Execution → Natural Reply
    """
    build_system_prompt = staticmethod(build_system_prompt)

    def __init__(self) -> None:
        self._client: Optional[AsyncOpenAI] = None  # Lazy init after event loop starts
        self._last_cfg_snapshot: str = self._cfg_snapshot()
        self.last_voice_display_text: str = ""

    def _cfg_snapshot(self) -> str:
        """Return a string that changes whenever llm config changes."""
        cfg = settings.llm
        return f"{cfg.base_url}|{cfg.model_name}|{cfg.api_key}"

    async def _ensure_shared_client(self) -> None:
        """
        Phase 45: Initialise the shared httpx pool once the event loop is running,
        then rebuild the AsyncOpenAI client to inject it.
        """
        await _get_shared_http_client()  # Ensure pool is created
        snap = self._cfg_snapshot()
        if self._client is None or snap != self._last_cfg_snapshot:
            cfg = settings.llm
            self._client = AsyncOpenAI(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout=45.0,
                max_retries=0,  # Zero-wait: immediately raise API errors to trigger instant auto-fallback loop
                http_client=_SHARED_HTTP_CLIENT,
            )
            self._last_cfg_snapshot = snap
            logger.info("[Phase45] AsyncOpenAI client (re)built with shared httpx pool.")

    def _get_client(self) -> AsyncOpenAI:
        """Return client synchronously (legacy). Builds fresh if needed."""
        if self._client is None:
            self._client = _make_client()
            self._last_cfg_snapshot = self._cfg_snapshot()
        snap = self._cfg_snapshot()
        if snap != self._last_cfg_snapshot:
            logger.info("[LLMEngine] Config changed — rebuilding OpenAI client.")
            self._client = _make_client()
            self._last_cfg_snapshot = snap
        return self._client

    # ------------------------------------------------------------------
    # Core LLM call
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """
        Phase 46.3: Zero-Tolerance Error Handling & Intelligent Auto-Fallback (Non-streaming).
        Loops through settings.llm.router_models.
        If a model fails with RateLimitError (429), APIError (500/503), or APIConnectionError,
        logs warning and seamlessly tries the next fallback model.
        """
        await self._ensure_shared_client()
        client = self._client  # type: ignore[assignment]

        # Phase 68: không ghi cứng tên model provider — xem giải thích ở
        # core/config_loader.py. Danh sách dự phòng do router cấp lúc lưu
        # cấu hình; rỗng thì thử đúng một model đang bật.
        DEFAULT_ROUTER: List[str] = []
        active_m = (settings.llm.model_name or "").strip()
        configured_list = getattr(settings.llm, "router_models", []) or []
        if isinstance(configured_list, str):
            configured_list = [configured_list.strip()]

        # Bỏ tên rỗng: gọi model "" chỉ tốn một vòng gọi ra ngoài rồi báo lỗi
        # chung chung, khiến thông báo lỗi khó hiểu hơn nhiều so với nói thẳng
        # là chưa cấu hình model.
        models: List[str] = [active_m] if active_m else []
        for m in configured_list:
            clean = str(m).strip()
            if clean and clean not in models:
                models.append(clean)
        for dm in DEFAULT_ROUTER:
            if dm not in models:
                models.append(dm)

        if not models:
            raise ValueError(
                "Chưa cấu hình model nào. Mở tab Quản Lý Trợ Lý AI > Bộ Não & "
                "Xử Lý Ngôn Ngữ và chọn model đang hoạt động."
            )

        last_error = None
        for model_name in models:
            try:
                kwargs: Dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "max_tokens": 2048,
                    "temperature": 0.2,  # Low temperature for deterministic RPA/command outputs
                    "stream": False,
                    # Thinking disabled: budget_tokens=0 prevents internal monologue from leaking
                    # into reply text and causing confused/repeated responses (Bug #1 fix)
                    "extra_body": {
                        "thinking": {
                            "budget_tokens": 0
                        }
                    },
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                logger.info("[LLMEngine] Đang gọi 9router → model=%s", model_name)
                t0 = time.monotonic()
                response = await client.chat.completions.create(**kwargs)
                logger.info("[LLMEngine] Phản hồi nhận được từ 9router (%s) trong %.2fs.", model_name, time.monotonic() - t0)
                self._last_successful_model = model_name
                return response

            except (openai.RateLimitError, openai.APIError, openai.APIConnectionError) as e:
                # Lỗi từ nhà cung cấp (Hết Quota 429, Sập server 503...)
                logger.warning(f"[LLM FALLBACK] Model {model_name} thất bại. Lỗi: {e}. Đang thử model dự phòng...")
                last_error = e
                continue
            except Exception as e:
                # Các lỗi bất ngờ khác
                logger.error(f"[LLM ERROR] Lỗi không xác định với model {model_name}: {e}")
                last_error = e
                continue

        # Chốt chặn cuối cùng: Nếu tất cả model đều thất bại
        raise RuntimeError(f"Tất cả model {models} đều không phản hồi: {last_error}")

    # ------------------------------------------------------------------
    # Public Agentic API
    # ------------------------------------------------------------------

    def ask(
        self,
        query: str,
        source_device: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user query through the full agentic loop (sync wrapper).
        Safely detects if an asyncio event loop is already running on current thread.

        Args:
            query: The current user query.
            source_device: Device/room label for routing context.
            history: Optional list of prior {role, content} dicts for multi-turn memory.
        """
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    lambda: asyncio.run(self.ask_async(query, source_device, history))
                )
                return future.result()
        else:
            return asyncio.run(self.ask_async(query, source_device, history))

    async def ask_async(
        self,
        query: str,
        source_device: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Async implementation of the full agentic loop.

        Args:
            query: Current user query (this turn).
            source_device: Device/room label for routing context.
            history: Prior conversation turns [{role, content}, ...] for multi-turn memory.
                     If None, automatically retrieves from MemoryManager sliding window.
            session_id: Session/user identifier for conversational memory tracking (Phase 34).
        """
        from core.plugin_manager import plugin_manager
        from core.meta_architect import meta_architect
        from core.safety_guard import security_engine

        raw_tools = plugin_manager.get_all_tools()
        # Phase 38, 40, 32.1: Guarantee Native File System, Delegation & Strict Visual Overlay tools are in available_tools
        raw_tools = [t for t in raw_tools if t.get("function", {}).get("name") != "display_visual_data"]
        for extra_t in FILE_SYSTEM_TOOLS + DELEGATION_TOOLS + VISUAL_OVERLAY_TOOLS + ERP_ORGANIZATION_TOOLS:
            raw_tools.append(extra_t)

        # ═════════════════════════════════════════════════════════════════
        # Phase 60: Plugin Registry tools (Universal Connector Hub)
        # ═════════════════════════════════════════════════════════════════
        # Trước đây registry tồn tại nhưng không hề được LLM nhìn thấy, nên
        # 12 connector tool của Phase 59 chỉ gọi được bằng tay qua
        # /skills/execute. Ở đây ta nối schema của chúng vào danh sách tool và
        # chuyển tool_call tương ứng sang `plugin_registry.execute_tool()` để
        # đi qua timeout + circuit breaker + cổng HITL theo risk_level.
        #
        # Ưu tiên: nếu một tool có mặt ở CẢ registry lẫn plugin_manager, giữ
        # bản của registry và loại bản trùng của plugin_manager — registry bọc
        # thêm timeout + circuit breaker, còn plugin_manager thì không. Giữ cả
        # hai sẽ khiến LLM thấy trùng tên tool với hai mô tả khác nhau.
        _registry_tools: List[Dict[str, Any]] = []
        _registry_names: set = set()
        _plugin_registry = None
        try:
            from core.plugin_registry import plugin_registry as _plugin_registry

            for schema in _plugin_registry.get_all_tools_schema():
                name = (schema.get("function") or {}).get("name")
                if not name:
                    continue
                _registry_tools.append(schema)
                _registry_names.add(name)
        except Exception as reg_err:  # pylint: disable=broad-except
            # Registry hỏng KHÔNG được làm sập toàn bộ hội thoại.
            logger.warning("[Phase60] Không nạp được tool Plugin Registry: %s", reg_err)

        if _registry_names:
            raw_tools = [t for t in raw_tools
                         if t.get("function", {}).get("name") not in _registry_names]

        tools = self._enrich_tools_with_target_client(raw_tools + _registry_tools)

        # Zero-Trust Data Sanitizer
        sanitized_query = security_engine.mask_sensitive_data(query)

        system_content = build_system_prompt(source_device=source_device)

        # ═════════════════════════════════════════════════════════════════════
        # Phase 25: Resumption of Pending Actions on User Confirmation / Rejection
        # ═════════════════════════════════════════════════════════════════════
        from core.state_manager import state_manager
        # Zero-Trust: caller_id là danh tính dùng cho RBAC + hàng đợi pending action.
        # KHÔNG được mặc định thành "admin" — mặc định đó biến mọi request không khai
        # báo nguồn thành full quyền. Dùng "anonymous" để _resolve_role() fail-closed
        # về 'viewer' cho tới khi có cơ chế định danh thật sự.
        caller_id = str(source_device or "anonymous")
        active_session = str(session_id or source_device or "default")
        clean_q = query.strip().lower().rstrip(".,!?")

        CONFIRM_KEYWORDS = (
            "đồng ý", "dong y", "xác nhận", "xac nhan", "ok", "chạy đi", "chay di",
            "duyệt", "duyet", "yes", "confirm", "tiến hành", "tien hanh", "chấp nhận",
            "chap nhan", "cho phép", "cho phep", "run", "approve", "y", "tiếp tục", "tiep tuc",
            "thực hiện", "thuc hien", "ok em", "duyệt đi", "chạy luôn", "xác nhận đi", "ok anh",
            "chạy đi em", "anh đồng ý", "em đồng ý", "duyệt lệnh", "cho chạy", "đã duyệt", "đã xác nhận",
            "ấn xác nhận", "bấm xác nhận", "đã phê duyệt", "phê duyệt rồi", "duyệt rồi", "đã ấn", "đã bấm"
        )
        REJECT_KEYWORDS = (
            "hủy", "huy", "hủy bỏ", "huy bo", "không", "khong", "từ chối", "tu choi",
            "cancel", "no", "dừng", "dung", "dừng lại", "dung lai", "reject", "n", "thôi", "thoi",
            "thôi khỏi", "bỏ qua", "bo qua"
        )

        has_confirm_intent = any(k in clean_q for k in (
            "đồng ý", "dong y", "xác nhận", "xac nhan", "phê duyệt", "phe duyet",
            "chấp nhận", "chap nhan", "cho phép", "cho phep", "tiến hành", "tien hanh",
            "chạy đi", "duyệt đi", "đã duyệt", "duyệt rồi",
            "đã xác nhận", "xác nhận rồi", "ấn xác nhận", "bấm xác nhận"
            # NOTE: "tiếp tục" / "tiep tuc" removed — too ambiguous in general conversation
        ))
        has_negative_intent = any(k in clean_q for k in (
            "không", "khong", "hủy", "huy", "đừng", "dung", "chưa", "chua", "từ chối", "tu choi"
        ))
        # Bug #3 fix: Only treat as confirm when query is short (<= 35 chars).
        # Long sentences containing these words are likely general conversation, not approval.
        _is_short_command = len(clean_q) <= 35
        is_confirm = (
            clean_q in CONFIRM_KEYWORDS
            or any(clean_q.startswith(k) or clean_q.endswith(k) for k in CONFIRM_KEYWORDS)
            or (_is_short_command and has_confirm_intent and not has_negative_intent)
        )
        is_reject = clean_q in REJECT_KEYWORDS or any(clean_q.startswith(k) for k in REJECT_KEYWORDS)

        if is_confirm:
            pending = state_manager.get_and_clear_pending_action(caller_id)
            if not pending:
                all_p = state_manager.list_pending_actions()
                if all_p:
                    pending = state_manager.get_and_clear_pending_action(all_p[0].get("id") or "admin")

            if pending:
                tool_name = pending.get("tool_name", "")
                args = dict(pending.get("arguments", {}))
                args["confirmed"] = True
                target_client = pending.get("target_client", "master")
                orig_query = pending.get("query", "")

                logger.info(
                    "[Phase 25] Resuming pending action '%s' on '%s' for caller '%s' (orig_query='%s')",
                    tool_name, target_client, caller_id, orig_query,
                )
                security_engine.log_audit(target_client, tool_name, "NEED_CONFIRM", "USER_APPROVED", args)

                # Execute skill
                if target_client.lower() in ("master", "local", "server", "chính", "cục bộ"):
                    tool_res = await asyncio.to_thread(plugin_manager.execute_skill, tool_name, args)
                    is_not_found = (
                        (not tool_res.get("success", True) or tool_res.get("status") == "error")
                        and ("not found in registry" in str(tool_res.get("error", "")).lower()
                             or "không tìm thấy" in str(tool_res.get("error", "")).lower())
                    )
                    if is_not_found:
                        if tool_name in ("list_directory", "read_file", "write_file", "delete_item"):
                            from core.skills import file_system
                            fs_fn = getattr(file_system, tool_name, None)
                            if fs_fn:
                                tool_res = await asyncio.to_thread(fs_fn, **args)
                        elif tool_name == "delegate_to_specialist":
                            from core.skills import ai_delegation
                            tool_res = await ai_delegation.delegate_to_specialist_async(**args)
                        elif tool_name == "display_visual_data":
                            from skills.visual_skills import display_visual_data
                            tool_res = await asyncio.to_thread(display_visual_data, **args)
                        elif tool_name == "query_organization_data":
                            from core.database import erp_db
                            tool_res = {"status": "success", "data": erp_db.query_organization(args.get("query", ""))}
                else:
                    from core.orchestrator import orchestrator
                    tool_res = await orchestrator.execute_on_client(target_client, tool_name, args)

                tool_res_str = json.dumps(tool_res, ensure_ascii=False, default=str)
                masked_res = security_engine.mask_sensitive_data(tool_res_str)

                # Synthesize natural response for the original query
                synth_messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": orig_query or f"Thực hiện tác vụ {tool_name}"},
                    {"role": "assistant", "content": f"Tác vụ '{tool_name}' đã được phê duyệt. Em đang tiến hành thực thi..."},
                    {
                        "role": "user",
                        "content": (
                            f"Người dùng vừa xác nhận: '{query}'.\n"
                            f"Hệ thống đã thực thi công cụ `{tool_name}` và nhận được kết quả:\n"
                            f"```json\n{masked_res}\n```\n\n"
                            f"Dựa vào kết quả trên, hãy trả lời tự nhiên, rõ ràng bằng tiếng Việt để giải đáp chính xác và đầy đủ yêu cầu ban đầu: '{orig_query}'."
                        ),
                    },
                ]

                try:
                    synth_resp = await self._call_llm(messages=synth_messages, tools=None)
                    reply_text = synth_resp.choices[0].message.content or "Em đã thực thi xong tác vụ."
                except Exception as e:
                    logger.warning("[Phase 25] Lỗi synthesize câu trả lời: %s", e)
                    reply_text = f"Đã thực thi thành công tác vụ '{tool_name}'. Kết quả: {tool_res_str[:400]}"

                display_text, speech_text = self._extract_dual_channel(reply_text)
                state_manager.record_completed_action(pending, tool_res, display_text)
                from core.memory_manager import memory_manager
                memory_manager.add_turn(active_session, query, display_text)

                return {
                    "reply": display_text,
                    "speech_reply": speech_text,
                    "tool_calls_made": [{
                        "skill": tool_name,
                        "target_client": target_client,
                        "args": args,
                        "result": tool_res,
                    }],
                    "success": True,
                    "route_info": {"model": settings.llm.model_name},
                }

            # If no pending action, but user confirmed/asked about approval, check recently completed actions
            recent_done = state_manager.get_recent_completed_action(caller_id, max_age=1800.0)
            if recent_done:
                c_tool = recent_done.get("tool_name", "")
                c_query = recent_done.get("query", "")
                c_res = security_engine.mask_sensitive_data(json.dumps(recent_done.get("result"), ensure_ascii=False, default=str))
                c_reply = recent_done.get("reply", "")
                logger.info("[Phase 25] User confirmed/mentioned approval, found recently completed action '%s'", c_tool)
                synth_messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": c_query or f"Thực hiện tác vụ {c_tool}"},
                    {"role": "assistant", "content": c_reply or f"Tác vụ '{c_tool}' đã được phê duyệt."},
                    {
                        "role": "user",
                        "content": (
                            f"Người dùng vừa nhắn: '{query}'.\n"
                            f"Hệ thống xác nhận: Tác vụ `{c_tool}` cho yêu cầu '{c_query}' ĐÃ ĐƯỢC PHÊ DUYỆT và thực thi thành công trước đó.\n"
                            f"Kết quả thực thi:\n```json\n{c_res}\n```\n\n"
                            f"Hãy trả lời tự nhiên, rõ ràng bằng tiếng Việt để xác nhận với người dùng rằng tác vụ đã hoàn thành, đồng thời tóm tắt kết quả chính cho họ."
                        ),
                    },
                ]
                try:
                    synth_resp = await self._call_llm(messages=synth_messages, tools=None)
                    reply_text = synth_resp.choices[0].message.content or c_reply
                except Exception:
                    reply_text = c_reply or f"Tác vụ '{c_tool}' đã được phê duyệt và hoàn tất thành công."
                display_text, speech_text = self._extract_dual_channel(reply_text)
                from core.memory_manager import memory_manager
                memory_manager.add_turn(active_session, query, display_text)
                return {
                    "reply": display_text,
                    "speech_reply": speech_text,
                    "tool_calls_made": [{
                        "skill": c_tool,
                        "target_client": recent_done.get("target_client", "master"),
                        "args": recent_done.get("arguments", {}),
                        "result": recent_done.get("result"),
                    }],
                    "success": True,
                    "route_info": {"model": settings.llm.model_name},
                }

        if is_reject:
            pending = state_manager.get_and_clear_pending_action(caller_id)
            if not pending:
                all_p = state_manager.list_pending_actions()
                if all_p:
                    pending = state_manager.get_and_clear_pending_action(all_p[0].get("id") or "admin")

            if pending:
                tool_name = pending.get("tool_name", "")
                target_client = pending.get("target_client", "master")
                security_engine.log_audit(target_client, tool_name, "NEED_CONFIRM", "USER_REJECTED", pending.get("arguments", {}))
                logger.info("[Phase 25] Người dùng '%s' hủy bỏ tác vụ '%s'", caller_id, tool_name)
                rej_msg = f"Dạ, em đã hủy bỏ tác vụ '{tool_name}' theo yêu cầu của bạn."
                from core.memory_manager import memory_manager
                memory_manager.add_turn(active_session, query, rej_msg)
                return {
                    "reply": rej_msg,
                    "speech_reply": rej_msg,
                    "tool_calls_made": [],
                    "success": True,
                    "route_info": {"model": settings.llm.model_name},
                }

        # Bug #10 fix: Inject recent completed action context ONLY when relevant.
        # max_age reduced from 1800s (30 min) to 300s (5 min) to prevent stale context noise.
        # Also guard: only inject if query appears related to the completed action.
        recent_completed = state_manager.get_recent_completed_action(caller_id, max_age=300.0)
        if recent_completed:
            c_tool = recent_completed.get("tool_name", "")
            c_query = recent_completed.get("query", "")
            c_res_str = security_engine.mask_sensitive_data(json.dumps(recent_completed.get("result"), ensure_ascii=False, default=str))[:600]
            # Only inject if user is likely asking about it (has result/status/approval keywords)
            _result_keywords = ("kết quả", "xong chưa", "thế nào", "hoàn thành", "đã làm", "ra sao",
                                "hoàn tất", "tình trạng", "trạng thái", "báo cáo", "thực thi")
            _query_lower = sanitized_query.lower()
            _is_relevant = any(kw in _query_lower for kw in _result_keywords) or (c_tool and c_tool.lower() in _query_lower)
            if _is_relevant:
                system_content += (
                    f"\n\n[TÁC VỤ VỪA ĐƯỢC PHÊ DUYỆT VÀ THỰC THI GẦN ĐÂY]\n"
                    f"- Công cụ: {c_tool}\n"
                    f"- Yêu cầu gốc: '{c_query}'\n"
                    f"- Kết quả thực thi tóm tắt: {c_res_str}\n"
                    f"Nếu người dùng hỏi về tiến độ, kết quả hoặc nhắc đến việc đã phê duyệt, hãy giải thích rõ ràng dựa trên kết quả này."
                )

        # Build messages: system → history (prior turns from memory_manager) → current user
        from core.memory_manager import memory_manager
        effective_history = history if history is not None else memory_manager.get_history(active_session)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        if effective_history:
            # Phase 34: Sliding Window of last 14 messages (7 turns) to conserve tokens and prevent drift
            messages.extend(effective_history[-14:])
        messages.append({"role": "user", "content": sanitized_query})

        tool_calls_made: List[Dict[str, Any]] = []
        synthesised_this_turn: bool = False
        used_model: str = settings.llm.model_name

        for round_idx in range(MAX_TOOL_ROUNDS):
            logger.debug("LLM round %d — messages=%d, tools=%d", round_idx, len(messages), len(tools))

            try:
                response = await self._call_llm(
                    messages=messages,
                    tools=tools if tools else None,
                )
                used_model = getattr(self, "_last_successful_model", None) or settings.llm.model_name
            except Exception as exc:
                logger.error("[LLMEngine] [CHỐT CHẶN CUỐI CÙNG] Toàn bộ model dự phòng đều thất bại: %s", exc)
                fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
                return {
                    "reply": fallback_msg,
                    "speech_reply": fallback_msg,
                    "tool_calls_made": tool_calls_made,
                    "success": False,
                    "error": "ALL_MODELS_FAILED",
                    "route_info": {"model": "fallback"},
                }

            choice = response.choices[0]
            assistant_msg = choice.message
            finish_reason = getattr(choice, "finish_reason", "stop") or "stop"

            # Format assistant turn for history
            assistant_dict: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_msg.content or "",
            }
            if getattr(assistant_msg, "tool_calls", None):
                assistant_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": getattr(tc, "type", "function"),
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_msg.tool_calls
                ]
            messages.append(assistant_dict)

            # ---- Case 1: LLM wants to execute tool calls ----
            if finish_reason == "tool_calls" and getattr(assistant_msg, "tool_calls", None):
                for tool_call in assistant_msg.tool_calls:
                    fn_name: str = tool_call.function.name
                    try:
                        fn_args: Dict[str, Any] = (
                            json.loads(tool_call.function.arguments)
                            if isinstance(tool_call.function.arguments, str)
                            else (tool_call.function.arguments or {})
                        )
                    except json.JSONDecodeError:
                        fn_args = {}

                    # Extract target_client parameter (support both target_client and target_client_id)
                    target_client = str(
                        fn_args.pop("target_client_id", None)
                        or fn_args.pop("target_client", "master")
                        or "master"
                    ).strip()

                    # Phase 38 Zero-Trust Action Risk Evaluation
                    from core.zero_trust import evaluate_action_risk as zt_evaluate_risk
                    risk_level = zt_evaluate_risk(fn_name, fn_args)

                    if risk_level == "BLOCKED":
                        logger.warning("Zero-Trust Security: Tác vụ '%s' bị CHẶN HOÀN TOÀN.", fn_name)
                        security_engine.log_audit(target_client, fn_name, "BLOCKED", "REJECTED", fn_args)
                        result = {
                            "status": "error",
                            "message": f"Tác vụ '{fn_name}' bị từ chối do vi phạm chính sách bảo mật hệ thống (Blacklist).",
                        }
                    elif risk_level == "NEED_CONFIRM" and not fn_args.get("confirmed"):
                        logger.warning("Zero-Trust Security: Tác vụ '%s' yêu cầu người quản trị phê duyệt.", fn_name)
                        security_engine.log_audit(target_client, fn_name, "NEED_CONFIRM", "PENDING_CONFIRMATION", fn_args)

                        # Phase 25: Save pending action into StateManager queue
                        from core.state_manager import state_manager
                        caller_id = str(source_device or "anonymous")
                        chat_id = None
                        if source_device and "telegram:" in str(source_device):
                            parts = str(source_device).split(":")
                            if len(parts) >= 2:
                                chat_id = parts[1]

                        state_manager.save_pending_action(
                            user_id=caller_id,
                            tool_name=fn_name,
                            arguments=dict(fn_args),
                            target_client=target_client,
                            query=query,
                            chat_id=chat_id,
                            source_device=source_device,
                        )

                        result = {
                            "status": "need_confirm",
                            "message": f"Tác vụ '{fn_name}' yêu cầu phê duyệt bảo mật. Vui lòng bấm Xác nhận trên màn hình hoặc nhắn 'Đồng ý' để em chạy tiếp.",
                            "skill": fn_name,
                            "target_client": target_client,
                            "args": fn_args,
                            "requires_confirmation": True,
                        }
                    else:
                        # Phase 48: RBAC Permission Check
                        if _rbac_guard is not None:
                            _rbac_allowed, _rbac_reason = _rbac_guard.check_permission(
                                tool_name=fn_name,
                                employee_id=caller_id,
                                session_id=getattr(assistant_msg, "id", None),
                                payload=fn_args,
                            )
                            if not _rbac_allowed:
                                logger.warning(
                                    "[Phase48-RBAC] BLOCKED | tool=%s | caller=%s | reason=%s",
                                    fn_name, caller_id, _rbac_reason,
                                )
                                result = {
                                    "status": "error",
                                    "error": _rbac_reason,
                                    "code": "RBAC_DENIED",
                                }
                                tool_calls_made.append({
                                    "skill": fn_name,
                                    "target_client": target_client,
                                    "args": fn_args,
                                    "result": result,
                                })
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "content": json.dumps(result, ensure_ascii=False),
                                })
                                continue  # skip to next tool call

                        # SAFE or already CONFIRMED
                        if target_client.lower() in ("master", "local", "server", "chính", "cục bộ"):
                            # Phase 60: tool thuộc Plugin Registry (connector
                            # ngoại vi) đi qua timeout + circuit breaker + HITL.
                            if _registry_names and fn_name in _registry_names:
                                logger.info("[Phase60] Thực thi tool Plugin Registry: '%s'", fn_name)
                                result = await _plugin_registry.execute_tool(
                                    fn_name, fn_args, caller_id=caller_id
                                )
                                if result.get("awaiting_approval"):
                                    # Phải báo rõ cho LLM là CHƯA chạy, để nó
                                    # không kể cho người dùng là đã xong.
                                    result = {
                                        "status": "awaiting_approval",
                                        "success": False,
                                        "approval_id": result.get("approval_id"),
                                        "risk_level": result.get("risk_level"),
                                        "message": (
                                            "Tác vụ này rủi ro cao và đang chờ quản trị "
                                            "viên phê duyệt. CHƯA được thực thi."
                                        ),
                                    }
                            else:
                                logger.info("Thực thi kỹ năng cục bộ trên Master: '%s' tham số=%s", fn_name, fn_args)
                                result = await plugin_manager.execute_skill(fn_name, fn_args)
                                is_not_found = (
                                    (not result.get("success", True) or result.get("status") == "error")
                                    and ("not found in registry" in str(result.get("error", "")).lower()
                                         or "không tìm thấy" in str(result.get("error", "")).lower())
                                )
                                if is_not_found:
                                    if fn_name in ("list_directory", "read_file", "write_file", "delete_item"):
                                        from core.skills import file_system
                                        fs_fn = getattr(file_system, fn_name, None)
                                        if fs_fn:
                                            result = fs_fn(**fn_args)
                                    elif fn_name == "delegate_to_specialist":
                                        from core.skills import ai_delegation
                                        result = await ai_delegation.delegate_to_specialist_async(**fn_args)
                                    elif fn_name == "display_visual_data":
                                        from skills.visual_skills import display_visual_data
                                        result = display_visual_data(**fn_args)
                                    elif fn_name == "query_organization_data":
                                        from core.database import erp_db
                                        result = {"status": "success", "data": erp_db.query_organization(fn_args.get("query", ""))}
                        else:
                            logger.info("Điều phối kỹ năng '%s' tới máy trạm LAN [%s] tham số=%s", fn_name, target_client, fn_args)
                            from core.orchestrator import orchestrator
                            result = orchestrator.execute_on_client_sync(target_client, fn_name, fn_args)

                        is_success = (
                            result.get("status") == "success"
                            or result.get("success") is True
                        )
                        audit_status = "SUCCESS" if is_success else "FAILED"
                        security_engine.log_audit(target_client, fn_name, risk_level, audit_status, fn_args)

                    tool_calls_made.append({
                        "skill": fn_name,
                        "target_client": target_client,
                        "args": fn_args,
                        "result": result,
                    })

                    # Mask tool result before feeding back to cloud LLM context
                    raw_result_str = json.dumps(result, ensure_ascii=False, default=str)
                    masked_result_str = security_engine.mask_sensitive_data(raw_result_str)

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": masked_result_str,
                        }
                    )
                # Continue next round so LLM can synthesize tool results
                continue

            # ---- Case 2: LLM finished naturally (stop / end_turn) ----
            if finish_reason in ("stop", "end_turn", "length", None):
                reply_text: str = assistant_msg.content or ""

                # Check if LLM indicates absence of skill → trigger MetaArchitect
                if (
                    not synthesised_this_turn
                    and not tool_calls_made
                    and self._needs_synthesis(reply_text)
                ):
                    logger.info("LLM phản hồi chưa có công cụ phù hợp; kích hoạt MetaArchitect tự tạo kỹ năng mới...")
                    synthesised_this_turn = True
                    new_skill_name = self._synthesise_and_install(
                        query, meta_architect, plugin_manager
                    )
                    if new_skill_name:
                        raw_tools = plugin_manager.get_all_tools()
                        raw_tools = [t for t in raw_tools if t.get("function", {}).get("name") != "display_visual_data"]
                        for extra_t in FILE_SYSTEM_TOOLS + DELEGATION_TOOLS + VISUAL_OVERLAY_TOOLS + ERP_ORGANIZATION_TOOLS:
                            raw_tools.append(extra_t)
                        tools = self._enrich_tools_with_target_client(raw_tools)
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    f"[HỆ THỐNG] Kỹ năng mới '{new_skill_name}' vừa được cài đặt tự động. "
                                    "Hãy gọi công cụ này ngay để hoàn thành yêu cầu của người dùng."
                                ),
                            }
                        )
                        continue

                has_need_confirm = any(
                    tc.get("result", {}).get("status") == "need_confirm"
                    for tc in tool_calls_made
                )
                # Phase 34: Dual-Channel Output Separation & Sliding Memory Storage
                display_text, speech_text = self._extract_dual_channel(reply_text)
                from core.memory_manager import memory_manager
                memory_manager.add_turn(active_session, query, display_text)

                return {
                    "reply": display_text,
                    "speech_reply": speech_text,
                    "tool_calls_made": tool_calls_made,
                    "success": True,
                    "error": None,
                    "route_info": {"model": used_model},
                    "requires_confirmation": has_need_confirm,
                }

        # Exceeded MAX_TOOL_ROUNDS
        return {
            "reply": "Đã hoàn thành các bước tác vụ nhưng đã đạt giới hạn vòng lặp xử lý.",
            "tool_calls_made": tool_calls_made,
            "success": False,
            "error": "MAX_TOOL_ROUNDS exceeded",
            "route_info": {"model": used_model},
        }

    def process_voice_command_sync(
        self,
        text: str,
        source_device: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        TTS-optimised synchronous entry point for audio pipeline.
        Runs the full agentic loop and returns a clean Vietnamese string for edge-tts.

        Args:
            text: Transcribed user speech.
            source_device: Device/room label for routing context.
            history: Prior conversation turns for multi-turn continuity.
        """
        result = self.ask(text, source_device=source_device, history=history)
        raw_reply: str = result.get("reply", "")

        reply = self._sanitise_for_tts(raw_reply)

        if not reply:
            if result.get("success"):
                return "Em đã thực hiện xong yêu cầu của bạn."
            else:
                error = result.get("error", "")
                return f"Xin lỗi, em gặp lỗi khi thực hiện: {error[:80]}" if error else "Em không thể thực hiện yêu cầu này."

        return reply

    async def process_voice_command(
        self,
        text: str,
        source_device: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Async entry point for voice pipeline — avoids blocking the event loop.

        Args:
            text: Transcribed user speech.
            source_device: Device/room label for routing context.
            history: Prior conversation turns for multi-turn continuity.
        """
        result = await self.ask_async(text, source_device=source_device, history=history)
        raw_reply: str = result.get("reply", "")
        reply = self._sanitise_for_tts(raw_reply)

        if not reply:
            if result.get("success"):
                return "Em đã thực hiện xong yêu cầu của bạn."
            else:
                error = result.get("error", "")
                return f"Xin lỗi, em gặp lỗi: {error[:80]}" if error else "Em không thể thực hiện yêu cầu này."

        return reply

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        stream: bool = False,
        tools: Optional[List[Dict[str, Any]]] = None,
        source_device: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        Chat completion async entrypoint.
        Extracts user query and delegates to the agentic loop.
        """
        user_query = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_query = m.get("content", "")
                break

        if user_query:
            return await self.process_voice_command(user_query, source_device=source_device)

        response = await self._call_llm(messages=messages, tools=tools)
        content = getattr(response.choices[0].message, "content", "") or ""
        return str(content).strip()

    async def generate_response(self, prompt: str, **kwargs: Any) -> str:
        """Helper alias for single-prompt text generation."""
        return await self.chat(messages=[{"role": "user", "content": prompt}], **kwargs)

    async def report_action_execution(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: Dict[str, Any],
        source_device: Optional[str] = None,
    ) -> str:
        """
        Phase 25: Generate natural Vietnamese completion report after user confirms and runs a pending action.
        """
        status = result.get("status", "success")
        msg = result.get("message") or result.get("output") or result.get("data") or ""
        error = result.get("error") or ""

        # Quick fallback / prompt LLM for natural report
        raw_res = json.dumps(result, ensure_ascii=False, default=str)
        prompt = (
            f"Người dùng vừa xác nhận phê duyệt và hệ thống đã thực thi xong tác vụ sau:\n"
            f"- Tên tác vụ: {tool_name}\n"
            f"- Tham số: {json.dumps(args, ensure_ascii=False)}\n"
            f"- Kết quả thực thi: {raw_res[:800]}\n\n"
            f"Hãy trả lời người dùng bằng 1-2 câu tiếng Việt ngắn gọn, lịch sự, thông báo tác vụ đã hoàn thành thành công và tóm tắt kết quả."
        )
        try:
            summary = await self.generate_response(prompt)
            if summary and len(summary.strip()) > 5:
                return summary.strip()
        except Exception as exc:
            logger.warning("[LLMEngine] Failed to generate AI execution summary: %s", exc)

        if status == "success":
            return f"✅ Tác vụ '{tool_name}' đã được phê duyệt và thực thi thành công! Kết quả: {msg or 'Hoàn tất.'}"
        else:
            return f"⚠️ Tác vụ '{tool_name}' đã chạy sau khi xác nhận nhưng gặp lỗi: {error or msg or 'Không thành công.'}"

    async def stream_voice_response(
        self,
        query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        source_device: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Phase 23: Stream LLM response sentence-by-sentence for voice TTS pipeline.

        Unlike ask_async() which waits for the full response, this method:
          1. Opens a streaming connection to 9router (stream=True).
          2. Buffers incoming tokens and yields complete sentences as soon as
             a sentence-ending punctuation is encountered.
          3. If the stream contains tool_calls, cancels streaming and falls back
             to the full ask_async() agentic loop, yielding the final reply as
             a single sentence.

        Yields:
            Complete sentences (str), TTS-sanitised, ready for audio synthesis.

        NOTE: This method does NOT affect ask_async(), chat(), or any other
        existing callers. It is exclusively used by VoiceController.
        """
        from core.safety_guard import security_engine

        sanitized_query = security_engine.mask_sensitive_data(query)

        system_content = build_system_prompt(source_device=source_device)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        if history:
            messages.extend(history[-20:])
        messages.append({"role": "user", "content": sanitized_query})

        # Phase 45: Use shared connection pool for streaming (eliminates TLS handshake)
        await self._ensure_shared_client()
        client = self._client  # type: ignore[assignment]
        model = settings.llm.model_name

        from core.plugin_manager import plugin_manager
        raw_tools = plugin_manager.get_all_tools()
        raw_tools = [t for t in raw_tools if t.get("function", {}).get("name") != "display_visual_data"]
        for extra_t in FILE_SYSTEM_TOOLS + DELEGATION_TOOLS + VISUAL_OVERLAY_TOOLS + ERP_ORGANIZATION_TOOLS:
            raw_tools.append(extra_t)
        tools = self._enrich_tools_with_target_client(raw_tools)

        # Phase 46.3: Auto-fallback loop for streaming connection
        # Phase 68: không ghi cứng tên model provider — xem giải thích ở
        # core/config_loader.py. Danh sách dự phòng do router cấp lúc lưu
        # cấu hình; rỗng thì thử đúng một model đang bật.
        DEFAULT_ROUTER: List[str] = []
        active_m = (settings.llm.model_name or "").strip()
        configured_list = getattr(settings.llm, "router_models", []) or []
        if isinstance(configured_list, str):
            configured_list = [configured_list.strip()]

        models: List[str] = [active_m] if active_m else []
        for m in configured_list:
            clean = str(m).strip()
            if clean and clean not in models:
                models.append(clean)
        for dm in DEFAULT_ROUTER:
            if dm not in models:
                models.append(dm)

        if not models:
            raise ValueError(
                "Chưa cấu hình model nào. Mở tab Quản Lý Trợ Lý AI > Bộ Não & "
                "Xử Lý Ngôn Ngữ và chọn model đang hoạt động."
            )

        stream = None
        used_model = None
        t_start = time.monotonic()
        first_token_logged = False

        for model_name in models:
            try:
                logger.info("[LLMEngine] stream_voice_response → model=%s (stream=True)", model_name)
                stream = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    tools=tools if tools else None,
                    max_tokens=1024,
                    temperature=0.7,
                    stream=True,
                    extra_body={"thinking": {"budget_tokens": 0}},  # Disable thinking for speed
                )
                used_model = model_name
                break
            except (openai.RateLimitError, openai.APIError, openai.APIConnectionError) as e:
                # Lỗi từ nhà cung cấp (Hết Quota 429, Sập server 503...)
                logger.warning(f"[LLM FALLBACK] Streaming model {model_name} thất bại. Lỗi: {e}. Đang thử model dự phòng...")
                continue
            except Exception as e:
                # Các lỗi bất ngờ khác
                logger.error(f"[LLM ERROR] Streaming lỗi không xác định với model {model_name}: {e}")
                continue

        # Chốt chặn cuối cùng nếu tất cả model đều thất bại khi mở stream
        if stream is None:
            fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
            self.last_voice_display_text = fallback_msg
            yield fallback_msg
            return

        text_buffer = ""
        has_tool_calls = False
        has_yielded_any_sentence = False

        try:
            async for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # Phát hiện tool call — huỷ stream, chuyển sang vòng lặp agentic.
                #
                # Phase 67: bỏ phát lời đệm ở đây. Trước đây chỗ này tự phát
                # một câu đệm, trong khi `_process_hud_voice_command` đã phát
                # câu khác — nghe hai lần "em đang xử lý" rồi mới tới kết quả.
                # Giờ lời đệm do phía gọi quyết định, vì chỉ phía gọi mới biết
                # câu trả lời thật có về kịp trước ngưỡng chờ hay không.
                if getattr(delta, "tool_calls", None):
                    has_tool_calls = True
                    _wasted = time.monotonic() - t_start
                    detected_tools = [
                        getattr(getattr(tc, "function", None), "name", "") or ""
                        for tc in delta.tool_calls
                    ]
                    logger.info(
                        "[LLMEngine] Phát hiện tool call %s sau %.2fs — chuyển sang "
                        "vòng lặp agentic (phần thời gian chờ trên KHÔNG mất: "
                        "model suy luận lại từ đầu với tool mới).",
                        detected_tools, _wasted,
                    )
                    break

                token = getattr(delta, "content", "") or ""
                if not token:
                    continue

                if not first_token_logged:
                    logger.info(
                        "[LLMEngine] First token received in %.2fs (%s).",
                        time.monotonic() - t_start, used_model,
                    )
                    first_token_logged = True

                text_buffer += token

                # Flush complete sentences
                sentences, text_buffer = self._extract_sentences(text_buffer)
                for sentence in sentences:
                    clean = self._sanitise_for_tts(sentence)
                    if clean:
                        has_yielded_any_sentence = True
                        yield clean

        except Exception as exc:
            logger.error("[LLMEngine] Streaming error mid-response (ngắt an toàn): %s", exc)
            # Ngắt an toàn khi kết nối bị đứt giữa chừng lúc đang stream:
            # Nếu chưa yield được câu nào, gửi câu fallback nhẹ nhàng.
            # Nếu đã stream được một phần câu, dừng an toàn mà KHÔNG ném lỗi JSON.
            if not has_yielded_any_sentence and not text_buffer.strip():
                fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
                self.last_voice_display_text = fallback_msg
                yield fallback_msg
                return

        # Yield remaining buffer
        if text_buffer.strip() and not has_tool_calls:
            clean = self._sanitise_for_tts(text_buffer)
            if clean:
                has_yielded_any_sentence = True
                yield clean

        # Save streamed conversation to MemoryManager
        if not has_tool_calls and text_buffer.strip():
            self.last_voice_display_text = text_buffer.strip()
            try:
                from core.memory_manager import memory_manager as _mm
                _session = str(source_device or "voice")
                _mm.add_turn(_session, query, self._sanitise_for_tts(text_buffer))
            except Exception:
                pass

        # If tool calls detected, fall back to full agentic loop
        if has_tool_calls:
            logger.info("[LLMEngine] Executing full agentic loop for tool call...")
            try:
                result = await self.ask_async(
                    query=query,
                    source_device=source_device,
                    history=history,
                )
                self.last_voice_display_text = result.get("reply", "")
                speech_reply = result.get("speech_reply") or self._sanitise_for_tts(self.last_voice_display_text)
                if speech_reply:
                    yield speech_reply
                elif result.get("success"):
                    yield "Em đã thực hiện xong yêu cầu của bạn."
                else:
                    yield "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
            except Exception as exc:
                logger.error("[LLMEngine] Agentic fallback error: %s", exc)
                fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
                self.last_voice_display_text = fallback_msg
                yield fallback_msg

    @staticmethod
    def _extract_sentences(text: str) -> tuple[list[str], str]:
        """
        Phase 50 Ultra-Low Latency Sentence Chunking:
        1. Breaks on punctuation: ',', '.', '!', '?', '\n', ';', ':', '—'.
        2. If buffer exceeds 10-12 words without punctuation, aggressively cuts at word boundary
           so Edge-TTS can begin synthesis within < 200ms without waiting for a full sentence.

        Returns:
            (list_of_complete_sentences, remaining_buffer)
        """
        import re
        if not text:
            return [], ""

        # Break on commas, periods, questions, exclamations, colons, semicolons, em-dash, newlines
        boundary_pattern = re.compile(r'([^,.;:!?\n\—]+[,.;:!?\n\—])')

        raw_parts = []
        remaining = text

        while True:
            m = boundary_pattern.search(remaining)
            if not m:
                break
            part = remaining[:m.end()].strip()
            remaining = remaining[m.end():].lstrip()
            if part:
                raw_parts.append(part)

        sentences: list[str] = []
        for part in raw_parts:
            # Yield any clean clause with at least 4 characters
            if len(part.strip()) >= 4:
                sentences.append(part.strip())

        # Phase 50 Step 3.3: If remaining buffer is long (>= 10 words) without punctuation,
        # aggressively cut at word boundary so Edge-TTS can synthesize chunk 1 immediately!
        words = remaining.split()
        if len(words) >= 10:
            cut_idx = 10 if len(words) >= 12 else 8
            first_chunk = " ".join(words[:cut_idx]).strip()
            remaining = " ".join(words[cut_idx:]).strip()
            if first_chunk:
                sentences.append(first_chunk)

        return sentences, remaining



    @staticmethod
    def _enrich_tools_with_target_client(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Inject target_client parameter into every tool schema for LLM routing."""
        enriched: List[Dict[str, Any]] = []
        for t in tools:
            try:
                t_copy = json.loads(json.dumps(t))
                fn = t_copy.get("function", {})
                params = fn.get("parameters", {})
                if isinstance(params, dict) and "properties" in params:
                    params["properties"]["target_client"] = {
                        "type": "string",
                        "description": "Tên máy tính đích trong mạng LAN cần thực thi lệnh (ví dụ: 'master', 'PC-KETOAN-01', 'DESKTOP-ABC'). Mặc định là 'master'.",
                    }
                    params["properties"]["target_client_id"] = {
                        "type": "string",
                        "description": "ID hoặc bí danh máy trạm đích trong mạng LAN nếu có.",
                    }
                enriched.append(t_copy)
            except Exception:
                enriched.append(t)
        return enriched

    @staticmethod
    def _sanitise_for_tts(text: str) -> str:
        """
        Remove characters and patterns that sound unnatural when spoken by edge-tts.
        """
        import re
        # Remove fenced code blocks
        text = re.sub(r"```[\s\S]*?```", "", text)
        # Remove inline code
        text = re.sub(r"`[^`]+`", "", text)
        # Remove markdown bold/italic
        text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
        # Remove leading bullet/dash per line
        text = re.sub(r"^\s*[-•*]\s+", "", text, flags=re.MULTILINE)
        # Collapse multiple newlines into space
        text = re.sub(r"\n+", " ", text)
        # Remove JSON fragments
        text = re.sub(r"\{[^}]{0,200}\}", "", text)
        # Collapse excess whitespace
        text = re.sub(r" {2,}", " ", text).strip()
        return text

    @classmethod
    def _make_concise_speech_text(cls, text: str) -> str:
        """
        Phase 34: Tự động tổng hợp một câu nói tự nhiên, ngắn gọn từ văn bản chứa bảng biểu/code.
        """
        import re
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        intro_parts = []
        for line in lines:
            if line.startswith(("#", "|", "```", "---", "<", "•", "-", "*")):
                break
            if len(line) > 5 and not line.startswith(("{", "[")):
                intro_parts.append(line)

        if intro_parts:
            intro_str = cls._sanitise_for_tts(" ".join(intro_parts[:2]))
            if len(intro_str) > 10:
                if not any(intro_str.endswith(p) for p in (".", "!", "?")):
                    intro_str += "."
                return f"{intro_str} Chi tiết cụ thể đã được hiển thị trên màn hình của bạn."

        # Fallback sanitisation
        cleaned = cls._sanitise_for_tts(text)
        if len(cleaned) > 220:
            sentences = re.split(r"(?<=[.!?])\s+", cleaned)
            accum = []
            for s in sentences:
                if sum(len(x) for x in accum) + len(s) < 200:
                    accum.append(s)
                else:
                    break
            if accum:
                return " ".join(accum) + " Chi tiết đã được hiển thị trên màn hình."
            return cleaned[:180] + "..."
        return cleaned

    @classmethod
    def _extract_dual_channel(cls, text: str) -> Tuple[str, str]:
        """
        Phase 34: Dual-Channel Output Separation.
        Tách biệt văn phong hiển thị (chi tiết, Markdown) và văn phong nói (ngắn gọn, tự nhiên).
        Returns: (display_text, speech_text)
        """
        import re
        if not text:
            return "", ""

        # 1. Thẻ quy chuẩn: <!--VOICE: ... -->
        voice_comment = re.search(r"<!--\s*VOICE:\s*([\s\S]*?)\s*-->", text, re.IGNORECASE)
        if voice_comment:
            speech_raw = voice_comment.group(1).strip()
            display_text = re.sub(r"<!--\s*VOICE:\s*[\s\S]*?\s*-->", "", text).strip()
            speech_text = cls._sanitise_for_tts(speech_raw)
            return display_text, speech_text

        # 2. Thẻ thay thế: [VOICE] ... [/VOICE]
        voice_bracket = re.search(r"\[VOICE\]([\s\S]*?)\[/VOICE\]", text, re.IGNORECASE)
        if voice_bracket:
            speech_raw = voice_bracket.group(1).strip()
            display_text = re.sub(r"\[VOICE\][\s\S]*?\[/VOICE\]", "", text).strip()
            speech_text = cls._sanitise_for_tts(speech_raw)
            return display_text, speech_text

        display_text = text.strip()

        # 3. Tự động nội suy: nếu văn bản chứa bảng biểu, code hoặc quá dài (>250 chars)
        if "```" in display_text or "|" in display_text or len(display_text) > 250:
            speech_text = cls._make_concise_speech_text(display_text)
        else:
            speech_text = cls._sanitise_for_tts(display_text)

        return display_text, speech_text

    @staticmethod
    def _needs_synthesis(reply_text: str) -> bool:
        """Check whether LLM stated that no suitable skill exists."""
        if not reply_text:
            return False
        _SYNTHESIS_TRIGGERS = [
            "không có công cụ",
            "chưa có skill",
            "không thể thực hiện",
            "cần được lập trình thêm",
            "tôi không có khả năng",
            "chưa được hỗ trợ",
            "i don't have",
            "no tool available",
            "cannot perform",
        ]
        lower = reply_text.lower()
        return any(trigger in lower for trigger in _SYNTHESIS_TRIGGERS)

    @staticmethod
    def _synthesise_and_install(
        query: str,
        meta_architect: Any,
        plugin_manager: Any,
    ) -> Optional[str]:
        """Attempt to synthesise and install a new skill for the query."""
        import re as _re
        slug = _re.sub(r"[^\w\s]", "", query.lower())[:40].strip().replace(" ", "_")
        skill_filename = f"auto_{slug}" if slug else "auto_skill"

        try:
            code_str = meta_architect.synthesize_skill(
                intent_description=query,
                failed_context={"reason": "LLM indicated no suitable tool exists"},
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("MetaArchitect synthesis failed: %s", exc)
            return None

        success = meta_architect.verify_and_install(code_str, skill_filename)
        if success:
            new_names = plugin_manager.get_skill_names()
            return new_names[-1] if new_names else skill_filename
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

llm_engine = LLMEngine()

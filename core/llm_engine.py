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
from core.audio.sentence_streamer import sanitise_for_tts

# RBAC (Phase 48) áp dụng trong cổng thực thi tool chung:
# core.agent_voice_loop.run_tool_with_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase 45 Step 1: Global singleton httpx.AsyncClient for Connection Pooling.
# Reusing one persistent connection eliminates TLS handshake overhead (~200ms)
# on every LLM call. Limits: 50 keepalive connections, 120s keepalive timeout.
# ---------------------------------------------------------------------------
_SHARED_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP_CLIENT_LOCK = asyncio.Lock()


async def _get_shared_http_client() -> httpx.AsyncClient:
    """Return the module-level singleton httpx.AsyncClient from Phase 10 ConnectionPoolManager."""
    global _SHARED_HTTP_CLIENT
    from core.connection_pool import get_llm_http_client
    _SHARED_HTTP_CLIENT = await get_llm_http_client()
    return _SHARED_HTTP_CLIENT

# Maximum consecutive tool call rounds before stopping to prevent infinite loops
# Giảm từ 8 → 4: voice không cần nhiều vòng như web portal; giảm latency worst-case
MAX_TOOL_ROUNDS: int = 4
# Giới hạn vòng lặp cho voice riêng (ngắn hơn để phản hồi nhanh hơn)
MAX_TOOL_ROUNDS_VOICE: int = 3

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
    "Quy tắc 2 - Quyền Quản trị Tuyệt đối & Tự động Phê duyệt (Full Admin Mode): "
    "Mọi chỉ lệnh từ Quản trị viên (qua Robot ESP32, Telegram, Standby HUD, Web Portal Admin) đều được ủy quyền tối cao và tự động phê duyệt ngay lập tức. "
    "BẠN PHẢI THỰC THI NGAY MỌI CÔNG CỤ theo yêu cầu (chạy lệnh hệ thống, xóa/ghi file, truy vấn DB, điều khiển thiết bị). "
    "TUYỆT ĐỐI KHÔNG HỎI LẠI, KHÔNG YÊU CẦU PHÊ DUYỆT BẢO MẬT, KHÔNG NÓI 'Tác vụ này yêu cầu phê duyệt bảo mật'. Hãy thực thi ngay và báo cáo kết quả.\n"
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
    "[BẢO VỆ DỮ LIỆU CÁ NHÂN & BỘ ĐỆM TỰ HỦY (GDPR / NGHỊ ĐỊNH 13)]\n"
    "Dữ liệu chi tiết của phòng ban chỉ tồn tại trong bộ nhớ đệm tạm thời tối đa 15-30 phút cho phiên làm việc hiện tại. "
    "Khi chuyển đổi chủ đề hoặc sau thời gian chờ, bạn sẽ không còn quyền truy cập dữ liệu cá nhân chi tiết trừ khi được yêu cầu quét mới. "
    "Hãy chủ động nhắc người dùng nếu phiên làm việc với dữ liệu nhạy cảm đã hết hạn.\n\n"
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
        f"Thiết bị '{dev_name}' (bao gồm Robot ESP32, Telegram Gateway, Standby HUD, Web Portal) ĐƯỢC CẤP FULL QUYỀN ADMIN TỐI CAO trên toàn hệ thống.\n"
        f"Bạn ĐƯỢC PHÉP và BẮT BUỘC thực thi toàn bộ các kỹ năng (Skills), điều khiển chuyển động Robot, cử chỉ, "
        f"quét mạng, đọc ghi tập tin, tra cứu dữ liệu, can thiệp hệ điều hành theo mọi yêu cầu của người dùng.\n"
        f"TUYỆT ĐỐI KHÔNG từ chối, KHÔNG hỏi lại xác nhận, KHÔNG yêu cầu phê duyệt bảo mật — MỌI TÁC VỤ ĐỀU ĐƯỢC PHÊ DUYỆT TỰ ĐỘNG VÀ THỰC THI TRỰC TIẾP."
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

    # Phase 52 & 71: Phân định rõ ngữ cảnh thiết bị ra lệnh (HUD vs Robot vật lý)
    clean_src = str(source_device or "").strip().lower()
    if clean_src == "hud":
        device_context_prompt = (
            "\n\n[NGỮ CẢNH THIẾT BỊ: GIAO DIỆN TRỰC QUAN HUD (HUD TERMINAL)]\n"
            "Người dùng đang tương tác trực tiếp qua màn hình VN-MateAI HUD trên trình duyệt. "
            "Toàn bộ âm thanh giọng nói phản hồi sẽ phát trực tiếp qua loa HUD trên trình duyệt.\n"
            "- TUYỆT ĐỐI KHÔNG tự tiện gọi các công cụ vận động cơ thể robot vật lý (`animate_robot`, `move_robot`) "
            "trừ khi người dùng ra lệnh rõ ràng bằng lời nói yêu cầu điều khiển robot (ví dụ: 'vẫy tay đi', 'tiến lên', 'quay robot lại'). "
            "Khi người dùng chỉ chào hỏi hoặc hỏi đáp thông thường trên HUD, chỉ trả lời bằng lời nói, KHÔNG gọi animate_robot.\n"
        )
        system_content += device_context_prompt
    elif clean_src in ("robot", "xiaozhi") or clean_src.startswith("esp32"):
        robotics_system_prompt = (
            "\n\n[NGỮ CẢNH THIẾT BỊ: ROBOT ĐỂ BÀN VẬT LÝ (ESP32 XIAOZHI)]\n"
            "Người dùng đang tương tác trực tiếp với Robot vật lý đặt trên bàn. "
            "Toàn bộ âm thanh giọng nói phản hồi sẽ phát trực tiếp qua loa phần cứng của Robot.\n"
            "Bạn đang ở trong cơ thể Robot để bàn có tay, cổ và bánh xe:\n"
            "- Khi người dùng chào hỏi trực tiếp Robot, HÃY DÙNG tool `animate_robot(animation='wave_hand')` để vẫy tay chào lại.\n"
            "- Khi đồng ý hoặc xác nhận mệnh lệnh từ Robot, hãy dùng `animate_robot(animation='nod_head')` để gật đầu.\n"
            "- Khi được yêu cầu di chuyển, dùng `move_robot(direction=..., duration_ms=...)` (tối đa 3000ms).\n"
            "- Nếu phát hiện mép bàn/vực (ToF safety alert: edge_detected), lập tức dừng bánh xe và cảnh báo."
        )
        system_content += robotics_system_prompt
    else:
        # Client khác (Web portal, Desktop agent, API): Chỉ điều khiển robot khi có yêu cầu cụ thể
        robotics_system_prompt = (
            "\n\n[CƠ THỂ ROBOT VẬT LÝ & KỸ NĂNG VẬN ĐỘNG (PHASE 52)]\n"
            "Chỉ gọi các tool robot (`animate_robot`, `move_robot`) khi người dùng có yêu cầu điều khiển robot cụ thể.\n"
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
    from core.connection_pool import connection_pool_manager
    pool = connection_pool_manager.get_sync_llm_client() or _SHARED_HTTP_CLIENT
    return AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout=60.0,
        max_retries=0,  # Zero-wait: immediately raise API errors to trigger instant auto-fallback loop
        http_client=pool,
    )

# Danh mục tool: CHỈ plugin_manager (@export_skill). Phase 6 đã gỡ các danh sách
# viết tay FILE_SYSTEM_TOOLS / DELEGATION_TOOLS / VISUAL_OVERLAY_TOOLS /
# ERP_ORGANIZATION_TOOLS — 6/7 tool trùng với plugin_manager, tool còn lại
# (query_organization_data) nay là skill đã đăng ký.



class LLMEngine:
    """
    Thin-client agentic loop:
      User query → OpenAI-compatible call to 9router → Tool selection → Execution → Natural Reply
    """
    build_system_prompt = staticmethod(build_system_prompt)

    def __init__(self) -> None:
        self._client: Optional[AsyncOpenAI] = None  # Lazy init after event loop starts
        self._direct_client: Optional[AsyncOpenAI] = None  # Phase 91: Direct-mode client
        self._last_cfg_snapshot: str = self._cfg_snapshot()
        self.last_voice_display_text: str = ""
        # Phase 87: phần model TỰ SUY NGHĨ trước khi trả lời. Model suy luận ở
        # field `reasoning`, tách hẳn khỏi `content`, nên câu trả lời bạn nghe
        # không bị lẫn suy nghĩ — nhưng trước đây nó bị ném đi hoàn toàn.
        self.last_voice_reasoning: str = ""

    def _cfg_snapshot(self) -> str:
        """Return a string that changes whenever llm config changes."""
        cfg = settings.llm
        return (
            f"{cfg.base_url}|{cfg.model_name}|{cfg.api_key}"
            f"|{cfg.routing_mode}|{cfg.direct_url}|{cfg.direct_model}|{cfg.direct_api_key}"
            f"|{getattr(cfg, 'tri_brain_enabled', True)}"
            f"|{getattr(cfg, 'controller_model', '')}"
            f"|{getattr(cfg, 'voice_model', '')}"
            f"|{getattr(cfg, 'ops_model', '')}"
        )

    def get_brain_model(self, role: str = "controller") -> str:
        """
        Phase 94: Lấy tên mô hình theo kiến trúc 3 Bộ Não Chuyên Biệt:
          - 'controller': Bộ Não Kiểm Soát / Phân Luồng (Supervisor)
          - 'voice':      Bộ Não Giao Tiếp & Thoại Siêu Tốc (Conversational, no tools)
          - 'ops':        Bộ Não Vận Hành Kỹ Năng Hệ Thống (Operations & Tools)
        """
        cfg = settings.llm
        if not getattr(cfg, "tri_brain_enabled", True):
            return cfg.model_name or "ag/gemini-3.6-flash-high"

        if role == "voice":
            return getattr(cfg, "voice_model", "") or getattr(cfg, "model_name", "") or "ag/gemini-3.6-flash-high"
        elif role == "ops":
            return getattr(cfg, "ops_model", "") or getattr(cfg, "specialist_model", "") or getattr(cfg, "model_name", "") or "VN-MateAi"
        else:  # controller
            return getattr(cfg, "controller_model", "") or getattr(cfg, "model_name", "") or "ag/gemini-3.6-flash-high"

    @staticmethod
    def classify_intent(query: str) -> dict:
        """
        Phase 94: Bộ Não Kiểm Soát (Supervisor) phân loại ý định (< 1ms).
        - 'conversation': giao tiếp, hỏi đáp thông thường -> chuyển Bộ Não 2 (Voice Brain, 0 tools, <300ms).
        - 'operation': tác vụ kỹ thuật, can thiệp hệ thống -> phát Acoustic Ack ngay qua Loa, rồi chuyển Bộ Não 3 (Ops Brain).
        """
        import unicodedata
        raw = (query or "").strip().lower()
        norm = unicodedata.normalize("NFKD", raw.replace("đ", "d").replace("Đ", "D"))
        clean = "".join(c for c in norm if not unicodedata.combining(c))

        OP_KEYWORDS = [
            "kiem tra", "check", "quet", "scan", "cpu", "ram", "o dia", "disk", "bo nho",
            "tien trinh", "process", "kill", "xoa", "tao", "ghi", "doc file", "folder",
            "thu muc", "duong dan", "path", "network", "mang", "ping", "port", "ip",
            "wifi", "router", "gateway", "an ninh", "security", "threat", "audit", "lo hong",
            "database", "csdl", "sql", "query", "erp", "nhan su", "kpi", "bao cao",
            "report", "may tram", "client", "lan", "may tinh", "remote", "dieu khien",
            "restart", "khoi dong", "tat may", "shutdown", "chay script", "run", "lenh",
            "robot", "vay tay", "tien len", "lui lai", "quay", "dong co"
        ]

        CASUAL_STARTS = [
            "xin chao", "chao", "hello", "hi", "cam on", "thanks", "tam biet", "bye", "ok",
            "duoc roi", "ban la ai", "em la ai", "ten gi", "khoe khong", "the nao", "thoi tiet"
        ]

        if any(clean.startswith(cs) for cs in CASUAL_STARTS) and len(clean.split()) <= 5:
            return {
                "type": "conversation",
                "target_brain": "voice",
                "ack_needed": False,
            }

        is_op = any(kw in clean for kw in OP_KEYWORDS)
        if is_op:
            return {
                "type": "operation",
                "target_brain": "ops",
                "ack_needed": True,
            }

        return {
            "type": "conversation",
            "target_brain": "voice",
            "ack_needed": False,
        }

    async def _ensure_shared_client(self) -> None:
        """
        Phase 45: Initialise the shared httpx pool once the event loop is running,
        then rebuild the AsyncOpenAI client to inject it.
        Phase 91: Also build the direct-mode client if routing_mode != 'router'.
        """
        await _get_shared_http_client()  # Ensure pool is created
        snap = self._cfg_snapshot()
        if self._client is None or snap != self._last_cfg_snapshot:
            cfg = settings.llm
            # --- Router client (unchanged) ---
            self._client = AsyncOpenAI(
                base_url=cfg.base_url,
                api_key=cfg.api_key,
                timeout=45.0,
                max_retries=0,
                http_client=_SHARED_HTTP_CLIENT,
            )
            # --- Direct client (Phase 91) ---
            if cfg.direct_url:
                direct_url = cfg.direct_url.rstrip("/")
                if not direct_url.endswith("/v1"):
                    direct_url = direct_url + "/v1"
                self._direct_client = AsyncOpenAI(
                    base_url=direct_url,
                    api_key=cfg.direct_api_key or "lm-studio",
                    timeout=45.0,
                    max_retries=0,
                    http_client=_SHARED_HTTP_CLIENT,
                )
                logger.info(
                    "[Phase91] Direct-mode client built: %s (model=%s)",
                    direct_url,
                    cfg.direct_model or cfg.model_name,
                )
            else:
                self._direct_client = None
            self._last_cfg_snapshot = snap
            logger.info(
                "[Phase45] AsyncOpenAI clients (re)built | routing_mode=%s",
                cfg.routing_mode,
            )

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
    # Phase 2: LLM Streaming & Provider Abstraction
    # ------------------------------------------------------------------

    def get_provider(self, brain_role: str = "voice") -> Any:
        """
        Phase 2: Lấy LLM Provider chuẩn hóa theo vai trò Tri-Brain và routing_mode.
        """
        from core.llm_provider import DirectLLMProvider, NineRouterLLMProvider, TriBrainLLMProvider
        cfg = settings.llm
        direct_prov = None
        if self._direct_client and cfg.direct_url:
            direct_m = (cfg.direct_model or self.get_brain_model(brain_role) or "").strip()
            direct_prov = DirectLLMProvider(self._direct_client, direct_m, cfg.direct_url)

        primary_m = (self.get_brain_model(brain_role) or cfg.model_name or "").strip()
        router_models = getattr(cfg, "router_models", []) or []
        if isinstance(router_models, str):
            router_models = [router_models.strip()]

        router_prov = NineRouterLLMProvider(self._client, primary_m, router_models)
        return TriBrainLLMProvider(direct_provider=direct_prov, router_provider=router_prov)

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """
        Phase 2: Chuẩn hóa LLM streaming interface theo async generator:
            async for chunk in llm.stream(messages, ...):
                ...
        """
        await self._ensure_shared_client()
        provider = self.get_provider(brain_role=brain_role)
        mode = (settings.llm.routing_mode or "router").lower()
        async for chunk in provider.stream(
            messages=messages,
            tools=tools,
            brain_role=brain_role,
            routing_mode=mode,
            **kwargs,
        ):
            yield chunk

    async def stream_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """
        Phase 2: Stream token văn bản thô cho Voice/UI Event Bus:
            async for token in llm.stream_tokens(messages, ...):
                ...
        """
        async for chunk in self.stream(messages=messages, tools=tools, brain_role=brain_role, **kwargs):
            if chunk.content:
                yield chunk.content

    # ------------------------------------------------------------------
    # Core LLM call
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        brain_role: str = "controller",
    ) -> Any:
        """
        Gọi LLM không stream (vòng agent) qua provider chung (core.llm_provider).

        routing_mode: 'router' (9Router, có danh sách dự phòng) | 'direct' (gọi thẳng
        LM Studio / Ollama / DeepSeek) | 'auto' (direct trước, lỗi thì router).
        Phase 5: trước đây là _call_llm_direct + _call_llm_router — bản sao của
        provider.complete() với vòng thử model riêng.
        """
        await self._ensure_shared_client()
        mode = (settings.llm.routing_mode or "router").lower()
        if mode == "direct" and not self._direct_client:
            raise RuntimeError(
                "Direct mode được bật nhưng 'direct_url' chưa được cấu hình. "
                "Vào tab Quản Lý Trợ Lý AI → Bộ Não & Xử Lý Ngôn Ngữ để thiết lập."
            )
        provider = self.get_provider(brain_role=brain_role)
        return await provider.complete(
            messages=messages, tools=tools, brain_role=brain_role, routing_mode=mode,
        )

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
        caller: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Async implementation of the full agentic loop.

        caller: danh tính dùng cho RBAC/audit khi chạy tool (mặc định source_device;
        portal truyền username đã đăng nhập).

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

        # Phase 60/6: Plugin Registry KHÔNG còn là danh mục tool thứ hai. Tool nào
        # có trong registry (connector Phase 59, computer-use) vẫn được LLM thấy qua
        # plugin_manager; registry chỉ quyết định đường THỰC THI có timeout +
        # circuit breaker + cổng HITL theo risk_level (run_tool_with_policy).
        _registry_names: set = set()
        try:
            from core.plugin_registry import plugin_registry as _plugin_registry
            _registry_names = set(_plugin_registry.get_tool_names())
        except Exception as reg_err:  # pylint: disable=broad-except
            # Registry hỏng KHÔNG được làm sập toàn bộ hội thoại.
            logger.warning("[Phase60] Không đọc được Plugin Registry: %s", reg_err)

        tools = self._enrich_tools_with_target_client(raw_tools)

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
        # về 'viewer' cho tới khi có cơ chế định danh thật sự. `caller` (người dùng
        # đã đăng nhập) thắng source_device — cùng khoá mà cổng tool dùng khi lưu
        # pending action, nếu không "Đồng ý" sẽ không bao giờ tìm thấy tác vụ.
        caller_id = str(caller or source_device or "anonymous")
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

                # Thực thi qua cổng chung (RBAC + audit; confirmed=True nên không hỏi
                # lại). Trước Phase 6: asyncio.to_thread(plugin_manager.execute_skill)
                # — execute_skill là async nên tác vụ đã duyệt không bao giờ chạy.
                from core.agent_voice_loop import run_tool_with_policy
                _gate = await run_tool_with_policy(
                    tool_name,
                    {**args, "target_client": target_client},
                    caller=caller_id,
                    source_device=source_device,
                    query=orig_query,
                )
                tool_res = _gate["result"]

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

        intent = self.classify_intent(query)
        brain_role = "ops" if (intent.get("type") == "operation" or tools) else "controller"

        for round_idx in range(MAX_TOOL_ROUNDS):
            logger.debug("LLM round %d — messages=%d, tools=%d, brain_role=%s", round_idx, len(messages), len(tools), brain_role)

            try:
                response = await self._call_llm(
                    messages=messages,
                    tools=tools if tools else None,
                    brain_role=brain_role,
                )
                used_model = getattr(response, "model", None) or settings.llm.model_name
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

            # Phase 87: lưu phần suy nghĩ của model. Router đặt ở field
            # `reasoning` (đôi khi `reasoning_content`), tách khỏi `content`.
            # Ghi đè mỗi vòng để khi thoát vòng lặp, giá trị còn lại là suy nghĩ
            # của chính lượt gọi sinh ra câu trả lời cuối — không phải suy nghĩ
            # của vòng trước đã đi gọi tool.
            self.last_voice_reasoning = self._compact_reasoning(
                getattr(assistant_msg, "reasoning", "")
                or getattr(assistant_msg, "reasoning_content", "")
                or ""
            )

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

            # ---- Case 1: LLM wants to execute tool calls (PARALLEL) ----
            if finish_reason == "tool_calls" and getattr(assistant_msg, "tool_calls", None):

                # Phase-Perf: Chạy tất cả tool calls SONG SONG bằng asyncio.gather()
                # Trước: tuần tự mỗi tool → nếu LLM gọi 3 tool thì tổng thời gian = T1+T2+T3
                # Sau: song song → tổng thời gian = max(T1, T2, T3) → giảm 40-60%
                async def _run_single_tool(tool_call) -> dict:
                    """Execute one tool call, return dict with result."""
                    fn_name: str = tool_call.function.name
                    try:
                        fn_args: Dict[str, Any] = (
                            json.loads(tool_call.function.arguments)
                            if isinstance(tool_call.function.arguments, str)
                            else (tool_call.function.arguments or {})
                        )
                    except json.JSONDecodeError:
                        fn_args = {}

                    _tc_id = tool_call.id

                    # Cổng thực thi tool dùng chung (Zero-Trust, HITL, RBAC, audit) —
                    # cùng một implementation với đường voice realtime.
                    from core.agent_voice_loop import run_tool_with_policy
                    _gate = await run_tool_with_policy(
                        fn_name, fn_args,
                        caller=caller_id,
                        source_device=source_device,
                        query=query,
                        session_id=getattr(assistant_msg, "id", None),
                        registry_names=_registry_names,
                    )
                    return {"tool_call_id": _tc_id, "fn_name": fn_name, **_gate}

                logger.info(
                    "[LLMEngine-Parallel] Chạy %d tool(s) song song: %s",
                    len(assistant_msg.tool_calls),
                    [tc.function.name for tc in assistant_msg.tool_calls],
                )
                parallel_results = await asyncio.gather(
                    *[_run_single_tool(tc) for tc in assistant_msg.tool_calls],
                    return_exceptions=True,
                )
                for pres in parallel_results:
                    if isinstance(pres, Exception):
                        logger.error("[LLMEngine-Parallel] Tool error: %s", pres)
                        continue
                    tool_calls_made.append({
                        "skill": pres["fn_name"], "target_client": pres["target_client"],
                        "args": pres["args"], "result": pres["result"],
                    })
                    masked_result_str = security_engine.mask_sensitive_data(
                        json.dumps(pres["result"], ensure_ascii=False, default=str)
                    )
                    messages.append({"role": "tool", "tool_call_id": pres["tool_call_id"], "content": masked_result_str})
                # Tiếp tục vòng để LLM tổng hợp kết quả tool
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
                    # Phase 87: kèm luôn suy nghĩ, để phía gọi đọc được của
                    # đúng lượt này thay vì đọc thuộc tính chung (lượt song
                    # song sẽ ghi đè lẫn nhau).
                    "reasoning": self.last_voice_reasoning,
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

        reply = sanitise_for_tts(raw_reply)

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
        reply = sanitise_for_tts(raw_reply)

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
        session_id: Optional[str] = None,
        turn: Optional[Dict[str, Any]] = None,
        tool_ack: bool = True,
        caller: Optional[str] = None,
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
        # Phase 3: dùng chung cho mọi kênh voice (core/voice_turn.py).
        #   session_id — khoá lịch sử trong memory_manager (mặc định source_device).
        #   turn       — dict nhận kết quả CỦA LƯỢT NÀY: display_text, reasoning,
        #                used_agent. Thuộc tính last_voice_* trên singleton vẫn được
        #                ghi để tương thích, nhưng bị ghi đè khi nhiều phiên chạy song song.
        #   tool_ack   — False khi bên gọi đã phát câu đệm trước khi gọi LLM.
        from core.safety_guard import security_engine
        if turn is None:
            turn = {}
        _session = str(session_id or source_device or "voice")
        if history is None:
            from core.memory_manager import memory_manager as _mm_hist
            history = _mm_hist.get_history(_session)
        sanitized_query = security_engine.mask_sensitive_data(query)
        system_content = build_system_prompt(source_device=source_device)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        if history:
            # Phase 9: Cắt tỉa ngữ cảnh lịch sử cho giọng nói
            from core.history_pruner import prune_history_for_voice
            pruned_hist = prune_history_for_voice(history, max_turns=4, max_total_chars=1200)
            messages.extend(pruned_hist)
        messages.append({"role": "user", "content": sanitized_query})

        # Phase 45: Use shared connection pool for streaming (eliminates TLS handshake)
        await self._ensure_shared_client()
        # Phase 94: Phân loại ý định qua Bộ Não Kiểm Soát (Supervisor)
        intent = self.classify_intent(query)
        logger.info("[TriBrain] Intent phân loại: %s (chuyển sang %s brain)", intent["type"], intent["target_brain"])

        if intent["type"] == "conversation":
            # BỘ NÃO 2: Giao tiếp & Thoại — KHÔNG nạp tools, giảm tải 100% schemas!
            tools = None
            role = "voice"
        else:
            # BỘ NÃO 3: Vận hành hệ thống — Phase 8: Dynamic Skill Loading (Chỉ nạp top 5 công cụ liên quan nhất)
            role = "ops"
            from core.dynamic_skill_router import dynamic_skill_router
            raw_tools = dynamic_skill_router.get_tools_for_query(query, max_tools=5)
            tools = self._enrich_tools_with_target_client(raw_tools) if raw_tools else None

        # Phase 5: mở stream + thử model dự phòng do provider chung đảm nhận
        # (core.llm_provider — nhớ model hỏng, bỏ qua giá trị mẫu trong cấu hình).
        # Mọi model đều lỗi -> ngoại lệ ở lần lặp đầu, khối except bên dưới phát
        # câu xin lỗi như trước.
        provider = self.get_provider(brain_role=role)
        routing_mode = (settings.llm.routing_mode or "router").lower()
        t_start = time.monotonic()
        first_token_logged = False

        # Câu để ĐỌC: ranh giới an toàn + gộp câu ngắn / tách câu dài (Phase 5).
        from core.audio.sentence_buffer import SentenceBuffer
        speech_buf = SentenceBuffer(min_chars=1, min_words=8, max_words=30)
        # Toàn bộ chữ gốc của lượt.
        raw_reply = ""
        # Phase 87: gom phần suy nghĩ của model. Router trả nó ở
        # `delta.reasoning` (đôi khi tên `reasoning_content`), tách hẳn khỏi
        # `delta.content` — nên câu trả lời bạn nghe không bị lẫn suy nghĩ, nhưng
        # trước đây nó bị vứt đi hoàn toàn.
        reasoning_buffer = ""
        has_tool_calls = False
        has_yielded_any_sentence = False

        # Lượt mới -> xoá suy nghĩ của lượt cũ. Không xoá thì lượt không có
        # suy nghĩ sẽ hiện lại suy nghĩ của lượt trước — tức bịa.
        self.last_voice_reasoning = ""

        def _publish_reasoning() -> None:
            """Công bố suy nghĩ tích luỹ cho HUD, gọi ngay TRƯỚC mỗi lần yield.

            Model suy luận xong rồi mới bắt đầu viết câu trả lời, nên gọi trước
            lần yield đầu là đủ để HUD kịp hiện. Vẫn gọi trước mọi lần yield vì
            có model xen kẽ suy nghĩ với câu trả lời trong cùng một stream.
            """
            self.last_voice_reasoning = self._compact_reasoning(reasoning_buffer)
            turn["reasoning"] = self.last_voice_reasoning

        try:
            async for chunk in provider.stream(
                messages=messages,
                tools=tools if tools else None,
                brain_role=role,
                routing_mode=routing_mode,
            ):
                # Phase 87: nuốt phần suy nghĩ vào bộ đệm riêng. Cố tình KHÔNG
                # gộp vào chữ trả lời: đó là câu sẽ đọc to và hiện trên HUD.
                reasoning_buffer += chunk.reasoning or ""

                # Phát hiện tool call → phát câu xác nhận TỨC THÌ + chuyển agentic loop.
                # ⚡ FAST FEEDBACK: User nghe "Để em kiểm tra..." sau ~1s thay vì
                # im lặng 15-20s. TTS câu này chạy song song với agentic loop.
                if chunk.tool_calls:
                    has_tool_calls = True
                    _wasted = time.monotonic() - t_start
                    detected_tools = [tc.get("name", "") for tc in chunk.tool_calls]
                    logger.info(
                        "[LLMEngine] Phát hiện tool call %s sau %.2fs → phát ack ngay, chuyển sang vòng lặp agentic.",
                        detected_tools, _wasted,
                    )
                    # Phát câu xác nhận ngay lập tức (chỉ khi chưa nói gì)
                    if tool_ack and not has_yielded_any_sentence:
                        import random as _rand
                        _ack = _rand.choice([
                            "Dạ, để em kiểm tra thông tin đó cho anh nhé.",
                            "Vâng, em đang tra cứu cho anh.",
                            "Dạ, để em xử lý yêu cầu này.",
                            "Vâng, để em kiểm tra ngay.",
                        ])
                        _publish_reasoning()
                        yield _ack
                        has_yielded_any_sentence = True
                    break

                token = chunk.content or ""
                if not token:
                    continue

                if not first_token_logged:
                    logger.info(
                        "[LLMEngine] First token received in %.2fs (%s).",
                        time.monotonic() - t_start, chunk.model,
                    )
                    first_token_logged = True

                raw_reply += token
                turn["display_text"] = raw_reply

                for sentence in speech_buf.add_token(token):
                    clean = sanitise_for_tts(sentence)
                    if clean:
                        has_yielded_any_sentence = True
                        _publish_reasoning()
                        yield clean

        except Exception as exc:
            logger.error("[LLMEngine] Streaming error mid-response (ngắt an toàn): %s", exc)
            # Ngắt an toàn khi kết nối bị đứt giữa chừng lúc đang stream:
            # Nếu chưa yield được câu nào, gửi câu fallback nhẹ nhàng.
            # Nếu đã stream được một phần câu, dừng an toàn mà KHÔNG ném lỗi JSON.
            if not has_yielded_any_sentence and not raw_reply.strip():
                fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
                self.last_voice_display_text = fallback_msg
                turn["display_text"] = fallback_msg
                # Stream đứt giữa chừng: phần suy nghĩ đã nhận được vẫn còn
                # giá trị hiển thị (nó có thật, không phải bịa) — giữ lại.
                _publish_reasoning()
                yield fallback_msg
                return

        # Xả phần còn lại (câu cuối / câu ngắn đang chờ gộp)
        if not has_tool_calls:
            for rest in speech_buf.flush():
                clean = sanitise_for_tts(rest)
                if clean:
                    has_yielded_any_sentence = True
                    _publish_reasoning()
                    yield clean

        # Save streamed conversation to MemoryManager
        # Trước Phase 3 đoạn này dùng `text_buffer` — chỉ còn phần dư sau câu
        # cuối — nên câu trả lời kết thúc bằng dấu câu KHÔNG được lưu lịch sử và
        # chữ hiển thị là của lượt trước.
        if not has_tool_calls and raw_reply.strip():
            self.last_voice_display_text = raw_reply.strip()
            turn["display_text"] = raw_reply.strip()
            try:
                from core.memory_manager import memory_manager as _mm
                _mm.add_turn(_session, query, sanitise_for_tts(raw_reply))
            except Exception:
                pass

        # If tool calls detected, fall back to full agentic loop
        if has_tool_calls:
            logger.info("[LLMEngine] Executing full agentic loop for tool call...")
            self.last_voice_reasoning = ""
            turn["used_agent"] = True
            try:
                result = await self.ask_async(
                    query=query,
                    source_device=source_device,
                    history=history,
                    session_id=_session,
                    caller=caller,
                )
                self.last_voice_display_text = result.get("reply", "")
                turn["display_text"] = self.last_voice_display_text
                turn["reasoning"] = result.get("reasoning", "") or ""
                speech_reply = result.get("speech_reply") or sanitise_for_tts(self.last_voice_display_text)
                if speech_reply:
                    # Loại bỏ câu trùng với câu đã phát (acknowledgment)
                    _ack_prefixes = ("dạ, để em kiểm tra", "vâng, em đang", "dạ, để em xử lý", "vâng, để em kiểm tra")
                    if not any(speech_reply.lower().startswith(p) for p in _ack_prefixes):
                        yield speech_reply
                elif result.get("success"):
                    yield "Em đã thực hiện xong yêu cầu của anh."
                else:
                    yield "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
            except Exception as exc:
                logger.error("[LLMEngine] Agentic fallback error: %s", exc)
                fallback_msg = "Dạ, hệ thống xử lý ngôn ngữ hiện đang quá tải hoặc hết hạn mức. Anh vui lòng thử lại sau ít phút nhé."
                self.last_voice_display_text = fallback_msg
                turn["display_text"] = fallback_msg
                yield fallback_msg

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

    #: Phase 87 — độ dài tối đa của "quá trình suy nghĩ" hiển thị trên HUD.
    #:
    #: Đo thật trên máy này (2026-09-30): câu hỏi "12 quả táo cho đi 3 rồi mua
    #: thêm 5" sinh khoảng 250-700 ký tự suy nghĩ. Câu hỏi thật của CEO
    #: (báo cáo tài chính, tra cứu ERP) dài hơn nhiều. Không cắt thì khung này
    #: đẩy hết bố cục HUD, mà đọc cũng không hết.
    REASONING_DISPLAY_MAX = 600

    @classmethod
    def _compact_reasoning(cls, text: str, max_len: Optional[int] = None) -> str:
        """Gọn phần suy nghĩ của model cho HUD — GIỮ NGUYÊN tiếng gốc.

        Người dùng chọn rõ: "giữ nguyên tiếng Anh, chỉ làm gọn". Vì vậy ở đây
        KHÔNG dịch, KHÔNG tóm tắt, KHÔNG bỏ từ nào — chỉ gộp khoảng trắng và
        cắt độ dài. Bỏ từ trong suy luận là dựng lại ý model, tức là bịa.

        Cắt ở ranh giới câu/từ để không dính nửa từ ("...incomp|eted").
        """
        import re as _re

        if not text:
            return ""
        # Suy nghĩ ra nhiều dòng vì model ngắt dòng khi lập luận; gộp lại thành
        # một dòng cho gọn. Giữa các từ vẫn để 1 khoảng trắng.
        flat = _re.sub(r"\s+", " ", str(text)).strip()
        limit = cls.REASONING_DISPLAY_MAX if max_len is None else max_len
        if limit <= 0 or len(flat) <= limit:
            return flat
        cut = flat[:limit]
        # Lùi về ranh giới từ gần nhất trong 40 ký tự cuối, tránh cắt giữa từ.
        space = cut.rfind(" ", max(0, limit - 40))
        if space > limit // 2:
            cut = cut[:space]
        return cut.rstrip(" ,;:.-") + "…"

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
            intro_str = sanitise_for_tts(" ".join(intro_parts[:2]))
            if len(intro_str) > 10:
                if not any(intro_str.endswith(p) for p in (".", "!", "?")):
                    intro_str += "."
                return f"{intro_str} Chi tiết cụ thể đã được hiển thị trên màn hình của anh."

        # Fallback sanitisation
        cleaned = sanitise_for_tts(text)
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
            speech_text = sanitise_for_tts(speech_raw)
            return display_text, speech_text

        # 2. Thẻ thay thế: [VOICE] ... [/VOICE]
        voice_bracket = re.search(r"\[VOICE\]([\s\S]*?)\[/VOICE\]", text, re.IGNORECASE)
        if voice_bracket:
            speech_raw = voice_bracket.group(1).strip()
            display_text = re.sub(r"\[VOICE\][\s\S]*?\[/VOICE\]", "", text).strip()
            speech_text = sanitise_for_tts(speech_raw)
            return display_text, speech_text

        display_text = text.strip()

        # 3. Tự động nội suy: nếu văn bản chứa bảng biểu, code hoặc quá dài (>250 chars)
        if "```" in display_text or "|" in display_text or len(display_text) > 250:
            speech_text = cls._make_concise_speech_text(display_text)
        else:
            speech_text = sanitise_for_tts(display_text)

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

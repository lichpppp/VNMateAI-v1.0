"""
core/dynamic_skill_router.py
============================
Phase 8: Dynamic Skill Loading & Taxonomy-Based Domain Indexing.

Mục tiêu cốt lõi:
  - Phân loại toàn bộ kỹ năng (79+ skills và các native tools) vào các Miền Nghiệp Vụ (Domains).
  - Lập chỉ mục bộ nhớ đệm (In-Memory Pre-indexed Catalog) để truy xuất schema công cụ < 0.1ms.
  - Phân giải ý định người dùng (Query-to-Domain Matching) tức thì:
      + Đàm thoại thông thường: Trả về 0 tools (Triệt tiêu 100% overhead token, TTFT < 200ms).
      + Tác vụ kỹ thuật: Chỉ nạp đúng 3-5 tools thuộc domain liên quan nhất (thay vì nhồi nhét 100 tools ~30k tokens).
  - Hỗ trợ Lazy Loading & Auto-Reindexing khi có plugin mới được nạp hoặc bật/tắt.
  - Bảo toàn 100% Zero-Trust, RBAC và tương thích ngược với API Admin.
"""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Domain Taxonomy Definition
# ---------------------------------------------------------------------------

class SkillDomain(str, Enum):
    """Các miền nghiệp vụ chuẩn hóa cho hệ sinh thái công cụ của VN-MateAI."""
    SYSTEM_OPS = "system_ops"              # CPU, RAM, Disk, Services, Process, Kill, Windows SysAdmin, Monitoring
    NETWORK_SECURITY = "network_security"  # Ping, IP, Port, Firewall, Vulnerabilities, SSL, Network interfaces
    FILE_STORAGE = "file_storage"          # Read/Write file, Search directory, Delete files, File system
    DATABASE_ERP = "database_erp"          # SQL queries, Excel records, ERP metrics, Finance/KPI, Data sources
    PC_AUTOMATION = "pc_automation"        # Computer Use GUI, Mouse, Keyboard, Screenshots, Windows control
    KNOWLEDGE_RAG = "knowledge_rag"        # Enterprise RAG, GraphRAG, Tra cứu tri thức, Q&A tài liệu
    ITSM_WORKFLOW = "itsm_workflow"        # Tickets, Incident management, Onboarding workflows, IT service
    MULTI_AGENT = "multi_agent"            # Executive standup, Delegation to CFO/HR/CTO/Specialist
    VISUAL_MEDIA = "visual_media"          # Visual boards, Charts, Overlays, Screen inspection
    GENERAL_TOOLS = "general_tools"        # Các công cụ tiện ích tổng quát khác


# Danh sách từ khóa nhận diện cho từng miền (Đã chuẩn hóa không dấu)
DOMAIN_SIGNATURES: Dict[SkillDomain, List[str]] = {
    SkillDomain.SYSTEM_OPS: [
        "cpu", "ram", "bo nho", "tien trinh", "process", "kill", "dung tien trinh",
        "he thong", "disk", "o dia", "dung luong", "restart", "khoi dong lai",
        "service", "dich vu", "sysadmin", "task manager", "uptime", "hardware",
        "nhiet do", "load", "tai", "hieu nang",
    ],
    SkillDomain.NETWORK_SECURITY: [
        "mang", "network", "ip", "ping", "port", "cong", "wifi", "router", "gateway",
        "ket noi", "an ninh", "security", "threat", "lo hong", "quet", "scan",
        "firewall", "tuong lua", "audit", "ssl", "cert", "dns", "bang thong",
    ],
    SkillDomain.FILE_STORAGE: [
        "file", "tap tin", "thu muc", "folder", "doc file", "ghi file", "xoa file",
        "tao file", "copy", "move", "rename", "doi ten", "sao chep", "duong dan",
        "path", "luu tru", "danh sach file", "tim file",
    ],
    SkillDomain.DATABASE_ERP: [
        "csdl", "database", "sql", "query", "truy van", "bang", "table", "mysql",
        "postgres", "excel", "bang tinh", "kpi", "doanh thu", "tai chinh", "chi phi",
        "nhan su", "luong", "erp", "don hang", "doanh so", "so lieu", "bao cao tai chinh",
    ],
    SkillDomain.PC_AUTOMATION: [
        "chup man hinh", "screenshot", "ung dung", "app", "mo app", "dong app",
        "chuot", "click", "ban phim", "go phim", "clipboard", "cua so", "window",
        "computer use", "dieu khien may", "nhan chuot", "keo tha",
    ],
    SkillDomain.KNOWLEDGE_RAG: [
        "tai lieu", "quy trinh", "huong dan", "chinh sach", "tri thuc", "knowledge",
        "rag", "graphrag", "tra cuu", "hoi dap", "tim kiem noi bo", "so tay", "wiki",
    ],
    SkillDomain.ITSM_WORKFLOW: [
        "ticket", "su co", "incident", "itsm", "sla", "ho tro", "yeu cau ho tro",
        "onboarding", "nhan vien moi", "workflow", "quy trinh duyet",
    ],
    SkillDomain.MULTI_AGENT: [
        "giao ban", "standup", "executive", "ceo", "cfo", "cto", "hr agent",
        "uy quyen", "delegate", "chuyen gia", "specialist", "phan tich sau", "da tac nhan",
    ],
    SkillDomain.VISUAL_MEDIA: [
        "bieu do", "chart", "do thi", "visual", "hien thi man hinh", "text board",
        "overlay", "hinh anh", "minh hoa",
    ],
}


def _strip_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt và chuẩn hóa chữ thường."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D").lower().strip()


# ---------------------------------------------------------------------------
# 2. Dynamic Skill Index & Router
# ---------------------------------------------------------------------------

class DynamicSkillRouter:
    """
    Bộ định tuyến kỹ năng động:
      - Quản lý bộ nhớ đệm phân loại (Pre-indexed Catalog) của tất cả kỹ năng.
      - Phân giải cực nhanh (sub-millisecond) các tools liên quan nhất theo ý định.
      - Giảm payload token gửi lên LLM từ ~30.000 tokens xuống còn ~500 tokens.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # domain -> List[Tool Schema Dict]
        self._domain_schemas: Dict[SkillDomain, List[Dict[str, Any]]] = {
            d: [] for d in SkillDomain
        }
        # tool_name -> Tool Schema Dict
        self._tool_cache: Dict[str, Dict[str, Any]] = {}
        # tool_name -> SkillDomain
        self._tool_domain_map: Dict[str, SkillDomain] = {}
        self._is_indexed: bool = False
        self._last_index_time: float = 0.0

    def rebuild_index(self) -> int:
        """
        Quét toàn bộ công cụ từ PluginManager, PluginRegistry và Native Tools,
        phân loại theo Domain và lập chỉ mục trong RAM.
        """
        with self._lock:
            t0 = time.perf_counter()
            self._domain_schemas = {d: [] for d in SkillDomain}
            self._tool_cache.clear()
            self._tool_domain_map.clear()

            all_tools: List[Dict[str, Any]] = []

            # 1. Nạp từ PluginManager
            try:
                from core.plugin_manager import plugin_manager
                for tool in plugin_manager.get_all_tools():
                    all_tools.append(tool)
            except Exception as exc:
                logger.warning("[DynamicSkillRouter] Không thể nạp tools từ PluginManager: %s", exc)

            # 2. Nạp Native Tools từ core/llm_engine
            try:
                from core.llm_engine import (
                    FILE_SYSTEM_TOOLS,
                    DELEGATION_TOOLS,
                    VISUAL_OVERLAY_TOOLS,
                    ERP_ORGANIZATION_TOOLS,
                )
                for extra in FILE_SYSTEM_TOOLS + DELEGATION_TOOLS + VISUAL_OVERLAY_TOOLS + ERP_ORGANIZATION_TOOLS:
                    all_tools.append(extra)
            except Exception as exc:
                logger.warning("[DynamicSkillRouter] Không thể nạp Native Tools: %s", exc)

            # 3. Nạp từ PluginRegistry (Phase 60)
            try:
                from core.plugin_registry import plugin_registry
                for reg_schema in plugin_registry.get_all_tools_schema():
                    all_tools.append(reg_schema)
            except Exception as exc:
                logger.warning("[DynamicSkillRouter] Không thể nạp tools từ PluginRegistry: %s", exc)

            # Khử trùng lặp tên tool (Ưu tiên tool từ registry hoặc native)
            unique_tools: Dict[str, Dict[str, Any]] = {}
            for t in all_tools:
                fn = t.get("function") or {}
                name = fn.get("name")
                if name:
                    unique_tools[name] = t

            # 4. Phân loại từng tool vào Domain tương ứng
            for name, tool in unique_tools.items():
                self._tool_cache[name] = tool
                domain = self._classify_tool_domain(name, tool)
                self._tool_domain_map[name] = domain
                self._domain_schemas[domain].append(tool)

            self._is_indexed = True
            self._last_index_time = time.monotonic()
            elapsed_ms = (time.perf_counter() - t0) * 1000

            logger.info(
                "[DynamicSkillRouter] Đã lập chỉ mục %d công cụ trên %d miền nghiệp vụ trong %.2fms.",
                len(unique_tools),
                len(self._domain_schemas),
                elapsed_ms,
            )
            return len(unique_tools)

    def _ensure_indexed(self) -> None:
        """Đảm bảo chỉ mục đã sẵn sàng."""
        if not self._is_indexed or not self._tool_cache:
            self.rebuild_index()

    def _classify_tool_domain(self, name: str, tool_schema: Dict[str, Any]) -> SkillDomain:
        """
        Phân loại tự động một công cụ vào một Domain cụ thể
        dựa trên tên hàm và nội dung mô tả.
        """
        fn = tool_schema.get("function") or {}
        desc = _strip_accents(fn.get("description", ""))
        name_clean = _strip_accents(name)
        combined = f"{name_clean} {desc}"

        # Quy tắc đặc thù cho các nhóm công cụ chính thức
        if any(k in name_clean for k in ["process", "service", "sysadmin", "cpu", "ram", "disk", "hardware", "system"]):
            return SkillDomain.SYSTEM_OPS
        if any(k in name_clean for k in ["ping", "port", "network", "firewall", "security", "threat", "scan"]):
            return SkillDomain.NETWORK_SECURITY
        if any(k in name_clean for k in ["file", "dir", "directory", "folder", "read_file", "write_file", "search_files"]):
            return SkillDomain.FILE_STORAGE
        if any(k in name_clean for k in ["excel", "sql", "database", "kpi", "finance", "hr", "erp", "data_source"]):
            return SkillDomain.DATABASE_ERP
        if any(k in name_clean for k in ["pc_control", "computer_use", "screenshot", "mouse", "keyboard", "window"]):
            return SkillDomain.PC_AUTOMATION
        if any(k in name_clean for k in ["rag", "knowledge", "wiki", "document"]):
            return SkillDomain.KNOWLEDGE_RAG
        if any(k in name_clean for k in ["ticket", "itsm", "onboarding", "sla", "incident"]):
            return SkillDomain.ITSM_WORKFLOW
        if any(k in name_clean for k in ["delegate", "standup", "executive", "agent_orchestrator"]):
            return SkillDomain.MULTI_AGENT
        if any(k in name_clean for k in ["visual", "chart", "overlay", "display_visual_data"]):
            return SkillDomain.VISUAL_MEDIA

        # Chấm điểm theo từ khóa miền
        best_domain = SkillDomain.GENERAL_TOOLS
        max_score = 0
        for domain, keywords in DOMAIN_SIGNATURES.items():
            score = sum(2 for kw in keywords if kw in name_clean) + sum(1 for kw in keywords if kw in desc)
            if score > max_score:
                max_score = score
                best_domain = domain

        return best_domain

    def get_domain_stats(self) -> Dict[str, int]:
        """Thống kê số lượng công cụ trong từng miền nghiệp vụ."""
        self._ensure_indexed()
        with self._lock:
            return {domain.value: len(tools) for domain, tools in self._domain_schemas.items()}

    def get_tools_by_domain(self, domain: SkillDomain | str) -> List[Dict[str, Any]]:
        """Lấy tất cả schema công cụ thuộc một miền cụ thể."""
        self._ensure_indexed()
        if isinstance(domain, str):
            try:
                domain = SkillDomain(domain.lower())
            except ValueError:
                return []
        with self._lock:
            return list(self._domain_schemas.get(domain, []))

    def get_tools_for_query(
        self,
        query: str,
        max_tools: int = 5,
        domain_hint: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Hàm cốt lõi của Phase 8:
        Phân giải ngữ cảnh truy vấn và nạp động đúng danh sách công cụ cần thiết.

        Quy tắc:
          1. Nếu là câu đàm thoại xã giao thông thường ("xin chào", "cảm ơn", "bạn là ai"...):
             -> Trả về [] (0 tools) -> TTFT siêu tốc (<200ms).
          2. Nếu có domain_hint:
             -> Lấy công cụ từ domain gợi ý.
          3. Nếu là lệnh tác vụ kỹ thuật:
             -> Khớp các miền nghiệp vụ phù hợp, chấm điểm liên quan từng tool,
                và lấy ra top max_tools công cụ điểm cao nhất.
        """
        self._ensure_indexed()
        query_clean = _strip_accents(query or "")

        # 1. Phát hiện đàm thoại xã giao thông thường -> Short-circuit 0 tools
        if domain_hint in ("voice", "conversation"):
            return []

        CASUAL_STARTS = [
            "xin chao", "chao", "hello", "hi", "cam on", "thanks", "tam biet",
            "bye", "ban la ai", "em la ai", "khoe khong", "the nao", "thoi tiet",
            "ke chuyen", "hat", "ngay mai", "bay gio la", "may gio", "chuc",
        ]
        CASUAL_CONTAINS = [
            "khoe khong", "the nao", "thoi tiet", "ban la ai", "em la ai",
            "tam su", "tro chuyen", "khoe k", "ngay tot lanh", "chuc mung",
        ]
        words = query_clean.split()
        is_casual = (
            (any(query_clean.startswith(cs) for cs in CASUAL_STARTS) and len(words) <= 8)
            or (any(cc in query_clean for cc in CASUAL_CONTAINS) and len(words) <= 10)
        )
        if is_casual:
            logger.debug("[DynamicSkillRouter] Câu đàm thoại xã giao -> 0 tools (TTFT tối ưu).")
            return []

        with self._lock:
            candidate_domains: Set[SkillDomain] = set()

            # Nếu có gợi ý miền từ ngoài
            if domain_hint:
                try:
                    candidate_domains.add(SkillDomain(domain_hint.lower()))
                except ValueError:
                    pass

            # Quét tìm các miền liên quan qua từ khóa
            for domain, keywords in DOMAIN_SIGNATURES.items():
                if any(kw in query_clean for kw in keywords):
                    candidate_domains.add(domain)

            # Nếu không tìm thấy domain khớp cụ thể -> Thử SYSTEM_OPS và GENERAL_TOOLS làm mặc định
            if not candidate_domains:
                candidate_domains.add(SkillDomain.SYSTEM_OPS)
                candidate_domains.add(SkillDomain.GENERAL_TOOLS)

            # Tập hợp các công cụ ứng viên từ các domain khớp (Khử trùng lặp tên tool)
            candidate_tools: List[Dict[str, Any]] = []
            seen_cand_names: Set[str] = set()
            for d in candidate_domains:
                for t in self._domain_schemas.get(d, []):
                    fn_name = (t.get("function") or {}).get("name")
                    if fn_name and fn_name not in seen_cand_names:
                        seen_cand_names.add(fn_name)
                        candidate_tools.append(t)

            # Chấm điểm độ liên quan của từng tool với câu hỏi
            scored: List[Tuple[int, Dict[str, Any]]] = []
            for tool in candidate_tools:
                fn = tool.get("function") or {}
                name = _strip_accents(fn.get("name", ""))
                desc = _strip_accents(fn.get("description", ""))
                score = 0

                # Khớp trực tiếp từng từ trong câu truy vấn
                for w in words:
                    if len(w) >= 3:
                        if w in name:
                            score += 4
                        if w in desc:
                            score += 1

                # Khớp từ khóa đặc trưng của miền
                for d in candidate_domains:
                    for kw in DOMAIN_SIGNATURES.get(d, []):
                        if kw in query_clean and kw in name:
                            score += 5
                        elif kw in query_clean and kw in desc:
                            score += 2

                if score > 0:
                    scored.append((score, tool))

            # Sắp xếp điểm giảm dần
            scored.sort(key=lambda x: x[0], reverse=True)

            # Lấy top max_tools (Đảm bảo tuyệt đối không trùng lặp)
            selected: List[Dict[str, Any]] = []
            seen_sel_names: Set[str] = set()
            for _, t in scored:
                fn_name = (t.get("function") or {}).get("name")
                if fn_name and fn_name not in seen_sel_names:
                    seen_sel_names.add(fn_name)
                    selected.append(t)
                    if len(selected) >= max_tools:
                        break

            # Nếu điểm đều bằng 0 nhưng là lệnh kỹ thuật -> Lấy top max_tools từ candidate_tools
            if not selected and candidate_tools:
                for t in candidate_tools:
                    fn_name = (t.get("function") or {}).get("name")
                    if fn_name and fn_name not in seen_sel_names:
                        seen_sel_names.add(fn_name)
                        selected.append(t)
                        if len(selected) >= max_tools:
                            break

            logger.debug(
                "[DynamicSkillRouter] Query: '%s' -> %d domain(s) [%s] -> %d tools được chọn (giảm từ %d tools)",
                query[:40],
                len(candidate_domains),
                ", ".join(d.value for d in candidate_domains),
                len(selected),
                len(self._tool_cache),
            )
            return selected


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
dynamic_skill_router = DynamicSkillRouter()

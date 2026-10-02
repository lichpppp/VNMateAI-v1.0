"""
src/mateai/application/skills/skill_resolver.py
===============================================
Bộ phân giải miền nghiệp vụ & Lọc động công cụ theo ngữ cảnh (Skill Resolver).

Mục tiêu:
- Tự động phân loại ý định người dùng (Query-to-Domain Intent Classification).
- Casual Chat Short-Circuit: Trả về 0 tools cho các câu đàm thoại thông thường (< 0.05ms, tiết kiệm 100% token).
- Tinh lọc ngữ cảnh: Chỉ nạp đúng 3-5 tools cần thiết vào LLM thay vì nạp toàn bộ 79+ tools.
- Tuân thủ RULE-003: Không truy vấn cơ sở dữ liệu thô.
"""

from __future__ import annotations

import re
import time
import unicodedata
from typing import Dict, List, Optional, Set, Tuple

from mateai.domain.skills.entities import SkillDomain, ToolDefinition
from mateai.domain.skills.registry import ToolRegistry, tool_registry

# Regex nhận diện các câu đàm thoại thông thường cần bỏ qua tools
_CASUAL_CHAT_RE = re.compile(
    r"^(xin ch[àa]o|ch[àa]o (?:b[ạa]n|em|s[ế]p|mate)|hello|hi|ch[ú]c (?:ng[ày] m[ớ]i|bu[ổ]i|ng[ủ] ngon)|"
    r"c[ả]m [ơn]|thank you|thanks|t[ạa]m bi[ệ]t|bye|b[ạa]n l[à] ai|em l[à] ai|gi[ớ]i thi[ệ]u v[ề] b[ả]n th[â]n|"
    r"h[ô]m nay th[ế] n[à]o|b[ạa]n kh[ỏ]e kh[ô]ng|th[ờ]i ti[ế]t(?: h[ô]m nay)? th[ế] n[à]o|k[ể] chuy[ệ]n(?: vui)?|"
    r"h[á]t m[ộ]t b[à]i|b[ạa]n c[ó] th[ể] l[à]m g[ì]|mateai l[à] g[ì])$",
    re.IGNORECASE | re.UNICODE,
)

# Bảng từ khóa đặc trưng cho từng miền nghiệp vụ (đã bỏ dấu)
DOMAIN_SIGNATURES: Dict[SkillDomain, List[str]] = {
    SkillDomain.SYSTEM_OPS: [
        "cpu", "ram", "bo nho", "tien trinh", "process", "kill", "dung tien trinh",
        "he thong", "disk", "o dia", "dung luong", "restart", "khoi dong lai",
        "service", "dich vu", "sysadmin", "task manager", "uptime", "hardware", "tai"
    ],
    SkillDomain.NETWORK_SECURITY: [
        "mang", "network", "ip", "ping", "port", "cong", "wifi", "router", "gateway",
        "ket noi", "an ninh", "security", "threat", "lo hong", "quet", "scan",
        "firewall", "tuong lua", "audit", "ssl", "cert", "dns"
    ],
    SkillDomain.FILE_STORAGE: [
        "file", "tep", "tap tin", "thu muc", "folder", "directory", "doc file",
        "ghi file", "xoa file", "tim kiem file", "duong dan", "path", "luu tru"
    ],
    SkillDomain.DATABASE_ERP: [
        "sql", "database", "co so du lieu", "bang", "query", "select", "insert",
        "doanh thu", "tai chinh", "hoa don", "chi phi", "kpi", "nhan su", "luong",
        "cham cong", "erp", "bao cao tai chinh", "excel", "du lieu"
    ],
    SkillDomain.PC_AUTOMATION: [
        "chup man hinh", "screenshot", "chuot", "mouse", "ban phim", "keyboard",
        "click", "go phim", "mo ung dung", "dong ung dung", "cua so", "window",
        "automation", "gui", "computer use"
    ],
    SkillDomain.KNOWLEDGE_RAG: [
        "tai lieu", "quy trinh", "chinh sach", "quy dinh", "so tay", "huong dan",
        "rag", "tra cuu", "hoi dap", "knowledge", "graphrag", "tri thuc"
    ],
    SkillDomain.ITSM_WORKFLOW: [
        "ticket", "yeu cau", "su co", "incident", "onboard", "nhan vien moi",
        "cap phat", "workflow", "phe duyet", "itsm", "helpdesk"
    ],
    SkillDomain.MULTI_AGENT: [
        "hop giao ban", "standup", "chuyen gia", "phan cong", "hoi y",
        "multi agent", "giam doc tai chinh", "giam doc cong nghe", "cfo", "cto"
    ],
    SkillDomain.VISUAL_MEDIA: [
        "bieu do", "chart", "do thi", "so do", "visualize", "hinh anh", "ve bieu do"
    ],
    SkillDomain.GENERAL_TOOLS: [
        "tien ich", "tinh toan", "may tinh", "chuyen doi", "thoi gian", "ngay thang"
    ],
}


def strip_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để so khớp nhanh."""
    text = text.replace("đ", "d").replace("Đ", "D")
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


class SkillResolver:
    """Bộ phân giải công cụ linh hoạt theo ngữ cảnh truy vấn."""

    def __init__(self, registry: Optional[ToolRegistry] = None):
        self.registry = registry or tool_registry

    def resolve_tools(self, query: str, max_tools: int = 5) -> List[ToolDefinition]:
        """
        Phân giải danh sách công cụ tối ưu cho truy vấn:
        - Nếu là đàm thoại thông thường: Trả về [] ngay lập tức.
        - Nếu là tác vụ kỹ thuật: Chọn lọc các tools thuộc miền có điểm số khớp cao nhất.
        """
        if not query or not query.strip():
            return []

        clean_query = query.strip()
        stripped = strip_accents(clean_query)

        # 1. Casual Chat Short-Circuit (< 0.05ms)
        if _CASUAL_CHAT_RE.search(clean_query) or _CASUAL_CHAT_RE.search(stripped):
            return []

        # 2. Domain Scoring
        domain_scores: Dict[SkillDomain, int] = {}
        for domain, keywords in DOMAIN_SIGNATURES.items():
            score = 0
            for kw in keywords:
                if kw in stripped:
                    score += 2 if len(kw.split()) > 1 else 1
            if score > 0:
                domain_scores[domain] = score

        if not domain_scores:
            # Mặc định lấy các công cụ chung nếu không rõ miền
            return self.registry.get_by_domain(SkillDomain.GENERAL_TOOLS)[:max_tools]

        # Sắp xếp các miền theo điểm liên quan
        sorted_domains = sorted(domain_scores.items(), key=lambda x: x[1], reverse=True)
        primary_domain = sorted_domains[0][0]

        candidates = self.registry.get_by_domain(primary_domain)
        # Nếu chưa đủ quota và có miền phụ
        if len(candidates) < max_tools and len(sorted_domains) > 1:
            secondary_domain = sorted_domains[1][0]
            candidates.extend(self.registry.get_by_domain(secondary_domain))

        return candidates[:max_tools]


# Singleton resolver
skill_resolver = SkillResolver()

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  Edge,
  Node,
  ReactFlowProvider,
  BackgroundVariant,
  MarkerType,
} from 'reactflow';
import dagre from 'dagre';
import 'reactflow/dist/style.css';

import { CoreNode } from './CoreNode';
import { RouterNode } from './RouterNode';
import { AgentNode } from './AgentNode';
import { WorkerNode } from './WorkerNode';
import { ConnectorNode } from './ConnectorNode';
import { CustomModuleNode } from './CustomModuleNode';
import { GlowingEdge } from './GlowingEdge';
import {
  Zap,
  Radio,
  Plus,
  Save,
  RotateCcw,
  Download,
  Upload,
  Sparkles,
  GitFork,
  Trash2,
  X,
  Server,
  Network,
  Maximize2,
  Eye,
  EyeOff,
  Bot,
  Receipt,
  Shield,
  Activity,
  ArrowRight,
  Play,
  CheckCircle2,
  Workflow,
  ChevronRight,
  Info,
  AlertTriangle,
  RefreshCw,
  Send,
  Database,
  Users,
  Building2,
  Mic,
} from 'lucide-react';

// Larger node dimensions for bold readability
const NODE_WIDTH = 340;
const NODE_HEIGHT = 170;

// Compact auto-layout with dagre
const getLayoutedElements = (nodes: Node[], edges: Edge[], direction = 'LR') => {
  const dagreGraph = new dagre.graphlib.Graph();
  dagreGraph.setDefaultEdgeLabel(() => ({}));
  dagreGraph.setGraph({
    rankdir: direction,
    nodesep: 45, // Khoảng cách giữa các node cùng tầng
    ranksep: 95, // Khoảng cách giữa các bước luồng liên tiếp
    marginx: 30,
    marginy: 30,
  });

  nodes.forEach((node) => {
    dagreGraph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  edges.forEach((edge) => {
    dagreGraph.setEdge(edge.source, edge.target);
  });

  dagre.layout(dagreGraph);

  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = dagreGraph.node(node.id);
    return {
      ...node,
      position: {
        x: (nodeWithPosition?.x || 0) - NODE_WIDTH / 2,
        y: (nodeWithPosition?.y || 0) - NODE_HEIGHT / 2,
      },
    };
  });

  return { nodes: layoutedNodes, edges };
};

// Workflow Tab Definition Interface
export interface WorkflowStep {
  step: number;
  nodeId: string;
  title: string;
  subtitle: string;
  role: string;
  color: string;
  badge: string;
}

export interface WorkflowDef {
  id: string;
  title: string;
  tabLabel: string;
  icon: any;
  color: string;
  activeClass: string;
  badgeText: string;
  description: string;
  allowedNodeIds: string[];
  steps: WorkflowStep[];
  simulationSteps: {
    source: string;
    target: string;
    action: string;
    delay: number;
  }[];
}

// 6 Complete Real-life Operational Workflows for Enterprise VN-MateAI
export const WORKFLOW_DEFS: WorkflowDef[] = [
  {
    id: 'all',
    title: 'Toàn Cảnh Hệ Thống & 16 Module Vận Hành',
    tabLabel: 'Toàn Cảnh (16 Modules)',
    icon: Network,
    color: 'text-cyan-400',
    activeClass: 'border-cyan-400/80 bg-cyan-950/70 text-cyan-200 shadow-[0_0_15px_rgba(0,242,254,0.3)]',
    badgeText: '16 MODULES ARCHITECTURE',
    description: 'Hạ tầng toàn diện: Robot Trợ Lý ESP32 & Telegram Bot ➔ AI Agents ➔ VN-MateAI Brain ➔ 9Router & Cụm Worknote ➔ SQLite Database & Active Directory ➔ 5 Cổng Doanh Nghiệp',
    allowedNodeIds: [], // All nodes
    steps: [
      { step: 1, nodeId: 'robot_companion', title: 'Robot Trợ Lý', subtitle: 'ESP32 Smart Companion', role: 'Voice & Loa TTS', color: 'text-amber-400', badge: 'ROBOT' },
      { step: 2, nodeId: 'gateway_telegram', title: 'Telegram Gateway', subtitle: 'Incident & HITL Bot', role: 'Cảnh Báo & Duyệt', color: 'text-sky-400', badge: 'TELEGRAM' },
      { step: 3, nodeId: 'core', title: 'VN-MateAI Brain', subtitle: 'Autonomous Orchestrator', role: 'Điều Phối Trung Tâm', color: 'text-cyan-400', badge: 'BRAIN' },
      { step: 4, nodeId: 'router_9', title: '9Router Gateway', subtitle: 'Multi-LLM Dispatch', role: 'Cân Bằng Tải AI', color: 'text-indigo-400', badge: 'GATEWAY' },
      { step: 5, nodeId: 'worker_cluster', title: 'Worknote Agent', subtitle: 'OpenClaw Cụm Trạm', role: 'Thực Thi RPA', color: 'text-emerald-400', badge: 'WORKERS' },
      { step: 6, nodeId: 'db_sqlite', title: 'SQLite Database', subtitle: 'vnmateai.db ERP', role: 'Dữ Liệu Sổ Cái', color: 'text-teal-400', badge: 'DATABASE' },
      { step: 7, nodeId: 'sync_ad', title: 'Active Directory', subtitle: 'Windows Domain Sync', role: 'Zero-Trust RBAC', color: 'text-blue-400', badge: 'DOMAIN' },
    ],
    simulationSteps: [
      { source: 'robot_companion', target: 'core', action: 'Robot gửi tín hiệu Wake Word "Hey Lyly"', delay: 0 },
      { source: 'core', target: 'router_9', action: '9Router: Điều phối mô hình LLM suy luận', delay: 1100 },
      { source: 'core', target: 'robot_companion', action: 'Brain stream âm thanh Edge-TTS trả về Robot', delay: 2200 },
      { source: 'core', target: 'worker_cluster', action: 'Worknote: Giao việc cụm máy trạm RPA', delay: 3300 },
      { source: 'core', target: 'db_sqlite', action: 'SQLite: Ghi nhận audit log vào vnmateai.db', delay: 4400 },
      { source: 'core', target: 'gateway_telegram', action: 'Telegram: Báo cáo kết quả tới Admin', delay: 5500 },
    ],
  },
  {
    id: 'voice_companion',
    title: 'Luồng Vận Hành: Robot Trợ Lý ESP32 (Voice I2S Mic & Loa Edge-TTS)',
    tabLabel: 'Robot Trợ Lý ESP32',
    icon: Bot,
    color: 'text-amber-400',
    activeClass: 'border-amber-400/80 bg-amber-950/70 text-amber-200 shadow-[0_0_15px_rgba(245,158,11,0.3)]',
    badgeText: 'VOICE ROBOT COMPANION',
    description: 'Đường đi 2 chiều: Người dùng gọi "Hey Lyly" ➔ Mic I2S gửi PCM qua WebSocket ➔ Brain định tuyến 9Router suy luận ➔ Stream âm thanh Edge-TTS về loa phát Robot & đổi biểu cảm OLED ➔ Lưu lịch sử vào SQLite DB',
    allowedNodeIds: ['robot_companion', 'core', 'router_9', 'db_sqlite', 'gateway_telegram'],
    steps: [
      { step: 1, nodeId: 'robot_companion', title: '1. Mic I2S & Wake Word', subtitle: 'Robot Trợ Lý ESP32', role: 'GỬI ÂM THANH (PCM 16kHz)', color: 'text-amber-400', badge: 'MIC I2S' },
      { step: 2, nodeId: 'core', title: '2. VAD / ASR & Phân Tích', subtitle: 'VN-MateAI Brain', role: 'XỬ LÝ NGÔN NGỮ TỰ NHIÊN', color: 'text-cyan-400', badge: 'BRAIN' },
      { step: 3, nodeId: 'router_9', title: '3. 9Router AI Gateway', subtitle: 'Cổng Đa Mô Hình LLM', role: 'SUY LUẬN GEMINI / CLAUDE', color: 'text-indigo-400', badge: 'LLM ROUTER' },
      { step: 4, nodeId: 'robot_companion', title: '4. Loa TTS & Biểu Cảm', subtitle: 'Robot Trợ Lý ESP32', role: 'NHẬN GIỌNG NÓI & OLED HUD', color: 'text-amber-400', badge: 'SPEAKER' },
      { step: 5, nodeId: 'db_sqlite', title: '5. Lưu Vết Hội Thoại', subtitle: 'SQLite Database', role: 'GHI SESSION VNMATEAI.DB', color: 'text-teal-400', badge: 'SQLITE' },
      { step: 6, nodeId: 'gateway_telegram', title: '6. Cảnh Báo Admin', subtitle: 'Telegram Bot Gateway', role: 'THÔNG BÁO TỨC THỜI', color: 'text-sky-400', badge: 'TELEGRAM' },
    ],
    simulationSteps: [
      { source: 'robot_companion', target: 'core', action: 'Robot gửi audio PCM qua WebSocket: "Hey Lyly, báo cáo tình trạng hệ thống"', delay: 0 },
      { source: 'core', target: 'router_9', action: '9Router định tuyến sang Gemini 2.5 Flash xử lý siêu tốc', delay: 1100 },
      { source: 'router_9', target: 'core', action: 'Trả về token suy luận và phân tích cảm xúc', delay: 2200 },
      { source: 'core', target: 'robot_companion', action: 'Brain stream âm thanh Edge-TTS trả về phát ra loa Robot để bàn', delay: 3300 },
      { source: 'core', target: 'db_sqlite', action: 'Ghi session đàm thoại vào SQLite Database vnmateai.db', delay: 4400 },
      { source: 'core', target: 'gateway_telegram', action: 'Bắn tin nhắn tóm tắt sự cố nếu phát hiện cảnh báo', delay: 5500 },
    ],
  },
  {
    id: 'telegram_gateway',
    title: 'Luồng Vận Hành: Telegram Bot Gateway (Cảnh Báo Sự Cố & Duyệt HITL)',
    tabLabel: 'Telegram Bot Gateway',
    icon: Send,
    color: 'text-sky-400',
    activeClass: 'border-sky-400/80 bg-sky-950/70 text-sky-200 shadow-[0_0_15px_rgba(14,165,233,0.3)]',
    badgeText: 'TELEGRAM INCIDENT & HITL',
    description: 'Đường đi 2 chiều: Brain/AIOps phát hiện sự cố khẩn ➔ Gửi tin nhắn tức thời kèm nút Inline Callback sang Telegram Admin ➔ Admin bấm Duyệt/Từ chối ➔ Webhook phản hồi về Brain ➔ Ghi vết bất biến SQLite & Kích hoạt RPA',
    allowedNodeIds: ['core', 'gateway_telegram', 'db_sqlite', 'worker_cluster', 'agent_cto'],
    steps: [
      { step: 1, nodeId: 'core', title: '1. Phát Hiện Sự Cố', subtitle: 'VN-MateAI Brain / AIOps', role: 'KHỞI TẠO CẢNH BÁO / HITL', color: 'text-cyan-400', badge: 'CORE INCIDENT' },
      { step: 2, nodeId: 'gateway_telegram', title: '2. Bắn Tin Nhắn Khẩn', subtitle: 'Telegram Bot Gateway', role: 'GỬI CẢNH BÁO & NÚT DUYỆT', color: 'text-sky-400', badge: 'TELEGRAM PUSH' },
      { step: 3, nodeId: 'gateway_telegram', title: '3. Admin Phê Duyệt', subtitle: 'Telegram Bot Gateway', role: 'NHẬN QUYẾT ĐỊNH (HITL)', color: 'text-sky-400', badge: 'ADMIN CALLBACK' },
      { step: 4, nodeId: 'core', title: '4. Tiếp Nhận Chỉ Đạo', subtitle: 'VN-MateAI Brain', role: 'XỬ LÝ LỆNH PHÊ DUYỆT', color: 'text-cyan-400', badge: 'WEBHOOK RECV' },
      { step: 5, nodeId: 'db_sqlite', title: '5. Ghi Vết Bất Biến', subtitle: 'SQLite Database', role: 'LƯU AUDIT LOG VNMATEAI.DB', color: 'text-teal-400', badge: 'SQLITE AUDIT' },
      { step: 6, nodeId: 'worker_cluster', title: '6. Kích Hoạt Kịch Bản', subtitle: 'Worknote Agent', role: 'THỰC THI SỬA CHỮA HỆ THỐNG', color: 'text-emerald-400', badge: 'RPA REMEDY' },
    ],
    simulationSteps: [
      { source: 'core', target: 'gateway_telegram', action: 'Bắn tin nhắn báo động máy trạm gặp sự cố và nút duyệt HITL', delay: 0 },
      { source: 'gateway_telegram', target: 'core', action: 'Admin bấm nút [Phê Duyệt Khởi Động Lại] qua Webhook Callback', delay: 1200 },
      { source: 'core', target: 'db_sqlite', action: 'Ghi nhận quyết định của Admin vào bảng audit_logs trong SQLite', delay: 2400 },
      { source: 'core', target: 'worker_cluster', action: 'Giao lệnh khởi động lại tiến trình xuống máy trạm Worknote', delay: 3600 },
      { source: 'worker_cluster', target: 'core', action: 'Máy trạm báo cáo hoàn tất khôi phục dịch vụ an toàn', delay: 4800 },
    ],
  },
  {
    id: 'sqlite_db',
    title: 'Luồng Vận Hành: SQLite Database (vnmateai.db - Trung Tâm Dữ Liệu Nội Bộ)',
    tabLabel: 'SQLite Database',
    icon: Database,
    color: 'text-teal-400',
    activeClass: 'border-teal-400/80 bg-teal-950/70 text-teal-200 shadow-[0_0_15px_rgba(20,184,166,0.3)]',
    badgeText: 'SQLITE CENTRAL DATABASE',
    description: 'Kho dữ liệu nội bộ ACID & WAL mode: CFO ghi nhận sổ cái thu chi ERP ➔ HR ghi nhận chấm công & hồ sơ ➔ Brain lưu trữ Session State & Metrics ➔ Worknote ghi nhật ký kiểm toán RPA',
    allowedNodeIds: ['agent_cfo', 'agent_hr', 'core', 'db_sqlite', 'worker_cluster'],
    steps: [
      { step: 1, nodeId: 'agent_cfo', title: '1. Sổ Cái & Dòng Tiền', subtitle: 'CFO Agent', role: 'GHI BẢNG ERP_FINANCES', color: 'text-amber-400', badge: 'CFO ERP' },
      { step: 2, nodeId: 'agent_hr', title: '2. Chấm Công & Nhân Sự', subtitle: 'HR Agent', role: 'GHI BẢNG HR_EMPLOYEES', color: 'text-purple-400', badge: 'HR ATTENDANCE' },
      { step: 3, nodeId: 'core', title: '3. Session & State Hệ Thống', subtitle: 'VN-MateAI Brain', role: 'GHI BẢNG AUDIT_LOGS', color: 'text-cyan-400', badge: 'CORE LOGS' },
      { step: 4, nodeId: 'worker_cluster', title: '4. Nhật Ký RPA Máy Trạm', subtitle: 'Worknote Agent', role: 'GHI BẢNG RPA_EXEC_LOGS', color: 'text-emerald-400', badge: 'WORKNOTE LOG' },
      { step: 5, nodeId: 'db_sqlite', title: '5. Lưu Trữ SQLite WAL Mode', subtitle: 'SQLite Database', role: 'ĐẢM BẢO TOÀN VẸN ACID & READ', color: 'text-teal-400', badge: 'SQLITE WAL' },
    ],
    simulationSteps: [
      { source: 'agent_cfo', target: 'db_sqlite', action: 'CFO ghi nhận 15 bút toán doanh thu mới vào bảng erp_finances', delay: 0 },
      { source: 'agent_hr', target: 'db_sqlite', action: 'HR ghi nhận 48 lượt check-in hôm nay vào bảng hr_employees', delay: 1100 },
      { source: 'core', target: 'db_sqlite', action: 'Brain cập nhật trạng thái hoạt động hệ thống vào audit_logs', delay: 2200 },
      { source: 'worker_cluster', target: 'db_sqlite', action: 'Worknote ghi nhận kịch bản RPA hoàn tất vào rpa_execution_logs', delay: 3300 },
      { source: 'db_sqlite', target: 'core', action: 'Brain đọc lại cấu hình session để phục vụ truy vấn tiếp theo', delay: 4400 },
    ],
  },
  {
    id: 'ad_sync',
    title: 'Luồng Vận Hành: Active Directory / LDAP Sync (Đồng Bộ Domain & Zero-Trust)',
    tabLabel: 'Active Directory Sync',
    icon: Users,
    color: 'text-blue-400',
    activeClass: 'border-blue-400/80 bg-blue-950/70 text-blue-200 shadow-[0_0_15px_rgba(59,130,246,0.3)]',
    badgeText: 'WINDOWS DOMAIN & ZERO-TRUST',
    description: 'Đồng bộ danh bạ doanh nghiệp: Kéo thông tin User & OU từ máy chủ Windows Domain Controller qua LDAP/LDAPS ➔ Brain cập nhật quyền Zero-Trust RBAC ➔ HR Agent quản trị Onboarding/Offboarding ➔ Lưu vnmateai.db ➔ Báo cáo Telegram',
    allowedNodeIds: ['sync_ad', 'core', 'agent_hr', 'db_sqlite', 'gateway_telegram'],
    steps: [
      { step: 1, nodeId: 'sync_ad', title: '1. Domain Controller Sync', subtitle: 'Active Directory / LDAP', role: 'KÉO DANH BẠ LDAP / LDAPS', color: 'text-blue-400', badge: 'LDAP SYNC' },
      { step: 2, nodeId: 'core', title: '2. Phân Tích Cấu Trúc OU', subtitle: 'VN-MateAI Brain', role: 'CẬP NHẬT ZERO-TRUST RBAC', color: 'text-cyan-400', badge: 'RBAC ENGINE' },
      { step: 3, nodeId: 'agent_hr', title: '3. Quản Trị Nhân Sự Mới', subtitle: 'HR Agent', role: 'ONBOARDING / OFFBOARDING', color: 'text-purple-400', badge: 'HR PROVISION' },
      { step: 4, nodeId: 'db_sqlite', title: '4. Lưu Bản Sao Nhân Viên', subtitle: 'SQLite Database', role: 'GHI BẢNG HR_EMPLOYEES', color: 'text-teal-400', badge: 'SQLITE STORE' },
      { step: 5, nodeId: 'gateway_telegram', title: '5. Báo Cáo Hoàn Tất', subtitle: 'Telegram Bot Gateway', role: 'THÔNG BÁO TỚI ADMIN', color: 'text-sky-400', badge: 'TELEGRAM NOTIFY' },
    ],
    simulationSteps: [
      { source: 'sync_ad', target: 'core', action: 'Kéo danh bạ 120 tài khoản và 8 OU từ Windows Domain Controller', delay: 0 },
      { source: 'core', target: 'sync_ad', action: 'Kiểm tra token xác thực và thẩm định chính sách Zero-Trust', delay: 1100 },
      { source: 'agent_hr', target: 'sync_ad', action: 'HR gửi yêu cầu cấp tài khoản cho nhân sự mới gia nhập', delay: 2200 },
      { source: 'agent_hr', target: 'db_sqlite', action: 'Cập nhật hồ sơ nhân sự mới vào SQLite Database vnmateai.db', delay: 3300 },
      { source: 'core', target: 'gateway_telegram', action: 'Gửi báo cáo hoàn tất đồng bộ danh bạ tới Telegram Admin', delay: 4400 },
    ],
  },
  {
    id: 'rpa_execution',
    title: 'Luồng Vận Hành: RPA & Kế Toán Doanh Nghiệp (Cụm Máy Trạm & 5 Cổng)',
    tabLabel: 'RPA & Kế Toán Doanh Nghiệp',
    icon: Server,
    color: 'text-emerald-400',
    activeClass: 'border-emerald-400/80 bg-emerald-950/70 text-emerald-200 shadow-[0_0_15px_rgba(16,185,129,0.3)]',
    badgeText: 'WORKNOTE RPA & 5 CONNECTORS',
    description: 'Thực thi tự động hóa & Tài chính: Brain giao việc cụm Worknote ➔ e-Invoice VN lấy hóa đơn thuế ➔ Paperless OCR bóc tách ➔ CFO Agent đối soát ➔ Ghi sổ vnmateai.db ➔ Đồng bộ Oracle ERP, M365 & S3',
    allowedNodeIds: ['core', 'worker_cluster', 'plugin_einvoice', 'plugin_paperless', 'agent_cfo', 'db_sqlite', 'plugin_m365', 'plugin_aws', 'plugin_oci'],
    steps: [
      { step: 1, nodeId: 'core', title: '1. Phân Bổ Task RPA', subtitle: 'VN-MateAI Brain', role: 'GIAO TASK CỤM TRẠM', color: 'text-cyan-400', badge: 'DISPATCH' },
      { step: 2, nodeId: 'worker_cluster', title: '2. Cụm Máy Trạm Thực Thi', subtitle: 'Worknote Agent', role: 'TỰ ĐỘNG HÓA DESKTOP/WEB', color: 'text-emerald-400', badge: 'RPA EXEC' },
      { step: 3, nodeId: 'plugin_einvoice', title: '3. Hóa Đơn Điện Tử VN', subtitle: 'e-Invoice VN', role: 'TẢI HÓA ĐƠN THUẾ XML', color: 'text-emerald-400', badge: 'TAX INVOICE' },
      { step: 4, nodeId: 'plugin_paperless', title: '4. Bóc Tách OCR', subtitle: 'Paperless-ngx OCR', role: 'TRÍCH XUẤT VĂN BẢN', color: 'text-teal-400', badge: 'OCR ENGINE' },
      { step: 5, nodeId: 'agent_cfo', title: '5. Thẩm Định CFO', subtitle: 'CFO Agent', role: 'ĐỐI SOÁT CÔNG NỢ', color: 'text-amber-400', badge: 'CFO AUDIT' },
      { step: 6, nodeId: 'db_sqlite', title: '6. Sổ Quỹ ERP', subtitle: 'SQLite Database', role: 'GHI SỔ VNMATEAI.DB', color: 'text-teal-400', badge: 'ERP SỔ CÁI' },
      { step: 7, nodeId: 'plugin_oci', title: '7. Oracle Cloud ERP', subtitle: 'Oracle OCI Cloud', role: 'ĐỒNG BỘ ERP TẬP ĐOÀN', color: 'text-rose-400', badge: 'ERP CLOUD' },
    ],
    simulationSteps: [
      { source: 'core', target: 'worker_cluster', action: 'Bắn kịch bản đối chiếu kho hàng xuống cụm máy trạm Worknote', delay: 0 },
      { source: 'plugin_einvoice', target: 'plugin_paperless', action: 'Chuyển 30 hóa đơn điện tử mới sang cụm OCR bóc tách', delay: 1100 },
      { source: 'plugin_paperless', target: 'agent_cfo', action: 'Trả dữ liệu bóc tách bảng kê thuế cho CFO Agent', delay: 2200 },
      { source: 'agent_cfo', target: 'db_sqlite', action: 'CFO ghi nhận bút toán hạch toán vào SQLite Database vnmateai.db', delay: 3300 },
      { source: 'agent_cfo', target: 'plugin_oci', action: 'Đồng bộ chứng từ hợp lệ vào hệ thống Oracle Cloud ERP', delay: 4400 },
      { source: 'worker_cluster', target: 'plugin_m365', action: 'Worknote xuất bảng tính Excel và gửi email báo cáo', delay: 5500 },
      { source: 'worker_cluster', target: 'plugin_aws', action: 'Lưu trữ log kiểm toán và video bằng chứng lên Amazon S3', delay: 6600 },
    ],
  },
];

export default function SystemCanvas() {
  // Master states storing all elements
  const [allNodes, setAllNodes] = useState<Node[]>([]);
  const [allEdges, setAllEdges] = useState<Edge[]>([]);

  // React Flow active states
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);

  // Active Workflow Tab
  const [activeTabId, setActiveTabId] = useState<string>('all');
  const [isSimulatingFlow, setIsSimulatingFlow] = useState(false);

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notification, setNotification] = useState<string | null>(null);
  const [wsConnected, setWsConnected] = useState(false);
  const [layoutDir, setLayoutDir] = useState<'LR' | 'TB'>('LR');
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showMiniMap, setShowMiniMap] = useState(true);

  // Modal State for adding new module
  const [showAddModal, setShowAddModal] = useState(false);
  const [newModuleType, setNewModuleType] = useState('routerNode');
  const [newModuleName, setNewModuleName] = useState('');
  const [newModuleCategory, setNewModuleCategory] = useState('');
  const [newModuleEndpoint, setNewModuleEndpoint] = useState('');

  const fileInputRef = useRef<HTMLInputElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const activeTimersRef = useRef<Record<string, NodeJS.Timeout>>({});

  // Active workflow object
  const activeWorkflow = useMemo(() => {
    return WORKFLOW_DEFS.find((w) => w.id === activeTabId) || WORKFLOW_DEFS[0];
  }, [activeTabId]);

  // Compute set of node IDs that are in ERROR / DISCONNECTED state
  const errorNodeIds = useMemo(() => {
    return new Set(
      nodes
        .filter((n) => {
          const s = (n.data?.status || '').toLowerCase();
          const c = (n.data?.circuitState || '').toLowerCase();
          return (
            s === 'error' ||
            s === 'offline' ||
            s === 'disconnected' ||
            c.includes('open') ||
            Boolean(n.data?.isError) ||
            Boolean(n.data?.hasError)
          );
        })
        .map((n) => n.id)
    );
  }, [nodes]);

  // Dynamically decorate edges: if edge touches any error node, turn edge RED
  const activeEdgesWithErrors = useMemo(() => {
    return edges.map((e) => {
      const isDisconnected = errorNodeIds.has(e.source) || errorNodeIds.has(e.target);
      return {
        ...e,
        data: {
          ...e.data,
          isError: isDisconnected || Boolean(e.data?.isError),
        },
      };
    });
  }, [edges, errorNodeIds]);

  // Toggle Error/Disconnected State on a Node
  const toggleNodeError = useCallback(
    (nodeId: string) => {
      const isCurrentlyError = errorNodeIds.has(nodeId);
      const nextStatus = isCurrentlyError ? 'ready' : 'error';
      const nextErr = !isCurrentlyError;

      const updateNodeData = (nodeList: Node[]) =>
        nodeList.map((n) => {
          if (n.id === nodeId) {
            return {
              ...n,
              data: {
                ...n.data,
                status: nextStatus,
                isError: nextErr,
                hasError: nextErr,
              },
            };
          }
          return n;
        });

      setNodes((prev) => updateNodeData(prev));
      setAllNodes((prev) => updateNodeData(prev));

      // Update selected node if currently open in inspector
      setSelectedNode((prev) => {
        if (prev && prev.id === nodeId) {
          return {
            ...prev,
            data: {
              ...prev.data,
              status: nextStatus,
              isError: nextErr,
              hasError: nextErr,
            },
          };
        }
        return prev;
      });

      if (nextErr) {
        setNotification(`⚠️ Cảnh báo: Phát hiện mất kết nối luồng tại Node [${nodeId}]! Đã hiện thẻ đỏ và dấu X cảnh báo.`);
      } else {
        setNotification(`✅ Đã khôi phục kết nối luồng bình thường cho [${nodeId}].`);
      }
      setTimeout(() => setNotification(null), 4000);
    },
    [errorNodeIds, setNodes]
  );

  // Register all custom node types
  const nodeTypes = useMemo(
    () => ({
      coreNode: CoreNode,
      routerNode: RouterNode,
      agentNode: AgentNode,
      workerNode: WorkerNode,
      connectorNode: ConnectorNode,
      customNode: CustomModuleNode,
    }),
    []
  );

  const edgeTypes = useMemo(
    () => ({
      glowing: GlowingEdge,
    }),
    []
  );

  // Filter elements by workflow tab
  const filterElementsForTab = useCallback(
    (tabId: string, sourceNodes: Node[], sourceEdges: Edge[]) => {
      const tabDef = WORKFLOW_DEFS.find((w) => w.id === tabId) || WORKFLOW_DEFS[0];

      if (tabId === 'all' || tabDef.allowedNodeIds.length === 0) {
        return {
          tabNodes: sourceNodes,
          tabEdges: sourceEdges,
        };
      }

      const allowedSet = new Set(tabDef.allowedNodeIds);
      // Filter nodes
      const filteredNodes = sourceNodes.filter(
        (n) => allowedSet.has(n.id) || (tabId === 'ai_dispatch' && n.type === 'routerNode')
      );

      const nodeIdsSet = new Set(filteredNodes.map((n) => n.id));

      // Filter edges that connect between existing nodes in this tab
      const filteredEdges = sourceEdges.filter(
        (e) => nodeIdsSet.has(e.source) && nodeIdsSet.has(e.target)
      );

      return {
        tabNodes: filteredNodes,
        tabEdges: filteredEdges,
      };
    },
    []
  );

  // Switch Workflow Tab
  const handleSelectTab = useCallback(
    (tabId: string) => {
      setActiveTabId(tabId);
      setSelectedNode(null);

      const { tabNodes, tabEdges } = filterElementsForTab(tabId, allNodes, allEdges);
      const layouted = getLayoutedElements(tabNodes, tabEdges, layoutDir);
      setNodes(layouted.nodes);
      setEdges(layouted.edges);

      const targetTab = WORKFLOW_DEFS.find((w) => w.id === tabId);
      if (targetTab) {
        setNotification(`📂 Đã chuyển sang: ${targetTab.title}`);
        setTimeout(() => setNotification(null), 3000);
      }
    },
    [allNodes, allEdges, filterElementsForTab, layoutDir, setEdges, setNodes]
  );

  // Trigger glowing pulse on edge for 3 seconds
  const activateEdgeFlow = useCallback(
    (sourceId: string, targetId: string) => {
      setEdges((prevEdges) =>
        prevEdges.map((edge) => {
          const match =
            (edge.source === sourceId && edge.target === targetId) ||
            (edge.source === targetId && edge.target === sourceId) ||
            (edge.id.includes(sourceId) && edge.id.includes(targetId));

          if (match) {
            if (activeTimersRef.current[edge.id]) {
              clearTimeout(activeTimersRef.current[edge.id]);
            }

            activeTimersRef.current[edge.id] = setTimeout(() => {
              setEdges((currentEdges) =>
                currentEdges.map((e) =>
                  e.id === edge.id ? { ...e, data: { ...e.data, isActive: false } } : e
                )
              );
              setNodes((currentNodes) =>
                currentNodes.map((n) =>
                  n.id === targetId || n.id === sourceId
                    ? { ...n, data: { ...n.data, isGlowing: false } }
                    : n
                )
              );
            }, 3000);

            return {
              ...edge,
              data: {
                ...edge.data,
                isActive: true,
                activeColor: '#f97316',
              },
            };
          }
          return edge;
        })
      );

      // Trigger glow on target node
      setNodes((prevNodes) =>
        prevNodes.map((node) => {
          if (node.id === targetId || node.id === sourceId) {
            return {
              ...node,
              data: { ...node.data, isGlowing: true },
            };
          }
          return node;
        })
      );
    },
    [setEdges, setNodes]
  );

  // Focus and glow a specific step node
  const handleFocusStepNode = useCallback(
    (nodeId: string) => {
      setNodes((prevNodes) =>
        prevNodes.map((n) => ({
          ...n,
          data: {
            ...n.data,
            isGlowing: n.id === nodeId,
          },
        }))
      );

      const target = nodes.find((n) => n.id === nodeId);
      if (target) {
        setSelectedNode(target);
      }
    },
    [nodes, setNodes]
  );

  // Fetch topology from backend
  const fetchTopology = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/v1/system/topology');
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();

      const rawNodes: Node[] = (data.nodes || []).map((n: any) => ({
        ...n,
        type: n.type || 'connectorNode',
      }));

      const rawEdges: Edge[] = (data.edges || []).map((e: any) => ({
        ...e,
        label: e.label || e.data?.label || '',
        data: {
          ...e.data,
          label: e.data?.label || e.label || '',
        },
        type: 'glowing',
        animated: true,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: '#00f2fe',
          width: 14,
          height: 14,
        },
      }));

      setAllNodes(rawNodes);
      setAllEdges(rawEdges);

      const { tabNodes, tabEdges } = filterElementsForTab(activeTabId, rawNodes, rawEdges);
      const layouted = getLayoutedElements(tabNodes, tabEdges, layoutDir);
      setNodes(layouted.nodes);
      setEdges(layouted.edges);
    } catch (err: any) {
      console.error('[Topology] Fetch failed:', err);
      setError(err?.message || 'Lỗi nạp bản đồ');
      loadFallbackTopology();
    } finally {
      setLoading(false);
    }
  }, [activeTabId, filterElementsForTab, layoutDir, setEdges, setNodes]);

  // Fallback nodes if API is unreachable
  const loadFallbackTopology = () => {
    const defaultNodes: Node[] = [
      {
        id: 'core',
        type: 'coreNode',
        position: { x: 380, y: 240 },
        data: {
          label: 'VN-MateAI Brain',
          status: 'OPERATIONAL',
          activeAgents: 4,
          connectedWorkers: 17,
          engine: 'Orchestrator v2.0',
        },
      },
      {
        id: 'router_9',
        type: 'routerNode',
        position: { x: 760, y: 40 },
        data: {
          label: '9Router AI Gateway',
          status: 'Routing Online',
          latency: '< 12ms',
        },
      },
      {
        id: 'worker_cluster',
        type: 'workerNode',
        position: { x: 760, y: 180 },
        data: {
          label: 'Worknote Agent / OpenClaw',
          onlineCount: 17,
          latency: '< 1.1ms',
        },
      },
      {
        id: 'robot_companion',
        type: 'customNode',
        position: { x: 40, y: -90 },
        data: {
          label: 'Robot Trợ Lý ESP32',
          category: 'VOICE ROBOT COMPANION',
          endpoint: '/ws/xiaozhi',
          status: 'Active (Listening)',
          description: 'Robot để bàn thông minh ESP32: Mic I2S, loa Edge-TTS, wake word "Hey Lyly"',
        },
      },
      {
        id: 'gateway_telegram',
        type: 'connectorNode',
        position: { x: 380, y: -90 },
        data: {
          label: 'Telegram Bot Gateway',
          connectorType: 'telegram',
          circuitState: 'POLLING / WEBHOOK ACTIVE',
          status: 'ready',
          description: 'Cổng thông báo sự cố tức thời tới Telegram Admin và nhận lệnh duyệt HITL',
        },
      },
      {
        id: 'db_sqlite',
        type: 'customNode',
        position: { x: 380, y: 500 },
        data: {
          label: 'SQLite Database',
          category: 'DATABASE (vnmateai.db)',
          endpoint: 'vnmateai.db',
          status: 'Active (Read/Write)',
          description: 'Cơ sở dữ liệu ERP nội bộ: Sổ quỹ, Chấm công, Khách hàng, Audit Log',
        },
      },
      {
        id: 'sync_ad',
        type: 'connectorNode',
        position: { x: 40, y: 610 },
        data: {
          label: 'Active Directory / LDAP',
          connectorType: 'ad_ldap',
          circuitState: 'DOMAIN CONNECTED',
          status: 'ready',
          description: 'Đồng bộ danh bạ người dùng, OU phòng ban và phân quyền Zero-Trust',
        },
      },
      {
        id: 'agent_ceo',
        type: 'agentNode',
        position: { x: 40, y: 50 },
        data: {
          label: 'CEO Router Agent',
          role: 'ceo',
          description: 'Điều phối đa tác nhân, phân loại intent và giám sát SLA',
        },
      },
      {
        id: 'agent_cto',
        type: 'agentNode',
        position: { x: 40, y: 190 },
        data: {
          label: 'CTO / IT AIOps',
          role: 'cto',
          description: 'Hạ tầng mạng LAN, kiểm soát Zero-Trust và giám sát dịch vụ',
        },
      },
      {
        id: 'agent_hr',
        type: 'agentNode',
        position: { x: 40, y: 330 },
        data: {
          label: 'HR Agent',
          role: 'hr',
          description: 'Quản trị nhân sự & chấm công',
        },
      },
      {
        id: 'agent_cfo',
        type: 'agentNode',
        position: { x: 40, y: 470 },
        data: {
          label: 'CFO Agent',
          role: 'cfo',
          description: 'Sổ quỹ, doanh thu & đối soát chi phí',
        },
      },
      {
        id: 'plugin_m365',
        type: 'connectorNode',
        position: { x: 760, y: 320 },
        data: {
          label: 'Microsoft 365',
          connectorType: 'm365',
          circuitState: 'CLOSED (Healthy)',
          status: 'ready',
        },
      },
      {
        id: 'plugin_aws',
        type: 'connectorNode',
        position: { x: 760, y: 460 },
        data: {
          label: 'AWS Cloud Hub',
          connectorType: 'aws',
          circuitState: 'CLOSED (Healthy)',
          status: 'ready',
        },
      },
      {
        id: 'plugin_oci',
        type: 'connectorNode',
        position: { x: 760, y: 600 },
        data: {
          label: 'Oracle OCI Cloud',
          connectorType: 'oci',
          circuitState: 'CLOSED (Healthy)',
          status: 'ready',
        },
      },
      {
        id: 'plugin_paperless',
        type: 'connectorNode',
        position: { x: 760, y: 740 },
        data: {
          label: 'Paperless-ngx OCR',
          connectorType: 'paperless',
          circuitState: 'CLOSED (Healthy)',
          status: 'ready',
        },
      },
      {
        id: 'plugin_einvoice',
        type: 'connectorNode',
        position: { x: 760, y: 880 },
        data: {
          label: 'e-Invoice VN',
          connectorType: 'einvoice',
          circuitState: 'CLOSED (Healthy)',
          status: 'ready',
        },
      },
    ];

    const defaultEdges: Edge[] = [
      { id: 'agent_ceo->core', source: 'agent_ceo', target: 'core', label: 'Chỉ Đạo: Điều Phối Intent', data: { label: 'Chỉ Đạo: Điều Phối Intent', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'agent_cto->core', source: 'agent_cto', target: 'core', label: 'Giám Sát: Mạng & AIOps', data: { label: 'Giám Sát: Mạng & AIOps', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'agent_hr->core', source: 'agent_hr', target: 'core', label: 'Tham Mưu: Chính Sách', data: { label: 'Tham Mưu: Chính Sách', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'agent_cfo->core', source: 'agent_cfo', target: 'core', label: 'Báo Cáo: Doanh Thu ERP', data: { label: 'Báo Cáo: Doanh Thu ERP', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->router_9', source: 'core', target: 'router_9', label: 'Gửi: Prompt Suy Luận', data: { label: 'Gửi: Prompt Suy Luận', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'router_9->core', source: 'router_9', target: 'core', label: 'Nhận: Token Streaming', data: { label: 'Nhận: Token Streaming', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'core->worker_cluster', source: 'core', target: 'worker_cluster', label: 'Gửi: Kịch Bản RPA', data: { label: 'Gửi: Kịch Bản RPA', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'worker_cluster->core', source: 'worker_cluster', target: 'core', label: 'Nhận: Telemetry Trạm', data: { label: 'Nhận: Telemetry Trạm', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'robot_companion->core', source: 'robot_companion', target: 'core', label: 'Gửi: Mic PCM 16kHz', data: { label: 'Gửi: Mic PCM 16kHz', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->robot_companion', source: 'core', target: 'robot_companion', label: 'Nhận: Loa Edge-TTS & OLED', data: { label: 'Nhận: Loa Edge-TTS & OLED', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'core->gateway_telegram', source: 'core', target: 'gateway_telegram', label: 'Gửi: Cảnh Báo & Duyệt HITL', data: { label: 'Gửi: Cảnh Báo & Duyệt HITL', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'gateway_telegram->core', source: 'gateway_telegram', target: 'core', label: 'Nhận: Quyết Định Duyệt HITL', data: { label: 'Nhận: Quyết Định Duyệt HITL', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'core->db_sqlite', source: 'core', target: 'db_sqlite', label: 'Ghi: Audit Log Bất Biến', data: { label: 'Ghi: Audit Log Bất Biến', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'db_sqlite->core', source: 'db_sqlite', target: 'core', label: 'Đọc: Session State', data: { label: 'Đọc: Session State', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'agent_cfo->db_sqlite', source: 'agent_cfo', target: 'db_sqlite', label: 'Ghi: Sổ Cái Kế Toán ERP', data: { label: 'Ghi: Sổ Cái Kế Toán ERP', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'agent_hr->db_sqlite', source: 'agent_hr', target: 'db_sqlite', label: 'Ghi: Chấm Công & Nhân Sự', data: { label: 'Ghi: Chấm Công & Nhân Sự', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'worker_cluster->db_sqlite', source: 'worker_cluster', target: 'db_sqlite', label: 'Ghi: Log RPA Trạm', data: { label: 'Ghi: Log RPA Trạm', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'sync_ad->core', source: 'sync_ad', target: 'core', label: 'Gửi: Danh Bạ User & OU', data: { label: 'Gửi: Danh Bạ User & OU', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'agent_hr->sync_ad', source: 'agent_hr', target: 'sync_ad', label: 'Gửi: Onboard / Offboard', data: { label: 'Gửi: Onboard / Offboard', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->sync_ad', source: 'core', target: 'sync_ad', label: 'Tra Cứu: Quyền Zero-Trust', data: { label: 'Tra Cứu: Quyền Zero-Trust', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'core->plugin_m365', source: 'core', target: 'plugin_m365', label: 'Đồng Bộ: Graph Mail & Lịch', data: { label: 'Đồng Bộ: Graph Mail & Lịch', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->plugin_aws', source: 'core', target: 'plugin_aws', label: 'Quản Trị: Cloud Hub S3', data: { label: 'Quản Trị: Cloud Hub S3', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->plugin_oci', source: 'core', target: 'plugin_oci', label: 'Giám Sát: Oracle OCI', data: { label: 'Giám Sát: Oracle OCI', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->plugin_paperless', source: 'core', target: 'plugin_paperless', label: 'Truy Vấn: Tài Liệu OCR', data: { label: 'Truy Vấn: Tài Liệu OCR', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'core->plugin_einvoice', source: 'core', target: 'plugin_einvoice', label: 'Đồng Bộ: Hóa Đơn Thuế VN', data: { label: 'Đồng Bộ: Hóa Đơn Thuế VN', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'worker_cluster->plugin_m365', source: 'worker_cluster', target: 'plugin_m365', label: 'Xuất: File Excel & Email', data: { label: 'Xuất: File Excel & Email', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'worker_cluster->plugin_aws', source: 'worker_cluster', target: 'plugin_aws', label: 'Lưu: Video Bằng Chứng S3', data: { label: 'Lưu: Video Bằng Chứng S3', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'plugin_einvoice->plugin_paperless', source: 'plugin_einvoice', target: 'plugin_paperless', label: 'Chuyển: Hóa Đơn Sang OCR', data: { label: 'Chuyển: Hóa Đơn Sang OCR', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'plugin_paperless->agent_cfo', source: 'plugin_paperless', target: 'agent_cfo', label: 'Trả: Dữ Liệu Bóc Tách OCR', data: { label: 'Trả: Dữ Liệu Bóc Tách OCR', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'agent_cfo->plugin_oci', source: 'agent_cfo', target: 'plugin_oci', label: 'Đồng Bộ: Sổ Vào Oracle ERP', data: { label: 'Đồng Bộ: Sổ Vào Oracle ERP', direction: 'send' }, type: 'glowing', animated: true },
      { id: 'plugin_aws->agent_cto', source: 'plugin_aws', target: 'agent_cto', label: 'Báo Cáo: Telemetry Cloud', data: { label: 'Báo Cáo: Telemetry Cloud', direction: 'receive' }, type: 'glowing', animated: true },
      { id: 'plugin_oci->agent_cto', source: 'plugin_oci', target: 'agent_cto', label: 'Báo Cáo: Máy Chủ OCI', data: { label: 'Báo Cáo: Máy Chủ OCI', direction: 'receive' }, type: 'glowing', animated: true },
    ];

    setAllNodes(defaultNodes);
    setAllEdges(defaultEdges);

    const layouted = getLayoutedElements(defaultNodes, defaultEdges, layoutDir);
    setNodes(layouted.nodes);
    setEdges(layouted.edges);
  };

  // Re-organize layout
  const handleAutoLayout = (dir: 'LR' | 'TB') => {
    setLayoutDir(dir);
    const layouted = getLayoutedElements(nodes, edges, dir);
    setNodes(layouted.nodes);
    setEdges(layouted.edges);
    setNotification(`📐 Đã căn chỉnh sơ đồ theo hướng ${dir === 'LR' ? 'Ngang (LR)' : 'Dọc (TB)'}`);
    setTimeout(() => setNotification(null), 3000);
  };

  // Save customized topology to backend
  const handleSaveTopology = async () => {
    setSaving(true);
    try {
      const res = await fetch('/api/v1/system/topology/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nodes: allNodes, edges: allEdges }),
      });
      if (res.ok) {
        setNotification('💾 Đã lưu cấu hình sơ đồ topology thành công!');
      } else {
        throw new Error('Lỗi lưu server');
      }
    } catch (e: any) {
      setNotification(`⚠️ ${e?.message || 'Không thể lưu lên server, đã lưu bộ nhớ trình duyệt'}`);
      localStorage.setItem('vnmate_custom_topology', JSON.stringify({ nodes: allNodes, edges: allEdges }));
    } finally {
      setSaving(false);
      setTimeout(() => setNotification(null), 3500);
    }
  };

  // Reset to auto-discovered topology
  const handleResetTopology = async () => {
    if (!confirm('Bạn có chắc muốn khôi phục sơ đồ về mặc định hệ thống?')) return;
    try {
      await fetch('/api/v1/system/topology/reset', { method: 'POST' });
    } catch (e) {}
    localStorage.removeItem('vnmate_custom_topology');
    fetchTopology();
    setNotification('🔄 Đã khôi phục sơ đồ về mặc định!');
    setTimeout(() => setNotification(null), 3000);
  };

  // Export JSON Topology file
  const handleExportJSON = () => {
    const dataStr =
      'data:text/json;charset=utf-8,' +
      encodeURIComponent(JSON.stringify({ nodes: allNodes, edges: allEdges }, null, 2));
    const downloadAnchor = document.createElement('a');
    downloadAnchor.setAttribute('href', dataStr);
    downloadAnchor.setAttribute(
      'download',
      `vnmateai-topology-${new Date().toISOString().slice(0, 10)}.json`
    );
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
    setNotification('📥 Đã xuất file JSON cấu hình!');
    setTimeout(() => setNotification(null), 3000);
  };

  // Import JSON Topology file
  const handleImportJSON = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const parsed = JSON.parse(event.target?.result as string);
        if (parsed.nodes && parsed.edges) {
          setAllNodes(parsed.nodes);
          setAllEdges(parsed.edges);
          const layouted = getLayoutedElements(parsed.nodes, parsed.edges, layoutDir);
          setNodes(layouted.nodes);
          setEdges(layouted.edges);
          setActiveTabId('all');
          setNotification('📤 Đã nhập thành công sơ đồ từ file!');
        }
      } catch (err) {
        alert('File JSON không hợp lệ!');
      }
    };
    reader.readAsText(file);
  };

  // Add a new module dynamically
  const handleAddModule = () => {
    if (!newModuleName.trim()) {
      alert('Vui lòng nhập tên Module!');
      return;
    }

    const newId = `module_${Date.now()}`;
    const newNode: Node = {
      id: newId,
      type: newModuleType,
      position: { x: 400 + Math.random() * 80, y: 150 + Math.random() * 80 },
      data: {
        label: newModuleName,
        category: newModuleCategory || 'ENTERPRISE MODULE',
        endpoint: newModuleEndpoint || 'Ready',
        status: 'Active',
      },
    };

    const newEdge: Edge = {
      id: `core->${newId}`,
      source: 'core',
      target: newId,
      type: 'glowing',
      animated: true,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: '#00f2fe',
        width: 14,
        height: 14,
      },
    };

    setAllNodes((nds) => [...nds, newNode]);
    setAllEdges((eds) => [...eds, newEdge]);
    setNodes((nds) => [...nds, newNode]);
    setEdges((eds) => [...eds, newEdge]);

    setShowAddModal(false);
    setNewModuleName('');
    setNewModuleCategory('');
    setNewModuleEndpoint('');
    setNotification(`✨ Đã thêm module mới: "${newModuleName}"!`);
    setTimeout(() => setNotification(null), 3500);
  };

  // Delete selected node
  const handleDeleteNode = (nodeId: string) => {
    if (nodeId === 'core') {
      alert('Không thể xoá node VN-MateAI Brain trung tâm!');
      return;
    }
    setAllNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setAllEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
    setNodes((nds) => nds.filter((n) => n.id !== nodeId));
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
    setSelectedNode(null);
    setNotification(`🗑️ Đã xoá module khỏi sơ đồ.`);
    setTimeout(() => setNotification(null), 3000);
  };

  // Drag and drop connection between handles
  const onConnect = useCallback(
    (params: Connection) => {
      if (!params.source || !params.target) return;
      const newEdge: Edge = {
        ...params,
        source: params.source,
        target: params.target,
        id: `${params.source}->${params.target}-${Date.now()}`,
        type: 'glowing',
        animated: true,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: '#00f2fe',
          width: 14,
          height: 14,
        },
      };
      setEdges((eds) => addEdge(newEdge, eds));
      setAllEdges((eds) => addEdge(newEdge, eds));
      setNotification(`🔗 Đã tạo liên kết mới: ${params.source} ➔ ${params.target}`);
      setTimeout(() => setNotification(null), 3500);
    },
    [setEdges]
  );

  // Setup WebSocket connection
  useEffect(() => {
    let reconnectTimeout: any;
    let socket: WebSocket | null = null;

    const connectWs = () => {
      try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        const wsUrl = `${protocol}//${host}/ws/topology`;

        socket = new WebSocket(wsUrl);
        wsRef.current = socket;

        socket.onopen = () => setWsConnected(true);
        socket.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);
            if (msg.event === 'tool_executed' || msg.type === 'tool_executed') {
              const source = msg.source || 'core';
              const target = msg.target || 'router_9';
              setNotification(`⚡ [Real-time] ${source} ➔ ${target} (${msg.action || 'Live Flow'})`);
              activateEdgeFlow(source, target);
            }
          } catch (e) {}
        };
        socket.onclose = () => {
          setWsConnected(false);
          reconnectTimeout = setTimeout(connectWs, 3000);
        };
        socket.onerror = () => setWsConnected(false);
      } catch (err) {}
    };

    connectWs();
    fetchTopology();

    return () => {
      if (socket) socket.close();
      clearTimeout(reconnectTimeout);
      Object.values(activeTimersRef.current).forEach((t) => clearTimeout(t));
    };
  }, [activateEdgeFlow, fetchTopology]);

  // Single simulation trigger
  const triggerSimulation = async (source: string, target: string, actionName: string) => {
    setNotification(`🚀 Luồng: ${source} ➔ ${target} (${actionName})`);
    activateEdgeFlow(source, target);

    try {
      await fetch('/api/v1/system/topology/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, target, action: actionName }),
      });
    } catch (e) {}
  };

  // Run full multi-step workflow simulation for the active tab
  const handleRunWorkflowSimulation = async () => {
    if (isSimulatingFlow) return;
    setIsSimulatingFlow(true);
    setNotification(`⚡ Đang kích hoạt tuần tự luồng: ${activeWorkflow.title}...`);

    const steps = activeWorkflow.simulationSteps;
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      setTimeout(async () => {
        setNotification(
          `🚀 [Bước ${i + 1}/${steps.length}] ${s.source} ➔ ${s.target}: ${s.action}`
        );
        activateEdgeFlow(s.source, s.target);
        try {
          await fetch('/api/v1/system/topology/trigger', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source: s.source, target: s.target, action: s.action }),
          });
        } catch (e) {}

        if (i === steps.length - 1) {
          setTimeout(() => {
            setIsSimulatingFlow(false);
            setNotification(`✅ Đã hoàn tất luồng: ${activeWorkflow.title}!`);
            setTimeout(() => setNotification(null), 3000);
          }, 1500);
        }
      }, s.delay);
    }
  };

  // Check if selected node is currently in error
  const isSelectedNodeError = selectedNode ? errorNodeIds.has(selectedNode.id) : false;

  return (
    <div className="relative w-full h-screen bg-[#01060e] overflow-hidden flex flex-col">
      {/* Hidden File Input for JSON Import */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleImportJSON}
        accept=".json"
        className="hidden"
      />

      {/* 1. TOP SLIM COMMAND BAR & WORKFLOW TAB SELECTOR */}
      <div className="z-20 flex-shrink-0 flex flex-col bg-slate-950/95 backdrop-blur-2xl border-b border-cyan-500/30 shadow-xl">
        {/* Row 1: Brand, Tabs & Global Actions (48px) */}
        <div className="h-12 flex items-center justify-between px-3 md:px-4 border-b border-slate-800/80">
          {/* Left: Brand Icon & System Title */}
          <div className="flex items-center gap-2 mr-2">
            <div className="p-1.5 rounded-lg bg-cyan-500/20 border border-cyan-400/40 text-cyan-300">
              <Network className="w-4 h-4 animate-pulse" />
            </div>
            <div className="hidden lg:flex flex-col">
              <span className="font-orbitron font-extrabold text-xs text-cyan-300 tracking-wider">
                VN-MATEAI
              </span>
              <span className="text-[9px] font-mono text-slate-400 tracking-tight">
                TOPOLOGY WORKFLOW
              </span>
            </div>
          </div>

          {/* Center: Dynamic Cyberpunk Workflow Tabs */}
          <div className="flex items-center gap-1.5 overflow-x-auto py-1 px-1 rounded-xl bg-slate-900/90 border border-slate-800 scrollbar-none">
            {WORKFLOW_DEFS.map((w) => {
              const IconComp = w.icon;
              const isActive = activeTabId === w.id;
              // Check if any node in this tab's steps is currently in error
              const hasTabError = w.steps.some((st) => errorNodeIds.has(st.nodeId));

              return (
                <button
                  key={w.id}
                  onClick={() => handleSelectTab(w.id)}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-mono font-bold transition-all whitespace-nowrap border ${
                    hasTabError
                      ? 'border-rose-500/80 bg-rose-950/60 text-rose-300 shadow-[0_0_15px_rgba(244,63,94,0.3)] animate-pulse'
                      : isActive
                      ? w.activeClass
                      : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                  }`}
                >
                  <IconComp className={`w-3.5 h-3.5 ${hasTabError ? 'text-rose-400' : isActive ? w.color : 'text-slate-400'}`} />
                  <span>{w.tabLabel}</span>
                  {hasTabError ? (
                    <span className="flex items-center justify-center w-3.5 h-3.5 rounded-full bg-rose-600 text-white text-[9px] font-bold">
                      !
                    </span>
                  ) : isActive ? (
                    <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-ping ml-0.5" />
                  ) : null}
                </button>
              );
            })}
          </div>

          {/* Right: Quick Action Controls */}
          <div className="flex items-center gap-1.5 ml-2">
            {/* Add Module */}
            <button
              onClick={() => setShowAddModal(true)}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-mono text-xs font-bold shadow-[0_0_12px_rgba(0,242,254,0.3)] transition"
              title="Thêm module mới vào sơ đồ"
            >
              <Plus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Thêm Module</span>
            </button>

            {/* Orientation switch */}
            <div className="hidden sm:flex items-center rounded-lg bg-slate-900 border border-slate-800 p-0.5 text-xs font-mono">
              <button
                onClick={() => handleAutoLayout('LR')}
                className={`px-2 py-0.5 rounded text-[11px] font-bold ${layoutDir === 'LR' ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40' : 'text-slate-400'}`}
                title="Sắp xếp dạng Ngang (Trái sang Phải)"
              >
                Ngang
              </button>
              <button
                onClick={() => handleAutoLayout('TB')}
                className={`px-2 py-0.5 rounded text-[11px] font-bold ${layoutDir === 'TB' ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40' : 'text-slate-400'}`}
                title="Sắp xếp dạng Dọc (Trên xuống Dưới)"
              >
                Dọc
              </button>
            </div>

            {/* Save Topology */}
            <button
              onClick={handleSaveTopology}
              disabled={saving}
              className="flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-950/80 hover:bg-emerald-900 border border-emerald-500/40 text-emerald-300 text-xs font-mono font-semibold transition"
              title="Lưu lại cấu hình sơ đồ"
            >
              <Save className={`w-3.5 h-3.5 ${saving ? 'animate-spin' : ''}`} />
              <span className="hidden md:inline">Lưu</span>
            </button>

            {/* Export JSON */}
            <button
              onClick={handleExportJSON}
              className="p-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 transition"
              title="Xuất file JSON"
            >
              <Download className="w-3.5 h-3.5" />
            </button>

            {/* Import JSON */}
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-300 transition"
              title="Nhập file JSON"
            >
              <Upload className="w-3.5 h-3.5" />
            </button>

            {/* MiniMap Toggle */}
            <button
              onClick={() => setShowMiniMap(!showMiniMap)}
              className={`p-1.5 rounded-lg border transition ${showMiniMap ? 'bg-cyan-950/60 border-cyan-500/40 text-cyan-300' : 'bg-slate-900 border-slate-700 text-slate-400'}`}
              title={showMiniMap ? 'Ẩn MiniMap' : 'Hiện MiniMap'}
            >
              {showMiniMap ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
            </button>

            {/* Reset */}
            <button
              onClick={handleResetTopology}
              className="p-1.5 rounded-lg bg-slate-900 hover:bg-slate-800 border border-slate-700 text-slate-400 hover:text-rose-400 transition"
              title="Khôi phục mặc định"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>

            {/* WebSocket Live */}
            <div className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-900 border border-slate-800 text-[11px] font-mono">
              <span
                className={`h-2 w-2 rounded-full ${
                  wsConnected ? 'bg-emerald-400 animate-pulse shadow-[0_0_8px_#10b981]' : 'bg-rose-500'
                }`}
              />
              <span className={wsConnected ? 'text-emerald-400 font-bold' : 'text-rose-400'}>
                {wsConnected ? 'Live' : 'Off'}
              </span>
            </div>
          </div>
        </div>

        {/* Row 2: DETAILED WORKFLOW PIPELINE CONNECTION BAR (Từ đâu đến đâu, module nào kết nối với nhau) */}
        <div className="min-h-[46px] py-1.5 px-3 md:px-4 bg-slate-950/80 flex flex-wrap items-center justify-between gap-2 text-xs">
          {/* Left: Active Tab Badge & Stepper Chain */}
          <div className="flex items-center gap-2 overflow-x-auto scrollbar-none py-0.5">
            <span className="px-2 py-0.5 rounded bg-slate-900 border border-cyan-500/40 text-[10px] font-mono font-bold text-cyan-400 whitespace-nowrap tracking-wider">
              {activeWorkflow.badgeText}
            </span>

            {/* Sequential Steps (Nối từ đầu đến cuối) */}
            <div className="flex items-center gap-1.5">
              {activeWorkflow.steps.map((st, idx) => {
                const isStepError = errorNodeIds.has(st.nodeId);

                return (
                  <React.Fragment key={st.step}>
                    <button
                      onClick={() => handleFocusStepNode(st.nodeId)}
                      className={`group flex items-center gap-1.5 px-2.5 py-1 rounded-lg border transition-all text-left ${
                        isStepError
                          ? 'bg-rose-950/90 border-rose-500 text-rose-300 ring-1 ring-rose-500/50 shadow-[0_0_15px_rgba(244,63,94,0.4)]'
                          : 'bg-slate-900/90 hover:bg-slate-800 border-slate-800 hover:border-cyan-500/50'
                      }`}
                      title={isStepError ? `CẢNH BÁO: Node "${st.title}" đang mất kết nối!` : `Click để định vị Node "${st.title}"`}
                    >
                      <span
                        className={`flex items-center justify-center w-4 h-4 rounded-full border text-[10px] font-mono font-bold ${
                          isStepError
                            ? 'bg-rose-600 border-white text-white animate-bounce'
                            : 'bg-cyan-950 border-cyan-400/50 text-cyan-300'
                        }`}
                      >
                        {isStepError ? '✕' : st.step}
                      </span>
                      <div className="flex flex-col">
                        <span
                          className={`text-[11px] font-bold font-mono leading-none ${
                            isStepError ? 'text-rose-300' : st.color
                          }`}
                        >
                          {st.title}
                        </span>
                        <span
                          className={`text-[9px] font-mono mt-0.5 leading-none ${
                            isStepError ? 'text-rose-400 font-extrabold' : 'text-slate-400'
                          }`}
                        >
                          {isStepError ? 'MẤT KẾT NỐI ❌' : st.role}
                        </span>
                      </div>
                    </button>

                    {idx < activeWorkflow.steps.length - 1 && (
                      <div className="flex items-center text-slate-600 px-0.5">
                        <ChevronRight
                          className={`w-3.5 h-3.5 ${
                            isStepError
                              ? 'text-rose-500 animate-ping'
                              : 'text-cyan-500/60 animate-pulse'
                          }`}
                        />
                      </div>
                    )}
                  </React.Fragment>
                );
              })}
            </div>
          </div>

          {/* Right: Simulate This Tab's Workflow Button */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleRunWorkflowSimulation}
              disabled={isSimulatingFlow}
              className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-mono font-bold transition shadow-lg ${
                isSimulatingFlow
                  ? 'bg-amber-950/80 border border-amber-500/60 text-amber-300 animate-pulse'
                  : 'bg-gradient-to-r from-orange-600 to-amber-600 hover:from-orange-500 hover:to-amber-500 text-white shadow-[0_0_15px_rgba(249,115,22,0.4)]'
              }`}
            >
              <Zap className={`w-3.5 h-3.5 ${isSimulatingFlow ? 'animate-spin text-amber-300' : 'text-yellow-200'}`} />
              <span>{isSimulatingFlow ? 'Đang Chạy Luồng...' : '⚡ Bắn Luồng Test Tab Này'}</span>
            </button>
          </div>
        </div>
      </div>

      {/* 2. REAL-TIME TOAST NOTIFICATION */}
      {notification && (
        <div
          className={`absolute top-28 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-xl border font-mono text-xs shadow-2xl backdrop-blur-xl flex items-center gap-2 animate-bounce ${
            notification.includes('Cảnh báo') || notification.includes('mất kết nối')
              ? 'bg-rose-950/95 border-rose-400 text-rose-200 shadow-[0_0_30px_rgba(244,63,94,0.6)]'
              : 'bg-slate-900/95 border-cyan-400 text-cyan-300 shadow-[0_0_25px_rgba(0,242,254,0.45)]'
          }`}
        >
          {notification.includes('Cảnh báo') ? (
            <AlertTriangle className="w-4 h-4 text-rose-400 animate-ping" />
          ) : (
            <Sparkles className="w-4 h-4 text-cyan-400 animate-spin" />
          )}
          <span>{notification}</span>
        </div>
      )}

      {/* 3. SELECTED NODE DETAIL INSPECTOR PANEL (Top Right Float) */}
      {selectedNode && (
        <div
          className={`absolute top-28 right-4 z-40 w-80 p-4 rounded-2xl backdrop-blur-2xl border-2 shadow-2xl text-white ${
            isSelectedNodeError
              ? 'bg-slate-950/95 border-rose-500 shadow-[0_0_35px_rgba(244,63,94,0.5)] ring-1 ring-rose-500/50'
              : 'bg-slate-950/95 border-cyan-400/80 shadow-[0_0_30px_rgba(0,242,254,0.3)]'
          }`}
        >
          <div className="flex items-center justify-between pb-3 mb-3 border-b border-slate-800">
            <div className="flex items-center gap-2">
              <span
                className={`h-2.5 w-2.5 rounded-full ${
                  isSelectedNodeError ? 'bg-rose-500 animate-ping' : 'bg-cyan-400 animate-ping'
                }`}
              ></span>
              <h3
                className={`font-orbitron text-xs font-bold uppercase tracking-wider ${
                  isSelectedNodeError ? 'text-rose-400' : 'text-cyan-300'
                }`}
              >
                {isSelectedNodeError ? 'CẢNH BÁO SỰ CỐ LUỒNG' : 'THÔNG TIN MODULE'}
              </h3>
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              className="text-slate-400 hover:text-white p-1 rounded hover:bg-slate-800"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Error Banner in Inspector if Node is Disconnected */}
          {isSelectedNodeError && (
            <div className="mb-3 p-2.5 rounded-xl bg-rose-950/90 border border-rose-500 text-rose-200 text-xs flex items-start gap-2 shadow-[0_0_15px_rgba(244,63,94,0.3)]">
              <X className="w-4 h-4 text-rose-400 flex-shrink-0 mt-0.5 stroke-[3]" />
              <div>
                <div className="font-bold text-rose-300">ĐỨT GÃY KẾT NỐI LUỒNG!</div>
                <div className="text-[11px] text-rose-300/80 mt-0.5 leading-snug">
                  Module này không có tín hiệu phản hồi. Các đường dây liên kết đã chuyển sang cảnh báo đỏ đứt nét.
                </div>
              </div>
            </div>
          )}

          <div className="space-y-2 text-xs font-mono">
            <div>
              <span className="text-slate-400">ID: </span>
              <span className={`font-bold ${isSelectedNodeError ? 'text-rose-400' : 'text-cyan-400'}`}>
                {selectedNode.id}
              </span>
            </div>
            <div>
              <span className="text-slate-400">Tên: </span>
              <span className="text-white font-semibold">{selectedNode.data?.label}</span>
            </div>
            <div>
              <span className="text-slate-400">Phân loại: </span>
              <span className="text-indigo-400">{selectedNode.type}</span>
            </div>
            <div>
              <span className="text-slate-400">Trạng thái: </span>
              <span
                className={`font-bold uppercase ${
                  isSelectedNodeError ? 'text-rose-400' : 'text-emerald-400'
                }`}
              >
                {isSelectedNodeError ? '❌ MẤT KẾT NỐI (DISCONNECTED)' : selectedNode.data?.status || 'Active Online'}
              </span>
            </div>
            {selectedNode.data?.description && (
              <div className="pt-2 text-slate-300 text-[11px] leading-relaxed border-t border-slate-800/80">
                {selectedNode.data.description}
              </div>
            )}

            {/* Detailed Send and Receive Pathways for Selected Node */}
            <div className="pt-2.5 mt-2 border-t border-slate-800 space-y-2">
              <div className="text-[10px] font-bold text-cyan-400 uppercase tracking-wider">
                📡 Chiều Gửi Đi (Outgoing):
              </div>
              <div className="space-y-1 max-h-24 overflow-y-auto pr-1">
                {edges.filter((e) => e.source === selectedNode.id).length === 0 ? (
                  <div className="text-[10px] text-slate-500 italic">Không có luồng gửi đi trực tiếp</div>
                ) : (
                  edges
                    .filter((e) => e.source === selectedNode.id)
                    .map((e) => (
                      <div key={e.id} className="p-1 rounded bg-slate-900 border border-slate-800 text-[10px] flex items-center justify-between gap-1">
                        <span className="text-cyan-300 font-semibold truncate">➔ {e.target}</span>
                        <span className="text-slate-400 truncate max-w-[120px]">{e.data?.label || e.label || 'Truyền tải'}</span>
                      </div>
                    ))
                )}
              </div>

              <div className="text-[10px] font-bold text-amber-400 uppercase tracking-wider pt-1">
                📥 Chiều Nhận Về (Incoming):
              </div>
              <div className="space-y-1 max-h-24 overflow-y-auto pr-1">
                {edges.filter((e) => e.target === selectedNode.id).length === 0 ? (
                  <div className="text-[10px] text-slate-500 italic">Không có luồng nhận về trực tiếp</div>
                ) : (
                  edges
                    .filter((e) => e.target === selectedNode.id)
                    .map((e) => (
                      <div key={e.id} className="p-1 rounded bg-slate-900 border border-slate-800 text-[10px] flex items-center justify-between gap-1">
                        <span className="text-amber-300 font-semibold truncate">⬅️ {e.source}</span>
                        <span className="text-slate-400 truncate max-w-[120px]">{e.data?.label || e.label || 'Truyền tải'}</span>
                      </div>
                    ))
                )}
              </div>
            </div>
          </div>

          <div className="mt-4 pt-3 border-t border-slate-800 space-y-2">
            {/* Toggle Error Simulation Button */}
            <button
              onClick={() => toggleNodeError(selectedNode.id)}
              className={`w-full py-1.5 px-3 rounded-lg text-xs font-mono font-bold transition flex items-center justify-center gap-1.5 border shadow-md ${
                isSelectedNodeError
                  ? 'bg-emerald-950/90 hover:bg-emerald-900 border-emerald-500/60 text-emerald-300'
                  : 'bg-rose-950/80 hover:bg-rose-900 border-rose-500/60 text-rose-300'
              }`}
            >
              {isSelectedNodeError ? (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Khôi Phục Kết Nối Luồng</span>
                </>
              ) : (
                <>
                  <AlertTriangle className="w-3.5 h-3.5 text-rose-400" />
                  <span>Giả Lập Mất Kết Nối (Báo Đỏ)</span>
                </>
              )}
            </button>

            <div className="flex items-center gap-2">
              <button
                onClick={() =>
                  triggerSimulation(
                    selectedNode.id,
                    'core',
                    `Kiểm tra tín hiệu: ${selectedNode.data?.label || selectedNode.id}`
                  )
                }
                className="flex-1 py-1.5 px-3 rounded-lg bg-cyan-500/20 hover:bg-cyan-500/30 border border-cyan-400/50 text-cyan-300 text-xs font-mono font-bold transition flex items-center justify-center gap-1.5"
              >
                <Zap className="w-3.5 h-3.5" /> Bắn Luồng Test
              </button>

              {selectedNode.id !== 'core' && (
                <button
                  onClick={() => handleDeleteNode(selectedNode.id)}
                  className="p-1.5 rounded-lg bg-rose-950/80 hover:bg-rose-900 border border-rose-500/40 text-rose-300"
                  title="Xoá module này"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 4. FULL VIEWPORT REACT FLOW CANVAS (Maximizes Main Content) */}
      <div className="flex-1 w-full relative">
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={activeEdgesWithErrors}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedNode(node)}
            onPaneClick={() => setSelectedNode(null)}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            fitViewOptions={{ padding: 0.08, maxZoom: 1.25, minZoom: 0.35 }}
            attributionPosition="bottom-left"
            minZoom={0.25}
            maxZoom={2.2}
          >
            {/* Cyber Dot Background */}
            <Background
              variant={BackgroundVariant.Dots}
              gap={24}
              size={1.5}
              color="#00f2fe25"
              className="bg-[#01060e]"
            />

            {/* Flow Controls (Bottom Left) */}
            <Controls className="!bg-slate-950/90 !border-slate-800 !rounded-xl !shadow-2xl overflow-hidden [&>button]:!bg-transparent [&>button]:!border-b [&>button]:!border-slate-800 [&>button]:!fill-cyan-400 hover:[&>button]:!bg-slate-900" />

            {/* Optional MiniMap (Bottom Right) */}
            {showMiniMap && (
              <MiniMap
                className="!bg-slate-950/90 !border !border-cyan-500/30 !rounded-xl !shadow-2xl"
                nodeStrokeColor="#00f2fe"
                nodeColor={(n) => {
                  if (errorNodeIds.has(n.id)) return '#f43f5e';
                  if (n.type === 'coreNode') return '#00f2fe';
                  if (n.type === 'routerNode') return '#818cf8';
                  if (n.type === 'agentNode') return '#a855f7';
                  if (n.type === 'workerNode') return '#10b981';
                  return '#f59e0b';
                }}
                nodeBorderRadius={6}
                maskColor="rgba(1, 6, 14, 0.75)"
              />
            )}
          </ReactFlow>
        </ReactFlowProvider>
      </div>

      {/* 5. QUICK SIMULATION TRIGGER FLOATING BAR (Docked at Bottom Center) */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex flex-wrap items-center gap-1.5 p-1.5 px-3 rounded-2xl bg-slate-950/90 backdrop-blur-xl border border-cyan-500/40 shadow-2xl">
        <span className="text-[10px] font-mono font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1 pr-1">
          <Zap className="w-3.5 h-3.5 text-amber-400" /> Thao Tác Nhanh:
        </span>

        {/* Robot Trợ Lý ESP32 Button */}
        <button
          onClick={() => triggerSimulation('robot_companion', 'core', 'Robot ESP32: Voice Wake Word "Hey Lyly"')}
          className="px-2.5 py-1 text-[11px] font-mono font-bold rounded-lg bg-amber-950/80 hover:bg-amber-900 border border-amber-500/50 text-amber-300 hover:text-white transition flex items-center gap-1 shadow-[0_0_12px_rgba(245,158,11,0.3)]"
          title="Bắn luồng âm thanh thoại từ Robot ESP32 tới Core Brain"
        >
          <Bot className="w-3 h-3 text-amber-400" />
          <span>Robot ESP32</span>
        </button>

        {/* Telegram Bot Gateway Button */}
        <button
          onClick={() => triggerSimulation('core', 'gateway_telegram', 'Telegram Gateway: Báo Cáo & Duyệt HITL')}
          className="px-2.5 py-1 text-[11px] font-mono font-bold rounded-lg bg-sky-950/80 hover:bg-sky-900 border border-sky-500/50 text-sky-300 hover:text-white transition flex items-center gap-1 shadow-[0_0_12px_rgba(14,165,233,0.3)]"
          title="Gửi cảnh báo và phê duyệt HITL tới nhóm Admin Telegram"
        >
          <Send className="w-3 h-3 text-sky-400" />
          <span>Telegram</span>
        </button>

        {/* SQLite Database Button */}
        <button
          onClick={() => triggerSimulation('core', 'db_sqlite', 'SQLite DB: Ghi Audit Log & ERP State')}
          className="px-2.5 py-1 text-[11px] font-mono font-medium rounded-lg bg-teal-950/70 hover:bg-teal-900 border border-teal-500/40 text-teal-300 hover:text-white transition flex items-center gap-1"
          title="Lưu dữ liệu vào vnmateai.db nội bộ"
        >
          <Database className="w-3 h-3 text-teal-400" />
          <span>SQLite DB</span>
        </button>

        {/* Active Directory Sync Button */}
        <button
          onClick={() => triggerSimulation('sync_ad', 'core', 'Active Directory: Đồng Bộ Danh Bạ User & OU')}
          className="px-2.5 py-1 text-[11px] font-mono font-medium rounded-lg bg-indigo-950/70 hover:bg-indigo-900 border border-indigo-500/40 text-indigo-300 hover:text-white transition flex items-center gap-1"
          title="Đồng bộ danh bạ từ Windows Active Directory"
        >
          <Users className="w-3 h-3 text-indigo-400" />
          <span>AD Sync</span>
        </button>

        {/* Mock Disconnect / Error Alert Button */}
        <button
          onClick={() => {
            const targetId = activeWorkflow.steps[0]?.nodeId || 'robot_companion';
            toggleNodeError(targetId);
          }}
          className="px-2.5 py-1 text-[11px] font-mono font-bold rounded-lg bg-rose-950/80 hover:bg-rose-900 border border-rose-500/60 text-rose-300 hover:text-white transition flex items-center gap-1 shadow-[0_0_12px_rgba(244,63,94,0.3)]"
          title="Giả lập mất kết nối để xem cảnh báo đỏ và dấu X trực quan"
        >
          <AlertTriangle className="w-3 h-3 text-rose-400" />
          <span>⚠️ Test Lỗi (Báo Đỏ)</span>
        </button>
      </div>

      {/* 6. MODAL: ADD CUSTOM ENTERPRISE MODULE */}
      {showAddModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md">
          <div className="relative w-full max-w-md p-6 rounded-2xl bg-slate-950 border-2 border-cyan-500/80 shadow-[0_0_50px_rgba(0,242,254,0.4)]">
            <div className="flex items-center justify-between pb-3 mb-4 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <Plus className="w-5 h-5 text-cyan-400" />
                <h3 className="font-orbitron text-sm font-bold text-cyan-300 tracking-wider">
                  THÊM MODULE TÙY BIẾN
                </h3>
              </div>
              <button
                onClick={() => setShowAddModal(false)}
                className="text-slate-400 hover:text-white p-1 rounded"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-mono text-slate-300 mb-1.5">
                  Phân Loại Module (Type)
                </label>
                <select
                  value={newModuleType}
                  onChange={(e) => setNewModuleType(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono text-cyan-300 focus:outline-none focus:border-cyan-400"
                >
                  <option value="routerNode">9Router AI Gateway / Cognitive Service</option>
                  <option value="workerNode">Worker RPA / OpenClaw Node</option>
                  <option value="connectorNode">Enterprise Connector (Cloud / API / ERP)</option>
                  <option value="agentNode">Chuyên Gia AI (Specialist Agent)</option>
                  <option value="customNode">Custom Module Mở Rộng</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-mono text-slate-300 mb-1.5">
                  Tên Module (Label)
                </label>
                <input
                  type="text"
                  placeholder="Ví dụ: 9Router Backup Cluster, CRM Hub..."
                  value={newModuleName}
                  onChange={(e) => setNewModuleName(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono text-white focus:outline-none focus:border-cyan-400"
                />
              </div>

              <div>
                <label className="block text-xs font-mono text-slate-300 mb-1.5">
                  Nhóm / Category
                </label>
                <input
                  type="text"
                  placeholder="Ví dụ: AI Gateway, RPA Fleet, Database, ERP..."
                  value={newModuleCategory}
                  onChange={(e) => setNewModuleCategory(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono text-white focus:outline-none focus:border-cyan-400"
                />
              </div>

              <div>
                <label className="block text-xs font-mono text-slate-300 mb-1.5">
                  Mô Tả / Endpoint (Tùy chọn)
                </label>
                <input
                  type="text"
                  placeholder="Ví dụ: https://router.vnmate.internal/v1"
                  value={newModuleEndpoint}
                  onChange={(e) => setNewModuleEndpoint(e.target.value)}
                  className="w-full px-3 py-2 rounded-lg bg-slate-900 border border-slate-700 text-sm font-mono text-white focus:outline-none focus:border-cyan-400"
                />
              </div>
            </div>

            <div className="mt-6 flex items-center justify-end gap-3 pt-3 border-t border-slate-800">
              <button
                onClick={() => setShowAddModal(false)}
                className="px-4 py-2 rounded-lg text-xs font-mono text-slate-400 hover:text-white"
              >
                Hủy
              </button>
              <button
                onClick={handleAddModule}
                className="px-4 py-2 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-950 font-bold font-mono text-xs shadow-[0_0_15px_rgba(0,242,254,0.4)] transition"
              >
                Thêm Vào Sơ Đồ
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

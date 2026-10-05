'use client';

/**
 * Sơ đồ giám sát thời gian thực (docs/realtime/topology-plan.md).
 *
 * - Ô = thành phần thật từ `GET /api/v1/system/topology`, trạng thái
 *   ok / degraded / down / off / unknown kèm số đo và lý do; không có số đo
 *   thì hiện "chưa có số đo", không điền số giả.
 * - `/ws/topology`: "snapshot" (2 s/lần) cập nhật trạng thái, "step" là từng
 *   bước xử lý thật (lượt thoại, tool, phê duyệt, robot) -> sáng cạnh tương ứng
 *   và thêm vào "Luồng trực tiếp".
 * - Mất kết nối WS: tự nối lại, trong lúc đó hỏi REST mỗi 5 s (không đứng hình).
 * - Mô phỏng chỉ chạy CỤC BỘ trên trình duyệt này, gắn nhãn MÔ PHỎNG.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Edge,
  Handle,
  MarkerType,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlowInstance,
  applyNodeChanges,
  NodeChange,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Activity, AlertTriangle, Bot, Cpu, Database, Gauge, Headphones, MessageSquare, Monitor,
  Plug, RotateCcw, Save, Server, ShieldCheck, Volume2, Wrench, Wifi, WifiOff, Play,
  Check, Loader2, Pause, HelpCircle,
} from 'lucide-react';
import { authFetch, sessionToken } from '@/lib/api';

// ── Kiểu dữ liệu từ máy chủ ─────────────────────────────────────────────────
type Status = 'ok' | 'degraded' | 'down' | 'off' | 'unknown';

interface TopoNode {
  id: string;
  kind: string;
  label: string;
  status: Status;
  detail: string;
  metrics: Record<string, unknown>;
  group: string;
  since: number;
}

interface TopoEdge { id: string; source: string; target: string }

interface TopoEvent {
  seq: number;
  kind: string;
  status: string;
  ts: string;
  t: number;
  node?: string;
  source?: string;
  target?: string;
  stage?: string;
  trace_id?: string;
  channel?: string;
  ms?: number;
  detail?: string;
  simulated?: boolean;
}

interface Snapshot {
  nodes: TopoNode[];
  edges: TopoEdge[];
  counts: Record<string, number>;
  timestamp: string;
  layout?: Record<string, { x: number; y: number }>;
}

// ── Hiển thị ────────────────────────────────────────────────────────────────
const STATUS_TEXT: Record<Status, string> = {
  ok: 'Hoạt động', degraded: 'Suy giảm', down: 'Lỗi', off: 'Tắt', unknown: 'Chưa rõ',
};
const STATUS_COLOR: Record<Status, string> = {
  ok: '#22c55e', degraded: '#f59e0b', down: '#ef4444', off: '#64748b', unknown: '#94a3b8',
};
const EVENT_COLOR: Record<string, string> = {
  running: '#38bdf8', ok: '#22c55e', waiting: '#f59e0b', error: '#ef4444',
  down: '#ef4444', degraded: '#f59e0b', cancelled: '#94a3b8',
};
const STAGE_TEXT: Record<string, string> = {
  start: 'bắt đầu', router: 'định tuyến', ack_audio: 'câu xác nhận', first_text: 'LLM trả câu đầu',
  first_answer_audio: 'tiếng trả lời đầu', end: 'kết thúc', request: 'chờ phê duyệt',
  approved: 'đã duyệt', wake: 'nghe thấy tên gọi', stt: 'nhận dạng giọng nói',
  listen: 'đang lắng nghe', farewell: 'tạm biệt',
};
const KIND_TEXT: Record<string, string> = {
  turn: 'Lượt hội thoại', tool: 'Tool', approval: 'Phê duyệt', robot: 'Robot',
  status: 'Đổi trạng thái', simulation: 'Mô phỏng',
};
const METRIC_TEXT: Record<string, string> = {
  cpu_percent: 'CPU %', ram_percent: 'RAM %', disk_percent: 'Đĩa %', uptime: 'Thời gian chạy',
  latency_ms: 'Độ trễ (ms)', model: 'Model', models_cooling: 'Model tạm bỏ qua', turns: 'Số lượt',
  last_outcome: 'Kết quả gần nhất', last_ttfa_ms: 'Tiếng đầu (ms)', last_ttl_ms: 'Tổng lượt (ms)',
  backend: 'Bộ nhận dạng', last_ms: 'Lần gần nhất (ms)', connections: 'Kết nối',
  voice_sessions: 'Phiên thoại', ip: 'IP', state: 'Trạng thái', emotion: 'Biểu cảm', busy: 'Đang xử lý',
  follow_up: 'Nghe tiếp (mức)', firmware: 'Firmware', last_active: 'Hoạt động cuối', skills: 'Số kỹ năng',
  pending: 'Đang chờ', items: 'Danh sách', platform: 'Hệ điều hành',
};

const KIND_ICON: Record<string, React.ElementType> = {
  server: Server, llm: Cpu, database: Database, channel: MessageSquare, pipeline: Activity,
  stt: Headphones, tts: Volume2, robot: Bot, tools: Wrench, approval: ShieldCheck,
  worker: Monitor, connector: Plug,
};

/**
 * Cột mặc định theo hướng luồng xử lý (trái -> phải, kiểu n8n):
 * kênh vào -> nhận dạng giọng -> lõi hội thoại -> LLM / TTS / cổng tool -> nơi thực thi -> CSDL.
 */
function defaultColumn(n: TopoNode): number {
  if (n.group === 'channel') return 0;
  if (n.id === 'stt') return 1;
  if (n.id === 'voice') return 2;
  if (n.id === 'llm' || n.id === 'tts' || n.id === 'tools') return 3;
  if (n.id === 'db') return 5;
  return 4;   // core, hitl, máy trạm, connector
}

const COL_W = 230;
const ROW_H = 150;

function defaultPositions(nodes: TopoNode[]): Record<string, { x: number; y: number }> {
  const cols: Record<number, string[]> = {};
  for (const n of nodes) (cols[defaultColumn(n)] ??= []).push(n.id);
  const out: Record<string, { x: number; y: number }> = {};
  for (const [col, ids] of Object.entries(cols)) {
    ids.forEach((id, row) => {
      out[id] = { x: Number(col) * COL_W, y: (row - (ids.length - 1) / 2) * ROW_H };
    });
  }
  return out;
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'có' : 'không';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(1);
  if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') : '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function fmtSince(t: number): string {
  if (!t) return '';
  const d = new Date(t * 1000);
  const ago = Math.max(0, Math.round(Date.now() / 1000 - t));
  const rel = ago < 60 ? `${ago}s` : ago < 3600 ? `${Math.floor(ago / 60)} phút` : `${Math.floor(ago / 3600)} giờ`;
  return `từ ${d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })} (${rel})`;
}

function eventText(e: TopoEvent): string {
  const stage = e.stage ? STAGE_TEXT[e.stage] ?? e.stage : '';
  const path = e.source && e.target ? `${e.source} → ${e.target}` : e.node ?? '';
  return [KIND_TEXT[e.kind] ?? e.kind, stage, path].filter(Boolean).join(' · ');
}

// ── Ô thành phần ────────────────────────────────────────────────────────────
interface NodeData { node: TopoNode; pulse?: string; running?: boolean; runs?: number }

/** Dòng phụ dưới tên ô: số đo đáng xem nhất (kiểu "1 item" của n8n). */
function subtitle(n: TopoNode): string {
  const m = n.metrics || {};
  const num = (k: string, unit = '') => (typeof m[k] === 'number' ? `${fmtValue(m[k])}${unit}` : null);
  switch (n.kind) {
    case 'server': return [num('cpu_percent', '% CPU'), num('ram_percent', '% RAM')].filter(Boolean).join(' · ');
    case 'llm': return [num('latency_ms', ' ms'), m.model ? String(m.model) : null].filter(Boolean).join(' · ');
    case 'pipeline': return [num('turns', ' lượt'), num('last_ttfa_ms', ' ms')].filter(Boolean).join(' · ');
    case 'stt': case 'tts': return num('last_ms', ' ms') ?? '';
    case 'channel': return typeof m.connections === 'number' ? `${m.connections} kết nối` : '';
    case 'robot': return [m.state ? String(m.state) : null, m.ip ? String(m.ip) : null].filter(Boolean).join(' · ');
    case 'tools': return num('skills', ' kỹ năng') ?? '';
    case 'approval': return typeof m.pending === 'number' ? `${m.pending} chờ duyệt` : '';
    case 'worker': return m.ip ? String(m.ip) : '';
    default: return '';
  }
}

const HANDLE_STYLE: React.CSSProperties = {
  width: 10, height: 10, background: '#9ca3af', border: '2px solid #1f1f29',
};

function StatusNode({ data, selected }: NodeProps<NodeData>) {
  const n = data.node;
  const color = STATUS_COLOR[n.status] ?? STATUS_COLOR.unknown;
  const Icon = KIND_ICON[n.kind] ?? Gauge;
  const trigger = n.group === 'channel';           // kênh vào = "trigger" (bo tròn bên trái như n8n)
  const Badge = data.running ? Loader2
    : n.status === 'ok' ? Check
      : n.status === 'down' || n.status === 'degraded' ? AlertTriangle
        : n.status === 'off' ? Pause : HelpCircle;
  const badgeColor = data.running ? '#38bdf8' : color;
  const sub = subtitle(n);
  return (
    <div className="flex flex-col items-center" style={{ width: 150 }} title={n.detail || STATUS_TEXT[n.status]}>
      <div
        className="relative flex items-center justify-center bg-[#2b2b36]"
        style={{
          width: 92, height: 92,
          borderRadius: trigger ? '46px 12px 12px 46px' : 12,
          border: `2px solid ${selected ? '#ff6d5a' : data.pulse ?? (n.status === 'ok' ? '#3f3f4f' : color)}`,
          boxShadow: data.pulse ? `0 0 0 3px ${data.pulse}55, 0 0 22px ${data.pulse}` : '0 2px 6px #0006',
          transition: 'box-shadow 0.25s, border-color 0.25s',
          opacity: n.status === 'off' ? 0.55 : 1,
        }}
      >
        {!trigger && <Handle type="target" position={Position.Left} style={HANDLE_STYLE} />}
        <Icon style={{ width: 40, height: 40, color: n.status === 'off' ? '#9ca3af' : '#e5e7eb' }} strokeWidth={1.5} />
        <span
          className="absolute -right-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full border-2 border-[#1f1f29]"
          style={{ background: badgeColor }}
          title={data.running ? 'Đang chạy' : STATUS_TEXT[n.status]}
        >
          <Badge className={`h-3.5 w-3.5 text-[#111] ${data.running ? 'animate-spin' : ''}`} strokeWidth={3} />
        </span>
        {!!data.runs && (
          <span className="absolute -bottom-2 rounded-full bg-[#3f3f4f] px-1.5 text-[10px] font-semibold text-slate-200" title="Số sự kiện 10 phút qua">
            {data.runs}
          </span>
        )}
        <Handle type="source" position={Position.Right} style={HANDLE_STYLE} />
      </div>
      <div className="mt-2 max-w-[150px] text-center text-[12px] font-semibold leading-tight text-slate-100">{n.label}</div>
      <div className="max-w-[150px] truncate text-center text-[10.5px]" style={{ color: n.status === 'ok' ? '#9ca3af' : color }}>
        {n.status === 'ok' ? sub || STATUS_TEXT.ok : `${STATUS_TEXT[n.status]}${n.detail ? ` — ${n.detail}` : ''}`}
      </div>
    </div>
  );
}

const nodeTypes = { status: StatusNode };

// ── Trang ───────────────────────────────────────────────────────────────────
const EDGE_GLOW_MS = 2500;
const MAX_EVENTS = 300;

type Tab = 'flow' | 'incidents' | 'detail';

export default function LiveTopology() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<TopoEvent[]>([]);
  const [wsState, setWsState] = useState<'connecting' | 'live' | 'down'>('connecting');
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('flow');
  const [glow, setGlow] = useState<Record<string, { color: string; until: number; label?: string }>>({});
  const [nodes, setNodes] = useState<Node<NodeData>[]>([]);
  const [rf, setRf] = useState<ReactFlowInstance | null>(null);
  const fitted = useRef(false);
  const [error, setError] = useState<string>('');
  const [notice, setNotice] = useState<string>('');
  const [, setTick] = useState(0);
  const lastSeq = useRef(0);
  const savedLayout = useRef<Record<string, { x: number; y: number }>>({});

  // Sự kiện mới -> danh sách + làm sáng cạnh/ô liên quan.
  const addEvents = useCallback((evs: TopoEvent[]) => {
    const fresh = evs.filter((e) => e.seq > lastSeq.current || e.simulated);
    if (!fresh.length) return;
    for (const e of fresh) if (!e.simulated) lastSeq.current = Math.max(lastSeq.current, e.seq);
    setEvents((prev) => [...prev, ...fresh].slice(-MAX_EVENTS));
    const now = Date.now();
    setGlow((prev) => {
      const next = { ...prev };
      for (const e of fresh) {
        if (now - e.t * 1000 > EDGE_GLOW_MS && !e.simulated) continue;   // lịch sử cũ: không nhấp nháy
        const color = EVENT_COLOR[e.status] ?? '#38bdf8';
        if (e.source && e.target) {
          const label = e.ms !== undefined && e.stage !== 'start' ? `${Math.round(e.ms)} ms` : (e.stage ? STAGE_TEXT[e.stage] ?? e.stage : undefined);
          next[`${e.source}->${e.target}`] = { color, until: now + EDGE_GLOW_MS, label };
          next[`node:${e.target}`] = { color, until: now + EDGE_GLOW_MS };
        }
        if (e.node) next[`node:${e.node}`] = { color, until: now + EDGE_GLOW_MS };
      }
      return next;
    });
  }, []);

  const applySnapshot = useCallback((s: Snapshot) => {
    setSnap(s);
    setLastUpdate(Date.now());
    if (s.layout) savedLayout.current = s.layout;
  }, []);

  // Tải lần đầu + dự phòng REST khi WS rớt.
  const loadRest = useCallback(async () => {
    try {
      const res = await authFetch('/api/v1/system/topology');
      if (res.status === 401 || res.status === 403) {
        setError(res.status === 403 ? 'Tài khoản không có quyền xem sơ đồ (cần quản lý hoặc admin).' : 'Phiên đăng nhập hết hạn.');
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      applySnapshot(await res.json());
      const er = await authFetch(`/api/v1/system/topology/events?since=${lastSeq.current}&limit=300`);
      if (er.ok) addEvents((await er.json()).events ?? []);
      setError('');
    } catch (e) {
      setError(`Không tải được trạng thái: ${(e as Error).message}`);
    }
  }, [addEvents, applySnapshot]);

  useEffect(() => { loadRest(); }, [loadRest]);

  // WebSocket + tự nối lại.
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let poll: ReturnType<typeof setInterval> | null = null;
    let ping: ReturnType<typeof setInterval> | null = null;
    let closed = false;
    let attempt = 0;

    const connect = () => {
      setWsState('connecting');
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const token = sessionToken();
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/topology${token ? `?token=${encodeURIComponent(token)}` : ''}`);
      socket.onopen = () => {
        attempt = 0;
        setWsState('live');
        if (poll) { clearInterval(poll); poll = null; }
        ping = setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send(JSON.stringify({ action: 'ping' })), 25000);
      };
      socket.onmessage = (msg) => {
        let data: any;
        try { data = JSON.parse(msg.data); } catch { return; }
        if (data.event === 'snapshot') applySnapshot(data);
        else if (data.event === 'history') addEvents(data.events ?? []);
        else if (data.event === 'step') addEvents([data]);
      };
      socket.onclose = () => {
        if (ping) { clearInterval(ping); ping = null; }
        if (closed) return;
        setWsState('down');
        if (!poll) poll = setInterval(loadRest, 5000);
        attempt += 1;
        retry = setTimeout(connect, Math.min(15000, 1000 * 2 ** Math.min(attempt, 4)));
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      if (poll) clearInterval(poll);
      if (ping) clearInterval(ping);
      socket?.close();
    };
  }, [addEvents, applySnapshot, loadRest]);

  // Đồng hồ 1 s: tắt dần hiệu ứng sáng, cập nhật "x giây trước".
  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      setGlow((prev) => {
        const keys = Object.keys(prev).filter((k) => prev[k].until > now);
        return keys.length === Object.keys(prev).length ? prev : Object.fromEntries(keys.map((k) => [k, prev[k]]));
      });
      setTick((t) => t + 1);
    }, 500);
    return () => clearInterval(id);
  }, []);

  // Số sự kiện 10 phút qua của từng ô (huy hiệu dưới ô).
  const runs = useMemo(() => {
    const out: Record<string, number> = {};
    const since = Date.now() / 1000 - 600;
    for (const e of events) {
      if (e.t < since || e.kind === 'status' || e.simulated) continue;
      [e.node, e.source, e.target].filter((id, i, a) => id && a.indexOf(id) === i)
        .forEach((id) => { out[id!] = (out[id!] ?? 0) + 1; });
    }
    return out;
  }, [events]);

  // Ô do state giữ: React Flow ghi kích thước đo được vào chính object ô, ô chỉ
  // hiện khi đã có kích thước. Mỗi snapshot chỉ cập nhật data + thêm/bớt ô,
  // giữ nguyên vị trí và kích thước.
  useEffect(() => {
    if (!snap) return;
    const defaults = defaultPositions(snap.nodes);
    setNodes((prev) => {
      const old = Object.fromEntries(prev.map((n) => [n.id, n]));
      return snap.nodes.map((n) => {
        const g = glow[`node:${n.id}`];
        return {
          ...(old[n.id] ?? {}),
          id: n.id,
          type: 'status',
          position: old[n.id]?.position ?? savedLayout.current[n.id] ?? defaults[n.id],
          data: { node: n, pulse: g?.color, running: g?.color === EVENT_COLOR.running, runs: runs[n.id] },
          selected: n.id === selected,
        };
      });
    });
  }, [snap, glow, selected, runs]);

  // Lần đầu có ô (và mỗi khi số ô đổi): căn sơ đồ vừa khung nhìn.
  useEffect(() => {
    if (!rf || !nodes.length) return;
    const key = nodes.length;
    if (fitted.current && (rf as unknown as { _n?: number })._n === key) return;
    (rf as unknown as { _n?: number })._n = key;
    fitted.current = true;
    const t = setTimeout(() => rf.fitView({ padding: 0.15, duration: 300 }), 80);
    return () => clearTimeout(t);
  }, [rf, nodes.length]);

  /**
   * Cạnh luôn chạy trái -> phải (kiểu n8n). Bước trả về (llm -> voice, tts -> hud,
   * core -> tools…) làm sáng cạnh xuôi giữa hai ô đó thay vì vẽ vòng cong ngược.
   */
  const rfEdges: Edge[] = useMemo(() => {
    if (!snap) return [];
    const byId = Object.fromEntries(snap.nodes.map((n) => [n.id, n]));
    const forward = (a: string, b: string): [string, string] =>
      byId[a] && byId[b] && defaultColumn(byId[a]) > defaultColumn(byId[b]) ? [b, a] : [a, b];
    const pairs: Record<string, { source: string; target: string; temp: boolean }> = {};
    for (const e of snap.edges) {
      const [a, b] = forward(e.source, e.target);
      pairs[`${a}->${b}`] ??= { source: a, target: b, temp: false };
    }
    const lit: Record<string, { color: string; until: number; label?: string }> = {};
    for (const [k, g] of Object.entries(glow)) {
      if (k.startsWith('node:')) continue;
      const [s0, t0] = k.split('->');
      if (!byId[s0] || !byId[t0] || s0 === t0) continue;
      const [a, b] = forward(s0, t0);
      const key = `${a}->${b}`;
      pairs[key] ??= { source: a, target: b, temp: true };     // cạnh chưa có: vẽ tạm khi đang sáng
      if (!lit[key] || g.until > lit[key].until) lit[key] = g;
    }
    return Object.entries(pairs).map(([id, p]) => {
      const g = lit[id];
      const targetDown = byId[p.target]?.status === 'down';
      const stroke = g?.color ?? (targetDown ? '#ef444488' : '#6b7280');
      return {
        id, source: p.source, target: p.target, animated: !!g,
        hidden: p.temp && !g,
        label: g?.label,
        labelStyle: { fill: '#e5e7eb', fontSize: 11, fontWeight: 600 },
        labelBgStyle: { fill: '#1f1f29' },
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 4,
        markerEnd: { type: MarkerType.ArrowClosed, color: g?.color ?? (targetDown ? '#ef4444' : '#6b7280'), width: 16, height: 16 },
        style: { stroke, strokeWidth: g ? 3 : 2, strokeDasharray: p.temp ? '6 4' : undefined },
      };
    });
  }, [snap, glow]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds) as Node<NodeData>[]);
  }, []);

  const saveLayout = async () => {
    const res = await authFetch('/api/v1/system/topology/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodes: nodes.map((n) => ({ id: n.id, position: n.position })), edges: [] }),
    });
    setNotice(res.ok ? 'Đã lưu bố cục.' : res.status === 403 ? 'Chỉ admin được lưu bố cục.' : `Lưu lỗi (HTTP ${res.status}).`);
    if (res.ok) savedLayout.current = Object.fromEntries(nodes.map((n) => [n.id, n.position]));
  };

  const resetLayout = async () => {
    const res = await authFetch('/api/v1/system/topology/reset', { method: 'POST' });
    if (res.ok) {
      savedLayout.current = {};
      if (snap) {
        const d = defaultPositions(snap.nodes);
        setNodes((nds) => nds.map((n) => ({ ...n, position: d[n.id] ?? n.position })));
        setTimeout(() => rf?.fitView({ padding: 0.15, duration: 300 }), 80);
      }
      setNotice('Đã đặt lại bố cục mặc định.');
    }
    else setNotice(res.status === 403 ? 'Chỉ admin được đặt lại bố cục.' : `Đặt lại lỗi (HTTP ${res.status}).`);
  };

  /** Mô phỏng CỤC BỘ một lượt thoại để thử giao diện — không gửi lên máy chủ. */
  const simulate = () => {
    const steps: [string, string, string, string][] = [
      ['start', 'hud', 'voice', 'running'], ['router', 'hud', 'voice', 'ok'], ['first_text', 'llm', 'voice', 'ok'],
      ['first_answer_audio', 'tts', 'hud', 'ok'], ['end', 'voice', 'hud', 'ok'],
    ];
    const trace = `sim-${Date.now()}`;
    steps.forEach(([stage, source, target, status], i) => setTimeout(() => addEvents([{
      seq: -Date.now() - i, kind: 'simulation', stage, source, target, status, trace_id: trace,
      ts: new Date().toISOString(), t: Date.now() / 1000, detail: 'MÔ PHỎNG cục bộ — không phải dữ liệu thật', simulated: true,
    }]), i * 700));
  };

  // ── Bảng bên ─────────────────────────────────────────────────────────────
  const flows = useMemo(() => {
    const groups: { key: string; events: TopoEvent[] }[] = [];
    const byKey: Record<string, { key: string; events: TopoEvent[] }> = {};
    for (const e of events) {
      if (e.kind === 'status') continue;
      const key = e.trace_id ?? `seq-${e.seq}`;
      if (!byKey[key]) { byKey[key] = { key, events: [] }; groups.push(byKey[key]); }
      byKey[key].events.push(e);
    }
    return groups.reverse().slice(0, 40);
  }, [events]);

  const incidents = useMemo(() => {
    const bad = (snap?.nodes ?? []).filter((n) => n.status === 'down' || n.status === 'degraded')
      .sort((a, b) => (a.status === 'down' ? -1 : 1) - (b.status === 'down' ? -1 : 1));
    const errs = events.filter((e) => !e.simulated && (e.status === 'error' || e.status === 'down' || e.status === 'degraded' || e.status === 'waiting')).slice(-40).reverse();
    return { bad, errs };
  }, [snap, events]);

  const selNode = snap?.nodes.find((n) => n.id === selected) ?? null;
  const selEvents = useMemo(() => selected
    ? events.filter((e) => e.node === selected || e.source === selected || e.target === selected).slice(-30).reverse()
    : [], [events, selected]);

  const counts = snap?.counts ?? {};
  const stale = lastUpdate && Date.now() - lastUpdate > 10000;

  return (
    <div className="flex h-full w-full flex-col bg-[#020817] text-slate-100">
      {/* Thanh trên */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-800 px-4 py-2">
        <h1 className="text-base font-bold tracking-wide">Sơ đồ hệ thống — giám sát thời gian thực</h1>
        <span className={`flex items-center gap-1 rounded px-2 py-0.5 text-xs ${wsState === 'live' ? 'bg-green-900/50 text-green-300' : wsState === 'connecting' ? 'bg-sky-900/50 text-sky-300' : 'bg-red-900/50 text-red-300'}`}>
          {wsState === 'live' ? <Wifi className="h-3 w-3" /> : <WifiOff className="h-3 w-3" />}
          {wsState === 'live' ? 'Trực tiếp' : wsState === 'connecting' ? 'Đang kết nối…' : 'Mất kết nối — hỏi lại mỗi 5 s'}
        </span>
        {lastUpdate > 0 && (
          <span className={`text-xs ${stale ? 'text-amber-400' : 'text-slate-500'}`}>
            cập nhật {Math.round((Date.now() - lastUpdate) / 1000)}s trước{stale ? ' — dữ liệu có thể cũ' : ''}
          </span>
        )}
        <div className="flex gap-2 text-xs">
          {(['down', 'degraded', 'ok', 'off', 'unknown'] as Status[]).map((s) => (
            <span key={s} className="flex items-center gap-1 rounded bg-slate-800/70 px-2 py-0.5">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: STATUS_COLOR[s] }} />
              {STATUS_TEXT[s]}: <b>{counts[s] ?? 0}</b>
            </span>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <button onClick={simulate} className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800" title="Chạy thử hiệu ứng trên trình duyệt này, không gửi lên máy chủ">
            <Play className="h-3 w-3" /> Mô phỏng
          </button>
          <button onClick={saveLayout} className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800">
            <Save className="h-3 w-3" /> Lưu bố cục
          </button>
          <button onClick={resetLayout} className="flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800">
            <RotateCcw className="h-3 w-3" /> Đặt lại
          </button>
        </div>
      </div>
      {(error || notice) && (
        <div className={`px-4 py-1 text-xs ${error ? 'bg-red-950 text-red-300' : 'bg-slate-900 text-slate-300'}`} onClick={() => setNotice('')}>
          {error || notice}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Sơ đồ */}
        <div className="relative min-w-0 flex-1">
          {!snap && !error && <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">Đang tải trạng thái thật…</div>}
          <ReactFlow
            nodes={nodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onInit={setRf}
            onNodeClick={(_, n) => { setSelected(n.id); setTab('detail'); }}
            onPaneClick={() => setSelected(null)}
            defaultEdgeOptions={{ type: 'default' }}
            nodesConnectable={false}
            minZoom={0.2}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
            style={{ background: '#1f1f29' }}
          >
            <Background variant={BackgroundVariant.Dots} color="#4b4b5c" gap={20} size={1.4} />
            <Controls showInteractive={false} position="bottom-left" />
            <MiniMap pannable zoomable nodeColor={(n) => STATUS_COLOR[(n.data as NodeData).node.status] ?? '#64748b'} maskColor="#1f1f2999" style={{ background: '#2b2b36' }} />
          </ReactFlow>
        </div>

        {/* Bảng bên */}
        <div className="flex w-[380px] shrink-0 flex-col border-l border-slate-800 bg-slate-950">
          <div className="flex border-b border-slate-800 text-xs">
            {([['flow', `Luồng trực tiếp`], ['incidents', `Sự cố (${incidents.bad.length})`], ['detail', 'Chi tiết']] as [Tab, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setTab(k)} className={`flex-1 px-2 py-2 ${tab === k ? 'border-b-2 border-sky-400 text-sky-300' : 'text-slate-400 hover:text-slate-200'}`}>
                {k === 'incidents' && incidents.bad.length > 0 && <AlertTriangle className="mr-1 inline h-3 w-3 text-red-400" />}
                {label}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-3 text-xs">
            {tab === 'flow' && (
              flows.length === 0
                ? <p className="text-slate-500">Chưa có hoạt động nào. Mỗi lượt thoại, tool, phê duyệt hay robot sẽ hiện ở đây ngay khi xảy ra.</p>
                : flows.map((g) => <FlowCard key={g.key} events={g.events} onPick={(id) => { setSelected(id); setTab('detail'); }} />)
            )}

            {tab === 'incidents' && (
              <>
                <h3 className="mb-2 font-semibold text-slate-300">Thành phần đang có vấn đề</h3>
                {incidents.bad.length === 0 && <p className="mb-3 text-green-400">Không có thành phần nào lỗi hoặc suy giảm.</p>}
                {incidents.bad.map((n) => (
                  <button key={n.id} onClick={() => { setSelected(n.id); setTab('detail'); }} className="mb-2 block w-full rounded border p-2 text-left hover:bg-slate-900" style={{ borderColor: STATUS_COLOR[n.status] }}>
                    <div className="flex justify-between font-semibold"><span>{n.label}</span><span style={{ color: STATUS_COLOR[n.status] }}>{STATUS_TEXT[n.status]}</span></div>
                    <div className="text-slate-400">{n.detail || 'không có mô tả'}</div>
                    <div className="text-slate-500">{fmtSince(n.since)}</div>
                  </button>
                ))}
                <h3 className="mb-2 mt-4 font-semibold text-slate-300">Sự kiện lỗi / chờ gần đây</h3>
                {incidents.errs.length === 0 && <p className="text-slate-500">Không có.</p>}
                {incidents.errs.map((e) => <EventRow key={e.seq} e={e} />)}
              </>
            )}

            {tab === 'detail' && (
              selNode ? (
                <>
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">{selNode.label}</h3>
                    <span className="font-semibold" style={{ color: STATUS_COLOR[selNode.status] }}>{STATUS_TEXT[selNode.status]}</span>
                  </div>
                  <div className="mb-1 font-mono text-slate-500">{selNode.id}</div>
                  {selNode.detail && <p className="mb-2 text-slate-300">{selNode.detail}</p>}
                  <p className="mb-3 text-slate-500">{fmtSince(selNode.since)}</p>
                  <table className="mb-4 w-full">
                    <tbody>
                      {Object.entries(selNode.metrics).length === 0 && <tr><td className="text-slate-500">Chưa có số đo.</td></tr>}
                      {Object.entries(selNode.metrics).map(([k, v]) => (
                        <tr key={k} className="border-b border-slate-800">
                          <td className="py-1 pr-2 text-slate-400">{METRIC_TEXT[k] ?? k}</td>
                          <td className="py-1 text-right font-mono">{k === 'last_active' && typeof v === 'number' ? fmtSince(v) : fmtValue(v)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <h4 className="mb-2 font-semibold text-slate-300">Sự kiện liên quan</h4>
                  {selEvents.length === 0 && <p className="text-slate-500">Chưa có.</p>}
                  {selEvents.map((e) => <EventRow key={`${e.seq}`} e={e} />)}
                </>
              ) : <p className="text-slate-500">Bấm vào một ô trên sơ đồ để xem số đo và sự kiện của nó.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function EventRow({ e }: { e: TopoEvent }) {
  const color = EVENT_COLOR[e.status] ?? '#94a3b8';
  return (
    <div className="mb-1 flex gap-2 border-l-2 pl-2" style={{ borderColor: color }}>
      <span className="shrink-0 font-mono text-slate-500">{e.ts.slice(11, 19)}</span>
      <div className="min-w-0">
        <div>
          {e.simulated && <span className="mr-1 rounded bg-fuchsia-900 px-1 text-[10px] text-fuchsia-200">MÔ PHỎNG</span>}
          <span style={{ color }}>{eventText(e)}</span>
          {e.ms !== undefined && <span className="ml-1 font-mono text-slate-400">{Math.round(e.ms)} ms</span>}
        </div>
        {e.detail && <div className="truncate text-slate-400" title={e.detail}>{e.detail}</div>}
      </div>
    </div>
  );
}

/** Một lượt (cùng trace_id) hoặc một sự kiện lẻ: các bước theo thứ tự + tổng thời gian. */
function FlowCard({ events, onPick }: { events: TopoEvent[]; onPick: (id: string) => void }) {
  const first = events[0];
  const last = events[events.length - 1];
  const done = last.stage === 'end' || (first.kind !== 'turn' && first.kind !== 'simulation');
  const failed = events.some((e) => e.status === 'error');
  const color = failed ? '#ef4444' : done ? '#22c55e' : '#38bdf8';
  const title = first.kind === 'turn' || first.kind === 'simulation'
    ? `${KIND_TEXT[first.kind]} · ${first.channel ?? first.source ?? ''}`
    : KIND_TEXT[first.kind] ?? first.kind;
  return (
    <div className="mb-2 rounded border border-slate-800 bg-slate-900/60 p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-semibold" style={{ color }}>
          {first.simulated && <span className="mr-1 rounded bg-fuchsia-900 px-1 text-[10px] text-fuchsia-200">MÔ PHỎNG</span>}
          {title}
        </span>
        <span className="font-mono text-slate-500">
          {first.ts.slice(11, 19)}{events.length > 1 && last.t > first.t ? ` · ${Math.round((last.t - first.t) * 1000)} ms` : ''}
          {!done && !failed && <span className="ml-1 animate-pulse text-sky-300">●</span>}
        </span>
      </div>
      {events.map((e) => (
        <div key={e.seq} className="flex items-center gap-2">
          <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: EVENT_COLOR[e.status] ?? '#94a3b8' }} />
          <span className="text-slate-300">{e.stage ? STAGE_TEXT[e.stage] ?? e.stage : e.kind}</span>
          {(e.source || e.node) && (
            <button className="truncate text-slate-500 hover:text-sky-300" onClick={() => onPick((e.target ?? e.node ?? e.source)!)}>
              {e.source && e.target ? `${e.source} → ${e.target}` : e.node}
            </button>
          )}
          {e.ms !== undefined && <span className="ml-auto shrink-0 font-mono text-slate-400">{Math.round(e.ms)} ms</span>}
        </div>
      ))}
      {last.detail && !last.simulated && <div className="mt-1 truncate text-slate-400" title={last.detail}>{last.detail}</div>}
    </div>
  );
}

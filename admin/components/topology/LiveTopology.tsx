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
  Controls,
  Edge,
  Handle,
  MiniMap,
  Node,
  NodeProps,
  Position,
  applyNodeChanges,
  NodeChange,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Activity, AlertTriangle, Bot, Cpu, Database, Gauge, Headphones, MessageSquare, Monitor,
  Plug, RotateCcw, Save, Server, ShieldCheck, Volume2, Wrench, Wifi, WifiOff, Play,
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

/** Cột mặc định theo hướng luồng xử lý: kênh -> giọng nói -> lõi -> LLM/CSDL -> tool -> máy trạm/connector. */
function defaultColumn(n: TopoNode): number {
  if (n.group === 'channel') return 0;
  if (n.id === 'stt' || n.id === 'tts') return 1;
  if (n.id === 'voice' || n.id === 'core') return 2;
  if (n.id === 'llm' || n.id === 'db') return 3;
  if (n.id === 'tools' || n.id === 'hitl') return 4;
  return 5;
}

function defaultPositions(nodes: TopoNode[]): Record<string, { x: number; y: number }> {
  const rows: Record<number, number> = {};
  const out: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const col = defaultColumn(n);
    const row = rows[col] ?? 0;
    rows[col] = row + 1;
    out[n.id] = { x: col * 290, y: row * 150 + (col === 1 || col === 3 ? 60 : 0) };
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
interface NodeData { node: TopoNode; pulse?: string }

/** Số đo đáng xem nhất của từng loại ô (tối đa 3). */
function keyMetrics(n: TopoNode): [string, unknown][] {
  const m = n.metrics || {};
  const pick: Record<string, string[]> = {
    server: ['cpu_percent', 'ram_percent', 'disk_percent'],
    llm: ['latency_ms', 'model'],
    pipeline: ['turns', 'last_ttfa_ms', 'last_ttl_ms'],
    stt: ['last_ms'], tts: ['last_ms'],
    channel: ['connections', 'voice_sessions'],
    robot: ['state', 'ip', 'follow_up'],
    tools: ['skills'], approval: ['pending'],
    worker: ['ip', 'platform'],
  };
  return (pick[n.kind] ?? []).filter((k) => k in m).map((k) => [METRIC_TEXT[k] ?? k, m[k]]);
}

function StatusNode({ data, selected }: NodeProps<NodeData>) {
  const n = data.node;
  const color = STATUS_COLOR[n.status] ?? STATUS_COLOR.unknown;
  const Icon = KIND_ICON[n.kind] ?? Gauge;
  return (
    <div
      className="rounded-lg bg-slate-900/95 text-slate-100 shadow-lg"
      style={{
        width: 230,
        border: `2px solid ${selected ? '#e2e8f0' : color}`,
        boxShadow: data.pulse ? `0 0 0 4px ${data.pulse}66, 0 0 18px ${data.pulse}` : undefined,
        transition: 'box-shadow 0.25s',
      }}
    >
      <Handle type="target" position={Position.Left} style={{ background: color }} />
      <div className="flex items-center gap-2 px-3 pt-2">
        <Icon className="h-4 w-4 shrink-0" style={{ color }} />
        <span className="truncate text-[13px] font-semibold" title={n.label}>{n.label}</span>
      </div>
      <div className="flex items-center gap-2 px-3 pt-1 text-[11px]">
        <span className={`inline-block h-2 w-2 rounded-full ${n.status === 'down' ? 'animate-pulse' : ''}`} style={{ background: color }} />
        <span style={{ color }} className="font-semibold">{STATUS_TEXT[n.status] ?? n.status}</span>
        <span className="truncate text-slate-500">{fmtSince(n.since)}</span>
      </div>
      {n.detail && <div className="px-3 pt-1 text-[11px] leading-snug text-slate-400 line-clamp-2" title={n.detail}>{n.detail}</div>}
      <div className="grid grid-cols-1 gap-0.5 px-3 pb-2 pt-1 text-[11px]">
        {keyMetrics(n).map(([k, v]) => (
          <div key={k} className="flex justify-between gap-2">
            <span className="text-slate-500">{k}</span>
            <span className="truncate font-mono text-slate-200">{fmtValue(v)}</span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: color }} />
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
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>({});
  const [wsState, setWsState] = useState<'connecting' | 'live' | 'down'>('connecting');
  const [lastUpdate, setLastUpdate] = useState<number>(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('flow');
  const [glow, setGlow] = useState<Record<string, { color: string; until: number }>>({});
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
          next[`${e.source}->${e.target}`] = { color, until: now + EDGE_GLOW_MS };
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

  // Vị trí: bố cục đã lưu > vị trí kéo trong phiên > mặc định theo cột.
  const rfNodes: Node<NodeData>[] = useMemo(() => {
    if (!snap) return [];
    const defaults = defaultPositions(snap.nodes);
    return snap.nodes.map((n) => ({
      id: n.id,
      type: 'status',
      position: positions[n.id] ?? savedLayout.current[n.id] ?? defaults[n.id],
      data: { node: n, pulse: glow[`node:${n.id}`]?.color },
      selected: n.id === selected,
    }));
  }, [snap, positions, glow, selected]);

  const rfEdges: Edge[] = useMemo(() => {
    if (!snap) return [];
    const ids = new Set(snap.nodes.map((n) => n.id));
    const list: Edge[] = snap.edges.map((e) => {
      const g = glow[e.id];
      const targetDown = snap.nodes.find((n) => n.id === e.target)?.status === 'down';
      return {
        id: e.id, source: e.source, target: e.target, animated: !!g,
        style: { stroke: g?.color ?? (targetDown ? '#ef444488' : '#334155'), strokeWidth: g ? 3 : 1.5 },
      };
    });
    // Sự kiện trên cạnh không có sẵn (vd tts -> hud): vẽ tạm khi đang sáng.
    for (const [k, g] of Object.entries(glow)) {
      if (k.startsWith('node:') || list.some((e) => e.id === k)) continue;
      const [s, t] = k.split('->');
      if (ids.has(s) && ids.has(t)) {
        list.push({ id: k, source: s, target: t, animated: true, style: { stroke: g.color, strokeWidth: 3, strokeDasharray: '6 4' } });
      }
    }
    return list;
  }, [snap, glow]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const moved = applyNodeChanges(changes, rfNodes);
    setPositions((prev) => {
      const next = { ...prev };
      for (const c of changes) {
        if (c.type === 'position' && c.position) next[c.id] = moved.find((n) => n.id === c.id)!.position;
      }
      return next;
    });
  }, [rfNodes]);

  const saveLayout = async () => {
    const res = await authFetch('/api/v1/system/topology/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodes: rfNodes.map((n) => ({ id: n.id, position: n.position })), edges: [] }),
    });
    setNotice(res.ok ? 'Đã lưu bố cục.' : res.status === 403 ? 'Chỉ admin được lưu bố cục.' : `Lưu lỗi (HTTP ${res.status}).`);
    if (res.ok) savedLayout.current = Object.fromEntries(rfNodes.map((n) => [n.id, n.position]));
  };

  const resetLayout = async () => {
    const res = await authFetch('/api/v1/system/topology/reset', { method: 'POST' });
    if (res.ok) { savedLayout.current = {}; setPositions({}); setNotice('Đã đặt lại bố cục mặc định.'); }
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
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onNodeClick={(_, n) => { setSelected(n.id); setTab('detail'); }}
            onPaneClick={() => setSelected(null)}
            fitView
            minZoom={0.2}
            proOptions={{ hideAttribution: true }}
          >
            <Background color="#1e293b" gap={24} />
            <Controls />
            <MiniMap pannable zoomable nodeColor={(n) => STATUS_COLOR[(n.data as NodeData).node.status] ?? '#64748b'} maskColor="#02081799" style={{ background: '#0f172a' }} />
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

import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Server, HardDrive, Wifi, X, AlertTriangle } from 'lucide-react';

export interface WorkerNodeData {
  label?: string;
  onlineCount?: number;
  totalCount?: number;
  subType?: string;
  clusterIp?: string;
  latency?: string;
  status?: string;
  isGlowing?: boolean;
  isError?: boolean;
  hasError?: boolean;
}

export const WorkerNode = memo(({ data, selected }: NodeProps<WorkerNodeData>) => {
  const isGlowing = data.isGlowing;
  const online = data.onlineCount ?? 1;
  const statusStr = (data.status || '').toLowerCase();
  const hasError =
    data.isError ||
    data.hasError ||
    statusStr === 'error' ||
    statusStr === 'offline' ||
    statusStr === 'disconnected' ||
    online === 0;

  return (
    <div
      className={`relative min-w-[320px] max-w-[360px] p-5 rounded-2xl transition-all duration-300 backdrop-blur-2xl border-2 ${
        hasError
          ? 'border-rose-500 shadow-[0_0_40px_rgba(244,63,94,0.7)] bg-gradient-to-b from-rose-950/85 to-slate-950/95 ring-2 ring-rose-500/50 scale-[1.02]'
          : isGlowing
          ? 'border-orange-500 shadow-[0_0_45px_rgba(249,115,22,0.8)] bg-slate-950/95 scale-105'
          : selected
          ? 'border-emerald-400 shadow-[0_0_35px_rgba(16,185,129,0.5)] bg-slate-950/95'
          : 'border-emerald-500/50 shadow-[0_0_25px_rgba(16,185,129,0.2)] bg-[#01140e]/95 hover:border-emerald-400'
      }`}
    >
      {/* Visual Red X Badge if Connection is Lost */}
      {hasError && (
        <div
          className="absolute -top-3.5 -right-3.5 z-30 flex items-center justify-center w-8 h-8 rounded-full bg-rose-600 border-2 border-white shadow-[0_0_20px_#f43f5e] animate-bounce"
          title="Mất kết nối cụm máy trạm Worknote Agent!"
        >
          <X className="w-5 h-5 text-white stroke-[3.5]" />
        </div>
      )}

      {/* Input Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id="worker-in"
        className={`!w-3.5 !h-3.5 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-emerald-400'
        }`}
      />

      {/* Header Bar */}
      <div className="flex items-center justify-between gap-2 mb-3">
        <span
          className={`text-[10px] font-mono tracking-widest font-extrabold uppercase px-2 py-0.5 rounded border ${
            hasError
              ? 'text-rose-400 bg-rose-950/80 border-rose-500/60'
              : 'text-emerald-400 bg-emerald-950/80 border-emerald-500/40'
          }`}
        >
          EXECUTION CLUSTER
        </span>
        <div
          className={`flex items-center gap-1.5 px-2.5 py-0.5 rounded-full border ${
            hasError
              ? 'bg-rose-950/90 border-rose-500 text-rose-300'
              : 'bg-emerald-950 border-emerald-500/40'
          }`}
        >
          {hasError ? (
            <>
              <span className="h-2 w-2 rounded-full bg-rose-500 animate-ping"></span>
              <span className="text-[11px] font-mono font-bold text-rose-300 uppercase">
                Mất Kết Nối
              </span>
            </>
          ) : (
            <>
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
              </span>
              <span className="text-[11px] font-mono font-bold text-emerald-300">
                {online} Nodes Active
              </span>
            </>
          )}
        </div>
      </div>

      {/* Main Info */}
      <div className="flex items-center gap-3.5">
        <div
          className={`p-3 rounded-xl border-2 flex items-center justify-center flex-shrink-0 ${
            hasError
              ? 'bg-rose-900/40 border-rose-500/80 text-rose-400'
              : 'bg-emerald-500/20 border-emerald-400/40 text-emerald-300'
          }`}
        >
          <Server className="w-7 h-7" />
        </div>
        <div className="overflow-hidden">
          <h4
            className={`font-orbitron font-extrabold text-base truncate ${
              hasError ? 'text-rose-300' : 'text-white'
            }`}
          >
            {data.label || 'Worknote Agent / OpenClaw'}
          </h4>
          <p
            className={`text-xs font-mono truncate mt-0.5 ${
              hasError ? 'text-rose-400 font-bold' : 'text-slate-400'
            }`}
          >
            {hasError ? '⚠️ CỤM MÁY TRẠM MẤT KẾT NỐI WEBSOCKET' : 'RPA Fleet • Browser • Desktop Automation'}
          </p>
        </div>
      </div>

      {/* Metrics Row */}
      <div
        className={`mt-4 pt-3 border-t flex items-center justify-between text-[11px] font-mono ${
          hasError ? 'border-rose-800/80 text-rose-300' : 'border-slate-800 text-slate-400'
        }`}
      >
        <span className="flex items-center gap-1.5">
          {hasError ? (
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400 animate-pulse" />
          ) : (
            <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
          )}
          {data.clusterIp || '192.168.1.0/24 LAN'}
        </span>
        <span
          className={`flex items-center gap-1 font-bold ${
            hasError ? 'text-rose-400' : 'text-emerald-400'
          }`}
        >
          <Wifi className="w-3.5 h-3.5" />
          {hasError ? 'Mất Tín Hiệu' : data.latency || '< 1.1ms'}
        </span>
      </div>

      {/* Output Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="worker-out"
        className={`!w-3.5 !h-3.5 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-emerald-400'
        }`}
      />
    </div>
  );
});

WorkerNode.displayName = 'WorkerNode';

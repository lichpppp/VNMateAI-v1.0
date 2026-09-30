import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Cpu, ShieldCheck, Activity, Sparkles, Server, X, AlertTriangle } from 'lucide-react';

export interface CoreNodeData {
  label?: string;
  status?: string;
  activeAgents?: number;
  connectedWorkers?: number;
  engine?: string;
  uptime?: string;
  isGlowing?: boolean;
  isError?: boolean;
  hasError?: boolean;
}

export const CoreNode = memo(({ data, selected }: NodeProps<CoreNodeData>) => {
  const isGlowing = data.isGlowing;
  const statusStr = (data.status || '').toLowerCase();
  const hasError =
    data.isError ||
    data.hasError ||
    statusStr === 'error' ||
    statusStr === 'offline' ||
    statusStr === 'disconnected';

  return (
    <div
      className={`relative min-w-[340px] max-w-[380px] p-6 rounded-2xl transition-all duration-300 backdrop-blur-2xl border-2 ${
        hasError
          ? 'border-rose-500 shadow-[0_0_45px_rgba(244,63,94,0.7)] bg-gradient-to-b from-rose-950/90 to-slate-950/95 ring-2 ring-rose-500/50 scale-[1.02]'
          : isGlowing
          ? 'border-orange-500 shadow-[0_0_45px_rgba(249,115,22,0.8)] bg-slate-950/95 scale-105'
          : selected
          ? 'border-cyan-400 shadow-[0_0_35px_rgba(0,242,254,0.6)] bg-slate-950/95'
          : 'border-cyan-500/60 shadow-[0_0_30px_rgba(0,242,254,0.25)] bg-[#020917]/95 hover:border-cyan-400'
      }`}
      style={{
        clipPath:
          'polygon(0% 16px, 16px 0%, calc(100% - 16px) 0%, 100% 16px, 100% calc(100% - 16px), calc(100% - 16px) 100%, 16px 100%, 0% calc(100% - 16px))',
      }}
    >
      {/* Visual Red X Badge if Connection is Lost */}
      {hasError && (
        <div
          className="absolute -top-3.5 -right-3.5 z-30 flex items-center justify-center w-8 h-8 rounded-full bg-rose-600 border-2 border-white shadow-[0_0_20px_#f43f5e] animate-bounce"
          title="Lỗi luồng VN-MateAI Brain trung tâm!"
        >
          <X className="w-5 h-5 text-white stroke-[3.5]" />
        </div>
      )}

      {/* Input Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id="core-in"
        className={`!w-3.5 !h-3.5 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-cyan-400'
        }`}
      />

      {/* Cyber Accents */}
      <div className="flex items-center justify-between gap-2 mb-3">
        <span
          className={`text-[10px] font-mono tracking-widest font-extrabold uppercase px-2 py-0.5 rounded border ${
            hasError
              ? 'text-rose-400 bg-rose-950/80 border-rose-500/60'
              : 'text-cyan-400 bg-cyan-950/80 border-cyan-500/40'
          }`}
        >
          CORE BRAIN // MASTER
        </span>
        <div
          className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border ${
            hasError
              ? 'bg-rose-950/90 border-rose-500 text-rose-300'
              : 'bg-slate-900 border-slate-800'
          }`}
        >
          {hasError ? (
            <>
              <span className="h-2 w-2 rounded-full bg-rose-500 animate-ping"></span>
              <span className="text-[11px] font-mono text-rose-300 font-extrabold uppercase">
                Mất Kết Nối
              </span>
            </>
          ) : (
            <>
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-cyan-500"></span>
              </span>
              <span className="text-[11px] font-mono text-cyan-300 font-bold uppercase tracking-wider">
                {data.status || 'OPERATIONAL'}
              </span>
            </>
          )}
        </div>
      </div>

      {/* Main Core Content */}
      <div className="flex items-center gap-4">
        <div
          className={`relative p-3.5 rounded-2xl border-2 shadow-inner flex items-center justify-center flex-shrink-0 ${
            hasError
              ? 'bg-rose-900/40 border-rose-500/80 text-rose-400'
              : 'bg-gradient-to-br from-cyan-500/30 to-blue-700/40 border-cyan-400/50 text-cyan-300'
          }`}
        >
          <Cpu className="w-9 h-9 animate-pulse" />
          <Sparkles className="w-4 h-4 text-cyan-200 absolute -top-1 -right-1" />
        </div>
        <div className="overflow-hidden">
          <h3
            className={`font-orbitron font-extrabold text-xl tracking-wider truncate ${
              hasError ? 'text-rose-300' : 'text-cyan-200'
            }`}
          >
            {data.label || 'VN-MateAI Brain'}
          </h3>
          <p
            className={`text-xs font-mono truncate mt-0.5 ${
              hasError ? 'text-rose-400 font-bold' : 'text-slate-400'
            }`}
          >
            {hasError ? '⚠️ HỆ ĐIỀU PHỐI ĐỨT GÃY LUỒNG' : data.engine || 'Autonomous Orchestrator v2.0'}
          </p>
        </div>
      </div>

      {/* Metrics Grid */}
      <div
        className={`mt-4 pt-3.5 border-t grid grid-cols-2 gap-2 text-xs font-mono ${
          hasError ? 'border-rose-800/80' : 'border-slate-800'
        }`}
      >
        <div
          className={`flex items-center gap-2 p-2 rounded-lg border ${
            hasError
              ? 'bg-rose-950/60 border-rose-500/40 text-rose-300'
              : 'bg-slate-900/80 border-slate-800/80'
          }`}
        >
          <Activity className={`w-4 h-4 ${hasError ? 'text-rose-400' : 'text-cyan-400'}`} />
          <div>
            <div className="text-[10px] text-slate-400">Agents</div>
            <div className={`font-bold ${hasError ? 'text-rose-300' : 'text-cyan-300'}`}>
              {data.activeAgents ?? 4} Active
            </div>
          </div>
        </div>

        <div
          className={`flex items-center gap-2 p-2 rounded-lg border ${
            hasError
              ? 'bg-rose-950/60 border-rose-500/40 text-rose-300'
              : 'bg-slate-900/80 border-slate-800/80'
          }`}
        >
          <Server className={`w-4 h-4 ${hasError ? 'text-rose-400' : 'text-emerald-400'}`} />
          <div>
            <div className="text-[10px] text-slate-400">Workers</div>
            <div className={`font-bold ${hasError ? 'text-rose-300' : 'text-emerald-300'}`}>
              {data.connectedWorkers ?? 17} Nodes
            </div>
          </div>
        </div>
      </div>

      {/* Output Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="core-out"
        className={`!w-3.5 !h-3.5 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-cyan-400'
        }`}
      />
    </div>
  );
});

CoreNode.displayName = 'CoreNode';

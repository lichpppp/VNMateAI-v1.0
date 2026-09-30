import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import {
  GitFork,
  Cpu,
  Globe,
  Image,
  Mic,
  Search,
  Sparkles,
  X,
  AlertTriangle,
} from 'lucide-react';

export interface RouterNodeData {
  label?: string;
  gatewayType?: string;
  modelsSupported?: string[];
  status?: string;
  latency?: string;
  activeRequests?: number;
  isGlowing?: boolean;
  isError?: boolean;
  hasError?: boolean;
}

export const RouterNode = memo(({ data, selected }: NodeProps<RouterNodeData>) => {
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
      className={`relative min-w-[320px] max-w-[360px] p-5 rounded-2xl transition-all duration-300 backdrop-blur-2xl border-2 ${
        hasError
          ? 'border-rose-500 shadow-[0_0_40px_rgba(244,63,94,0.7)] bg-gradient-to-b from-rose-950/85 to-slate-950/95 ring-2 ring-rose-500/50 scale-[1.02]'
          : isGlowing
          ? 'border-orange-500 shadow-[0_0_45px_rgba(249,115,22,0.8)] bg-slate-950/95 scale-105'
          : selected
          ? 'border-indigo-400 shadow-[0_0_35px_rgba(99,102,241,0.6)] bg-slate-950/95'
          : 'border-indigo-500/60 shadow-[0_0_25px_rgba(99,102,241,0.25)] bg-[#03071c]/95 hover:border-indigo-400'
      }`}
    >
      {/* Visual Red X Badge if Connection is Lost */}
      {hasError && (
        <div
          className="absolute -top-3.5 -right-3.5 z-30 flex items-center justify-center w-8 h-8 rounded-full bg-rose-600 border-2 border-white shadow-[0_0_20px_#f43f5e] animate-bounce"
          title="Cổng 9Router AI Gateway mất tín hiệu kết nối!"
        >
          <X className="w-5 h-5 text-white stroke-[3.5]" />
        </div>
      )}

      {/* Input Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id="router-in"
        className={`!w-3.5 !h-3.5 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-indigo-400'
        }`}
      />

      {/* Top Cyber Badge */}
      <div className="flex items-center justify-between gap-2 mb-3">
        <span
          className={`px-2.5 py-0.5 text-[10px] font-mono font-extrabold uppercase rounded border tracking-wider ${
            hasError
              ? 'text-rose-400 bg-rose-950/80 border-rose-500/60'
              : 'text-indigo-300 bg-indigo-950/80 border-indigo-500/40'
          }`}
        >
          AI DISPATCH GATEWAY
        </span>
        <div
          className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border ${
            hasError ? 'bg-rose-950/90 border-rose-500' : 'bg-slate-900 border-slate-800'
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
              <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
              <span className="text-[11px] font-mono text-emerald-400 font-bold">Online</span>
            </>
          )}
        </div>
      </div>

      {/* Main Info */}
      <div className="flex items-center gap-3.5">
        <div
          className={`p-3 rounded-xl border-2 shadow-inner flex items-center justify-center flex-shrink-0 ${
            hasError
              ? 'bg-rose-900/40 border-rose-500/80 text-rose-400'
              : 'bg-gradient-to-br from-indigo-600/30 to-purple-800/40 border-indigo-400/40 text-indigo-300'
          }`}
        >
          <GitFork className="w-7 h-7 animate-pulse" />
        </div>
        <div className="overflow-hidden">
          <h4
            className={`font-orbitron font-extrabold text-base tracking-wide truncate ${
              hasError ? 'text-rose-300' : 'text-indigo-200'
            }`}
          >
            {data.label || '9Router AI Gateway'}
          </h4>
          <p
            className={`text-xs font-mono truncate mt-0.5 ${
              hasError ? 'text-rose-400 font-bold' : 'text-slate-400'
            }`}
          >
            {hasError ? '⚠️ CỔNG ĐIỀU PHỐI AI ĐỨT KẾT NỐI' : 'Multi-Provider LLM Cognitive Routing'}
          </p>
        </div>
      </div>

      {/* Multi-Provider Badges */}
      <div className="mt-3.5 flex flex-wrap gap-1.5">
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-900 border border-indigo-500/30 text-[10px] font-mono text-slate-200">
          <Cpu className="w-3 h-3 text-cyan-400" /> LLMs: Gemini/Claude/DeepSeek
        </span>
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-900 border border-indigo-500/30 text-[10px] font-mono text-slate-200">
          <Search className="w-3 h-3 text-amber-400" /> Web Search
        </span>
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-900 border border-indigo-500/30 text-[10px] font-mono text-slate-200">
          <Image className="w-3 h-3 text-pink-400" /> Image Gen
        </span>
        <span className="flex items-center gap-1 px-2 py-0.5 rounded-lg bg-slate-900 border border-indigo-500/30 text-[10px] font-mono text-slate-200">
          <Mic className="w-3 h-3 text-emerald-400" /> Edge TTS
        </span>
      </div>

      {/* Stats Matrix Grid */}
      <div
        className={`mt-3 pt-3 border-t flex items-center justify-between text-[11px] font-mono ${
          hasError ? 'border-rose-800/80 text-rose-300' : 'border-slate-800 text-slate-400'
        }`}
      >
        <span className="flex items-center gap-1">
          {hasError ? (
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400 animate-pulse" />
          ) : (
            <Globe className="w-3.5 h-3.5 text-indigo-400" />
          )}
          {hasError ? 'Mất Kết Nối Gateway' : 'Multi-LLM Failover'}
        </span>
        <span className={`font-bold ${hasError ? 'text-rose-400' : 'text-cyan-400'}`}>
          {hasError ? 'Timeout' : `Ping: ${data.latency || '< 12ms'}`}
        </span>
      </div>

      {/* Output Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="router-out"
        className={`!w-3.5 !h-3.5 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-indigo-400'
        }`}
      />
    </div>
  );
});

RouterNode.displayName = 'RouterNode';

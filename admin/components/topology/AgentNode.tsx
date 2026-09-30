import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Bot, UserCheck, Code2, LineChart, Briefcase, Zap, X, AlertTriangle } from 'lucide-react';

export interface AgentNodeData {
  label?: string;
  role?: string;
  description?: string;
  model?: string;
  status?: string;
  intentCount?: number;
  isGlowing?: boolean;
  isError?: boolean;
  hasError?: boolean;
}

export const AgentNode = memo(({ data, selected }: NodeProps<AgentNodeData>) => {
  const isGlowing = data.isGlowing;
  const statusStr = (data.status || '').toLowerCase();
  const hasError =
    data.isError ||
    data.hasError ||
    statusStr === 'error' ||
    statusStr === 'offline' ||
    statusStr === 'disconnected';

  const getRoleConfig = (role?: string) => {
    const r = (role || '').toLowerCase();
    if (r.includes('ceo') || r.includes('router')) {
      return {
        icon: Bot,
        badge: 'CEO ROUTER',
        border: 'border-violet-500/60 hover:border-violet-400',
        glow: 'shadow-[0_0_25px_rgba(168,85,247,0.3)]',
        color: 'text-violet-400',
        bgColor: 'from-violet-600/30 to-purple-800/30',
      };
    }
    if (r.includes('cto') || r.includes('it') || r.includes('aiops')) {
      return {
        icon: Code2,
        badge: 'CTO / AIOPS',
        border: 'border-sky-500/60 hover:border-sky-400',
        glow: 'shadow-[0_0_25px_rgba(14,165,233,0.3)]',
        color: 'text-sky-400',
        bgColor: 'from-sky-600/30 to-blue-800/30',
      };
    }
    if (r.includes('hr') || r.includes('nhân sự')) {
      return {
        icon: UserCheck,
        badge: 'HR AGENT',
        border: 'border-pink-500/60 hover:border-pink-400',
        glow: 'shadow-[0_0_25px_rgba(236,72,153,0.3)]',
        color: 'text-pink-400',
        bgColor: 'from-pink-600/30 to-rose-800/30',
      };
    }
    if (r.includes('cfo') || r.includes('tài chính') || r.includes('finance')) {
      return {
        icon: LineChart,
        badge: 'CFO AGENT',
        border: 'border-amber-500/60 hover:border-amber-400',
        glow: 'shadow-[0_0_25px_rgba(245,158,11,0.3)]',
        color: 'text-amber-400',
        bgColor: 'from-amber-600/30 to-yellow-800/30',
      };
    }
    return {
      icon: Briefcase,
      badge: 'ENTERPRISE AGENT',
      border: 'border-indigo-500/60 hover:border-indigo-400',
      glow: 'shadow-[0_0_25px_rgba(99,102,241,0.3)]',
      color: 'text-indigo-400',
      bgColor: 'from-indigo-600/30 to-purple-800/30',
    };
  };

  const config = getRoleConfig(data.role || data.label);
  const IconComponent = config.icon;

  return (
    <div
      className={`relative min-w-[300px] max-w-[340px] p-5 rounded-2xl transition-all duration-300 backdrop-blur-2xl border-2 ${
        hasError
          ? 'border-rose-500 shadow-[0_0_40px_rgba(244,63,94,0.7)] bg-gradient-to-b from-rose-950/85 to-slate-950/95 ring-2 ring-rose-500/50 scale-[1.02]'
          : isGlowing
          ? 'border-orange-500 shadow-[0_0_40px_rgba(249,115,22,0.8)] bg-slate-950/95 scale-105'
          : selected
          ? 'border-cyan-400 shadow-[0_0_30px_rgba(0,242,254,0.5)] bg-slate-950/95'
          : `${config.border} ${config.glow} bg-slate-950/90`
      }`}
    >
      {/* Visual Red X Badge if Connection is Lost */}
      {hasError && (
        <div
          className="absolute -top-3.5 -right-3.5 z-30 flex items-center justify-center w-8 h-8 rounded-full bg-rose-600 border-2 border-white shadow-[0_0_20px_#f43f5e] animate-bounce"
          title="Mất kết nối Agent! Cảnh báo sự cố."
        >
          <X className="w-5 h-5 text-white stroke-[3.5]" />
        </div>
      )}

      {/* Input Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id="agent-in"
        className={`!w-3 !h-3 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-violet-400'
        }`}
      />

      {/* Top Bar with Badge & Status */}
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <span
          className={`px-2.5 py-0.5 text-[10px] font-mono font-bold uppercase rounded border tracking-wider ${
            hasError ? 'text-rose-400 bg-rose-950/80 border-rose-500/60' : `${config.color} bg-slate-900 border-current/30`
          }`}
        >
          {config.badge}
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
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span className="text-[11px] font-mono text-slate-300">{data.status || 'Active'}</span>
            </>
          )}
        </div>
      </div>

      {/* Agent Header */}
      <div className="flex items-start gap-3.5">
        <div
          className={`p-3 rounded-xl border flex-shrink-0 ${
            hasError
              ? 'bg-rose-900/40 border-rose-500/80 text-rose-400'
              : `bg-gradient-to-br ${config.bgColor} border-current/30 ${config.color}`
          }`}
        >
          <IconComponent className="w-6 h-6" />
        </div>
        <div className="overflow-hidden">
          <h4
            className={`font-orbitron font-bold text-base truncate ${
              hasError ? 'text-rose-300' : 'text-slate-100'
            }`}
          >
            {data.label || 'Specialist Agent'}
          </h4>
          <p
            className={`text-xs mt-1 leading-snug line-clamp-2 ${
              hasError ? 'text-rose-400 font-medium' : 'text-slate-400'
            }`}
          >
            {hasError ? '⚠️ Tác nhân mất liên lạc với hệ thống điều phối' : data.description}
          </p>
        </div>
      </div>

      {/* Footer Info */}
      <div
        className={`mt-3.5 pt-3 border-t flex items-center justify-between text-[11px] font-mono ${
          hasError ? 'border-rose-800/80 text-rose-300' : 'border-slate-800 text-slate-400'
        }`}
      >
        <span className="truncate max-w-[160px]">
          Model: {data.model || 'Gemini 2.5 Flash'}
        </span>
        <span
          className={`flex items-center gap-1 font-semibold ${
            hasError ? 'text-rose-400' : 'text-cyan-400'
          }`}
        >
          {hasError ? (
            <>
              <AlertTriangle className="w-3.5 h-3.5" /> Disconnected
            </>
          ) : (
            <>
              <Zap className="w-3.5 h-3.5" /> Ready
            </>
          )}
        </span>
      </div>

      {/* Output Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="agent-out"
        className={`!w-3 !h-3 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-violet-400'
        }`}
      />
    </div>
  );
});

AgentNode.displayName = 'AgentNode';

import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import {
  Cloud,
  Layers,
  FileText,
  Mail,
  Cpu,
  Receipt,
  ExternalLink,
  Shield,
  X,
  AlertTriangle,
  Send,
  Users,
} from 'lucide-react';

export interface ConnectorNodeData {
  label?: string;
  connectorType?: string;
  status?: string;
  circuitState?: string;
  lastAction?: string;
  isGlowing?: boolean;
  isError?: boolean;
  hasError?: boolean;
}

export const ConnectorNode = memo(({ data, selected }: NodeProps<ConnectorNodeData>) => {
  const isGlowing = data.isGlowing;
  const statusStr = (data.status || '').toLowerCase();
  const circuitStr = (data.circuitState || '').toLowerCase();
  const hasError =
    data.isError ||
    data.hasError ||
    statusStr === 'error' ||
    statusStr === 'offline' ||
    statusStr === 'disconnected' ||
    circuitStr.includes('open');

  const getConnectorConfig = (type?: string, label?: string) => {
    const t = (type || label || '').toLowerCase();
    if (t.includes('aws')) {
      return {
        icon: Cloud,
        badge: 'AWS CLOUD',
        border: 'border-amber-500/60 hover:border-amber-400',
        glow: 'shadow-[0_0_25px_rgba(245,158,11,0.25)]',
        color: 'text-amber-400',
        bg: 'from-amber-600/30 to-orange-800/30',
      };
    }
    if (t.includes('oci') || t.includes('oracle')) {
      return {
        icon: Layers,
        badge: 'ORACLE OCI',
        border: 'border-red-500/60 hover:border-red-400',
        glow: 'shadow-[0_0_25px_rgba(239,68,68,0.25)]',
        color: 'text-red-400',
        bg: 'from-red-600/30 to-rose-800/30',
      };
    }
    if (t.includes('paperless')) {
      return {
        icon: FileText,
        badge: 'PAPERLESS-NGX',
        border: 'border-teal-500/60 hover:border-teal-400',
        glow: 'shadow-[0_0_25px_rgba(20,184,166,0.25)]',
        color: 'text-teal-400',
        bg: 'from-teal-600/30 to-emerald-800/30',
      };
    }
    if (t.includes('m365') || t.includes('microsoft') || t.includes('office')) {
      return {
        icon: Mail,
        badge: 'MICROSOFT 365',
        border: 'border-blue-500/60 hover:border-blue-400',
        glow: 'shadow-[0_0_25px_rgba(59,130,246,0.3)]',
        color: 'text-blue-400',
        bg: 'from-blue-600/30 to-indigo-800/30',
      };
    }
    if (t.includes('telegram')) {
      return {
        icon: Send,
        badge: 'TELEGRAM BOT GATEWAY',
        border: 'border-sky-500/60 hover:border-sky-400',
        glow: 'shadow-[0_0_25px_rgba(14,165,233,0.3)]',
        color: 'text-sky-400',
        bg: 'from-sky-600/30 to-blue-800/30',
      };
    }
    if (t.includes('ad') || t.includes('ldap') || t.includes('directory')) {
      return {
        icon: Users,
        badge: 'ACTIVE DIRECTORY / LDAP',
        border: 'border-indigo-500/60 hover:border-indigo-400',
        glow: 'shadow-[0_0_25px_rgba(99,102,241,0.3)]',
        color: 'text-indigo-400',
        bg: 'from-indigo-600/30 to-blue-900/30',
      };
    }
    if (t.includes('einvoice') || t.includes('hóa đơn')) {
      return {
        icon: Receipt,
        badge: 'E-INVOICE VN',
        border: 'border-emerald-500/60 hover:border-emerald-400',
        glow: 'shadow-[0_0_25px_rgba(16,185,129,0.25)]',
        color: 'text-emerald-400',
        bg: 'from-emerald-600/30 to-teal-800/30',
      };
    }
    if (t.includes('legacy') || t.includes('llm') || t.includes('ai')) {
      return {
        icon: Cpu,
        badge: 'LEGACY AI / ON-PREM',
        border: 'border-purple-500/60 hover:border-purple-400',
        glow: 'shadow-[0_0_25px_rgba(168,85,247,0.25)]',
        color: 'text-purple-400',
        bg: 'from-purple-600/30 to-pink-800/30',
      };
    }
    return {
      icon: ExternalLink,
      badge: 'EXTERNAL PLUGIN',
      border: 'border-cyan-500/60 hover:border-cyan-400',
      glow: 'shadow-[0_0_25px_rgba(0,242,254,0.25)]',
      color: 'text-cyan-400',
      bg: 'from-cyan-600/30 to-blue-800/30',
    };
  };

  const config = getConnectorConfig(data.connectorType, data.label);
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
          title="Mất kết nối luồng! Cảnh báo sự cố."
        >
          <X className="w-5 h-5 text-white stroke-[3.5]" />
        </div>
      )}

      {/* Input Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id="connector-in"
        className={`!w-3 !h-3 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-amber-400'
        }`}
      />

      {/* Header Badge */}
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
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
              <span className="text-[11px] font-mono text-emerald-400 font-semibold">Ready</span>
            </>
          )}
        </div>
      </div>

      {/* Title & Icon */}
      <div className="flex items-center gap-3.5">
        <div
          className={`p-3 rounded-xl border flex-shrink-0 ${
            hasError
              ? 'bg-rose-900/40 border-rose-500/80 text-rose-400'
              : `bg-gradient-to-br ${config.bg} border-current/30 ${config.color}`
          }`}
        >
          <IconComponent className="w-6 h-6" />
        </div>
        <div className="min-w-0 flex-1">
          <h4
            className={`font-orbitron font-bold text-sm tracking-wide truncate ${
              hasError ? 'text-rose-300' : 'text-white'
            }`}
          >
            {data.label || 'Connector'}
          </h4>
          <p
            className={`text-xs font-mono truncate mt-0.5 ${
              hasError ? 'text-rose-400 font-bold' : 'text-slate-400'
            }`}
          >
            {hasError
              ? '⚠️ CIRCUIT OPEN / DISCONNECTED'
              : `Circuit: ${data.circuitState || 'CLOSED (Healthy)'}`}
          </p>
        </div>
      </div>

      {/* Footer Info */}
      <div
        className={`mt-3.5 pt-3 border-t flex items-center justify-between text-[11px] font-mono ${
          hasError ? 'border-rose-800/80 text-rose-300' : 'border-slate-800 text-slate-400'
        }`}
      >
        <span className="flex items-center gap-1">
          {hasError ? (
            <AlertTriangle className="w-3.5 h-3.5 text-rose-400 animate-pulse" />
          ) : (
            <Shield className="w-3.5 h-3.5 text-cyan-400" />
          )}
          {hasError ? 'Lỗi Luồng Kết Nối' : 'Circuit Breaker'}
        </span>
        <span
          className={`truncate max-w-[130px] font-semibold ${
            hasError ? 'text-rose-400' : 'text-cyan-400'
          }`}
        >
          {hasError ? 'Tín hiệu ngắt' : data.lastAction || 'Idle'}
        </span>
      </div>

      {/* Output Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="connector-out"
        className={`!w-3 !h-3 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-amber-400'
        }`}
      />
    </div>
  );
});

ConnectorNode.displayName = 'ConnectorNode';

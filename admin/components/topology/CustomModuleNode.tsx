import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { Box, Plug, Database, Webhook, Settings2, X, AlertTriangle, Bot, Radio } from 'lucide-react';

export interface CustomModuleData {
  label?: string;
  category?: string;
  endpoint?: string;
  status?: string;
  description?: string;
  isGlowing?: boolean;
  isError?: boolean;
  hasError?: boolean;
}

export const CustomModuleNode = memo(({ data, selected }: NodeProps<CustomModuleData>) => {
  const isGlowing = data.isGlowing;
  const statusStr = (data.status || '').toLowerCase();
  const hasError =
    data.isError ||
    data.hasError ||
    statusStr === 'error' ||
    statusStr === 'offline' ||
    statusStr === 'disconnected';

  const getCategoryIcon = (cat?: string, lbl?: string) => {
    const c = (cat || lbl || '').toLowerCase();
    if (c.includes('robot') || c.includes('companion') || c.includes('voice')) {
      return Bot;
    }
    if (c.includes('db') || c.includes('data') || c.includes('sql') || c.includes('sqlite')) {
      return Database;
    }
    if (c.includes('hook') || c.includes('api') || c.includes('rest')) {
      return Webhook;
    }
    if (c.includes('plug') || c.includes('connect')) {
      return Plug;
    }
    return Box;
  };

  const IconComp = getCategoryIcon(data.category, data.label);

  return (
    <div
      className={`relative min-w-[300px] max-w-[340px] p-5 rounded-2xl transition-all duration-300 backdrop-blur-2xl border-2 ${
        hasError
          ? 'border-rose-500 shadow-[0_0_40px_rgba(244,63,94,0.7)] bg-gradient-to-b from-rose-950/85 to-slate-950/95 ring-2 ring-rose-500/50 scale-[1.02]'
          : isGlowing
          ? 'border-orange-500 shadow-[0_0_40px_rgba(249,115,22,0.8)] bg-slate-950/95 scale-105'
          : selected
          ? 'border-cyan-400 shadow-[0_0_30px_rgba(0,242,254,0.5)] bg-slate-950/95'
          : 'border-slate-700 shadow-[0_0_20px_rgba(0,0,0,0.5)] bg-slate-950/90 hover:border-cyan-500/60'
      }`}
    >
      {/* Visual Red X Badge if Connection is Lost */}
      {hasError && (
        <div
          className="absolute -top-3.5 -right-3.5 z-30 flex items-center justify-center w-8 h-8 rounded-full bg-rose-600 border-2 border-white shadow-[0_0_20px_#f43f5e] animate-bounce"
          title="Module mất kết nối!"
        >
          <X className="w-5 h-5 text-white stroke-[3.5]" />
        </div>
      )}

      {/* Input Handle (Left) */}
      <Handle
        type="target"
        position={Position.Left}
        id="custom-in"
        className={`!w-3 !h-3 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-cyan-400'
        }`}
      />

      {/* Header Bar */}
      <div className="flex items-center justify-between gap-2 mb-2.5">
        <span
          className={`px-2.5 py-0.5 text-[10px] font-mono font-bold uppercase rounded border tracking-wider ${
            hasError
              ? 'text-rose-400 bg-rose-950/80 border-rose-500/60'
              : 'text-slate-300 bg-slate-900 border-slate-700'
          }`}
        >
          {data.category || 'CUSTOM MODULE'}
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
              <span className="text-[11px] font-mono text-emerald-400 font-semibold">
                {data.status || 'Active'}
              </span>
            </>
          )}
        </div>
      </div>

      {/* Module Info */}
      <div className="flex items-center gap-3.5">
        <div
          className={`p-3 rounded-xl border flex items-center justify-center flex-shrink-0 ${
            hasError
              ? 'bg-rose-900/40 border-rose-500/80 text-rose-400'
              : 'bg-slate-900 border-slate-700 text-cyan-400'
          }`}
        >
          <IconComp className="w-6 h-6" />
        </div>
        <div className="overflow-hidden">
          <h4
            className={`font-orbitron font-bold text-sm truncate ${
              hasError ? 'text-rose-300' : 'text-white'
            }`}
          >
            {data.label || 'New Module'}
          </h4>
          <p
            className={`text-xs font-mono truncate mt-0.5 ${
              hasError ? 'text-rose-400 font-bold' : 'text-slate-400'
            }`}
          >
            {hasError ? '⚠️ KHÔNG THỂ KẾT NỐI VỚI ENDPOINT' : data.endpoint || 'Ready'}
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
            <Settings2 className="w-3.5 h-3.5 text-slate-500" />
          )}
          {hasError ? 'Lỗi Luồng' : 'Plugin Custom'}
        </span>
        <span className={`font-semibold ${hasError ? 'text-rose-400' : 'text-emerald-400'}`}>
          {hasError ? 'Offline' : 'Online'}
        </span>
      </div>

      {/* Output Handle (Right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="custom-out"
        className={`!w-3 !h-3 !border-2 !border-slate-950 hover:!scale-125 transition-transform ${
          hasError ? '!bg-rose-500' : '!bg-cyan-400'
        }`}
      />
    </div>
  );
});

CustomModuleNode.displayName = 'CustomModuleNode';

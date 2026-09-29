'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useDashboard } from '@/hooks/useDashboard';
import { Loader2, CheckCircle, AlertCircle, AlertTriangle, Info, ExternalLink, RefreshCw, Activity } from 'lucide-react';
import { formatDate } from '@/lib/utils';

const logIcons = {
  info: Info,
  warning: AlertTriangle,
  error: AlertCircle,
  success: CheckCircle,
};

const logColors = {
  info: 'text-vnmate-cyan border-vnmate-cyan/30 bg-vnmate-cyan/10',
  warning: 'text-vnmate-amber border-vnmate-amber/30 bg-vnmate-amber/10',
  error: 'text-vnmate-pink border-vnmate-pink/30 bg-vnmate-pink/10',
  success: 'text-vnmate-emerald border-vnmate-emerald/30 bg-vnmate-emerald/10',
};

export function AuditLogFeed({ maxLogs = 50 }: { maxLogs?: number }) {
  const { auditLogs, loading, refetch } = useDashboard();

  const filteredLogs = auditLogs.slice(0, maxLogs);

  if (loading && filteredLogs.length === 0) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-vnmate-cyan" />
          <span className="ml-3 text-vnmate-slate-400">Loading audit logs...</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-full">
      <CardHeader className="border-b border-vnmate-slate-800">
        <div className="flex items-center justify-between">
          <CardTitle className="text-vnmate-cyan flex items-center gap-2">
            <Activity className="h-5 w-5" />
            Live Activity Feed
          </CardTitle>
          <Button variant="ghost" size="sm" onClick={refetch}>
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="max-h-[500px] overflow-y-auto custom-scrollbar">
          {filteredLogs.length === 0 ? (
            <div className="p-8 text-center text-vnmate-slate-500">
              <Info className="h-12 w-12 text-vnmate-slate-600 mx-auto mb-4" />
              <p>No activity logs yet</p>
            </div>
          ) : (
            <div className="divide-y divide-vnmate-slate-800">
              {filteredLogs.map((log) => {
                const Icon = logIcons[log.level];
                const colorClass = logColors[log.level];
                return (
                  <div
                    key={log.id}
                    className={cn(
                      'p-4 hover:bg-vnmate-slate-900/50 transition-colors animate-in slide-in-from-right',
                      log.level === 'error' && 'border-l-4 border-vnmate-pink',
                      log.level === 'warning' && 'border-l-4 border-vnmate-amber',
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <div className={cn('flex-shrink-0 mt-0.5 p-1.5 rounded', colorClass)}>
                        <Icon className="h-4 w-4" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs text-vnmate-cyan">{formatDate(log.timestamp)}</span>
                          <span className="px-2 py-0.5 text-xs bg-vnmate-slate-800 rounded text-vnmate-slate-400">
                            {log.source}
                          </span>
                          <span className={cn('px-2 py-0.5 text-xs rounded font-medium', colorClass)}>
                            {log.level.toUpperCase()}
                          </span>
                        </div>
                        <p className="mt-1 text-vnmate-neon text-sm">{log.message}</p>
                        {log.metadata && Object.keys(log.metadata).length > 0 && (
                          <details className="mt-2">
                            <summary className="text-xs text-vnmate-slate-500 cursor-pointer hover:text-vnmate-slate-400">
                              View metadata
                            </summary>
                            <pre className="mt-2 text-xs text-vnmate-slate-400 bg-vnmate-slate-900 p-2 rounded overflow-x-auto font-mono">
                              {JSON.stringify(log.metadata, null, 2)}
                            </pre>
                          </details>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
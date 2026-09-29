'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { getStatusDotClass } from '@/lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useWorkers } from '@/hooks/useDashboard';
import { Loader2, Server, Cpu, HardDrive, Wifi, Activity, AlertCircle, CheckCircle, Monitor, Smartphone } from 'lucide-react';

const typeIcons = {
  'mac-mini': Monitor,
  'ubuntu': Server,
  'esp32': Smartphone,
  'custom': Cpu,
};

const typeLabels = {
  'mac-mini': 'Mac Mini',
  'ubuntu': 'Ubuntu Server',
  'esp32': 'ESP32 Robot',
  'custom': 'Custom',
};

export function WorkerStatus() {
  const { workers, loading, refetch } = useWorkers();

  if (loading && workers.length === 0) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-vnmate-cyan" />
          <span className="ml-3 text-vnmate-slate-400">Loading workers...</span>
        </CardContent>
      </Card>
    );
  }

  const onlineCount = workers.filter(w => w.status === 'online').length;
  const totalCount = workers.length;

  return (
    <Card className="h-full">
      <CardHeader className="border-b border-vnmate-slate-800">
        <div className="flex items-center justify-between">
          <CardTitle className="text-vnmate-cyan flex items-center gap-2">
            <Server className="h-5 w-5" />
            External Workers
          </CardTitle>
          <div className="flex items-center gap-3">
            <span className={cn('px-2 py-1 text-sm font-mono', onlineCount === totalCount ? 'text-vnmate-emerald' : 'text-vnmate-amber')}>
              {onlineCount}/{totalCount} Online
            </span>
            <Button variant="ghost" size="sm" onClick={refetch}>
              <Loader2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {workers.length === 0 ? (
          <div className="p-8 text-center text-vnmate-slate-500">
            <Server className="h-12 w-12 text-vnmate-slate-600 mx-auto mb-4" />
            <p>No workers registered</p>
            <p className="text-sm mt-1">Connect external workers to enable distributed processing</p>
          </div>
        ) : (
          <div className="divide-y divide-vnmate-slate-800">
            {workers.map((worker) => {
              const TypeIcon = typeIcons[worker.type] || Server;
              const statusConfig = {
                online: { label: 'Online', dotClass: getStatusDotClass('online'), color: 'text-vnmate-emerald' },
                offline: { label: 'Offline', dotClass: getStatusDotClass('offline'), color: 'text-vnmate-slate-500' },
                busy: { label: 'Busy', dotClass: getStatusDotClass('warning'), color: 'text-vnmate-amber' },
                error: { label: 'Error', dotClass: getStatusDotClass('warning'), color: 'text-vnmate-pink' },
              };
              const status = statusConfig[worker.status] || statusConfig.offline;

              return (
                <div
                  key={worker.id}
                  className="p-4 hover:bg-vnmate-slate-900/50 transition-colors flex items-center gap-4"
                >
                  <div className={cn('p-3 rounded-xl bg-vnmate-slate-800/50 border', worker.status === 'online' ? 'border-vnmate-emerald/30' : 'border-vnmate-slate-700')}>
                    <TypeIcon className={cn('h-6 w-6', worker.status === 'online' ? 'text-vnmate-emerald' : 'text-vnmate-slate-400')} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-vnmate-neon">{worker.name}</span>
                      <span className={cn('px-2 py-0.5 text-xs rounded bg-vnmate-slate-800 text-vnmate-slate-400')}>
                        {typeLabels[worker.type] || worker.type}
                      </span>
                      <span className={cn('px-2 py-0.5 text-xs rounded font-medium', status.dotClass + ' bg-transparent text-current')}>
                        {status.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 text-sm text-vnmate-slate-500">
                      <span className="flex items-center gap-1"><Wifi className="h-3 w-3" /> {worker.ip}</span>
                      <span className="flex items-center gap-1"><Cpu className="h-3 w-3" /> {worker.specs.cpu}</span>
                      <span className="flex items-center gap-1"><HardDrive className="h-3 w-3" /> {worker.specs.memory}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-right">
                      <p className="font-mono text-xs text-vnmate-slate-400">Last seen</p>
                      <p className="font-mono text-xs text-vnmate-neon">{worker.lastSeen}</p>
                    </div>
                    {worker.currentTask && (
                      <span className="px-2 py-1 text-xs bg-vnmate-amber/10 text-vnmate-amber rounded">
                        {worker.currentTask}
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
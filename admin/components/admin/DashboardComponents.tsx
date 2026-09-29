'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Card, CardContent } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { getStatusDotClass, formatDate } from '@/lib/utils';
import { useDashboard } from '@/hooks/useDashboard';
import { Loader2, Server, Cpu, HardDrive, Activity, AlertTriangle, CheckCircle, XCircle, Clock, Users, Zap } from 'lucide-react';

export function DashboardStats() {
  const { stats, loading, refetch } = useDashboard();

  if (loading || !stats) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} className="animate-pulse">
            <CardContent className="p-6">
              <div className="h-4 w-3/4 bg-vnmate-slate-800 rounded mb-4" />
              <div className="h-8 w-1/2 bg-vnmate-slate-800 rounded" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  const statCards = [
    {
      title: 'External Workers',
      value: `${stats.workers.online}/${stats.workers.total}`,
      subtitle: `${stats.workers.offline} offline`,
      icon: Server,
      color: 'text-vnmate-cyan',
      bgColor: 'bg-vnmate-cyan/10',
      borderColor: 'border-vnmate-cyan/30',
      trend: stats.workers.online === stats.workers.total ? 'All systems operational' : `${stats.workers.offline} workers need attention`,
      trendColor: stats.workers.online === stats.workers.total ? 'text-vnmate-emerald' : 'text-vnmate-amber',
    },
    {
      title: 'Plugins Connected',
      value: `${stats.plugins.connected}/${stats.plugins.total}`,
      subtitle: `${stats.plugins.disconnected} disconnected`,
      icon: Zap,
      color: 'text-vnmate-emerald',
      bgColor: 'bg-vnmate-emerald/10',
      borderColor: 'border-vnmate-emerald/30',
      trend: stats.plugins.disconnected === 0 ? 'All plugins healthy' : `${stats.plugins.disconnected} plugins need config`,
      trendColor: stats.plugins.disconnected === 0 ? 'text-vnmate-emerald' : 'text-vnmate-amber',
    },
    {
      title: 'Tasks Today',
      value: stats.tasks.totalToday.toString(),
      subtitle: `${stats.tasks.completed} completed, ${stats.tasks.failed} failed`,
      icon: Activity,
      color: 'text-vnmate-amber',
      bgColor: 'bg-vnmate-amber/10',
      borderColor: 'border-vnmate-amber/30',
      trend: stats.tasks.pending > 0 ? `${stats.tasks.pending} pending` : 'All caught up',
      trendColor: stats.tasks.pending > 0 ? 'text-vnmate-amber' : 'text-vnmate-emerald',
    },
    {
      title: 'Pending Approvals',
      value: stats.approvals.pending.toString(),
      subtitle: `${stats.approvals.approved} approved, ${stats.approvals.rejected} rejected`,
      icon: AlertTriangle,
      color: 'text-vnmate-pink',
      bgColor: 'bg-vnmate-pink/10',
      borderColor: 'border-vnmate-pink/30',
      trend: stats.approvals.pending > 0 ? 'Action required' : 'All clear',
      trendColor: stats.approvals.pending > 0 ? 'text-vnmate-pink' : 'text-vnmate-emerald',
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
      {statCards.map((stat, index) => (
        <Card key={index} className={cn('card-hover border-l-4', stat.borderColor)}>
          <CardContent className="p-6">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-sm text-vnmate-slate-400 font-medium">{stat.title}</p>
                <p className="text-3xl font-orbitron font-bold text-vnmate-neon mt-1">{stat.value}</p>
                <p className="text-sm text-vnmate-slate-500 mt-1">{stat.subtitle}</p>
              </div>
              <div className={cn('p-3 rounded-xl', stat.bgColor)}>
                <stat.icon className={cn('h-6 w-6', stat.color)} />
              </div>
            </div>
            <div className="mt-4 pt-4 border-t border-vnmate-slate-800 flex items-center justify-between">
              <span className={cn('text-sm font-medium', stat.trendColor)}>{stat.trend}</span>
              <Button variant="ghost" size="sm" onClick={refetch}>
                <Loader2 className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function SystemHealth() {
  const { stats } = useDashboard();

  if (!stats) return null;

  return (
    <Card>
      <CardContent className="p-6">
        <h3 className="text-lg font-semibold text-vnmate-neon mb-4 flex items-center gap-2">
          <Activity className="h-5 w-5 text-vnmate-cyan" />
          System Health
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-vnmate-slate-400">CPU Usage</span>
              <span className="font-mono text-vnmate-neon">{stats.system.cpu}%</span>
            </div>
            <div className="h-2 bg-vnmate-slate-800 rounded-full overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all duration-500', stats.system.cpu > 80 ? 'bg-vnmate-pink' : stats.system.cpu > 60 ? 'bg-vnmate-amber' : 'bg-vnmate-emerald')}
                style={{ width: `${stats.system.cpu}%` }}
              />
            </div>
          </div>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-vnmate-slate-400">Memory Usage</span>
              <span className="font-mono text-vnmate-neon">{stats.system.memory}%</span>
            </div>
            <div className="h-2 bg-vnmate-slate-800 rounded-full overflow-hidden">
              <div
                className={cn('h-full rounded-full transition-all duration-500', stats.system.memory > 80 ? 'bg-vnmate-pink' : stats.system.memory > 60 ? 'bg-vnmate-amber' : 'bg-vnmate-emerald')}
                style={{ width: `${stats.system.memory}%` }}
              />
            </div>
          </div>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-vnmate-slate-400">Uptime</span>
              <span className="font-mono text-vnmate-neon">{stats.system.uptime}</span>
            </div>
            <div className="h-2 bg-vnmate-slate-800 rounded-full overflow-hidden">
              <div className="h-full rounded-full bg-vnmate-cyan/20" style={{ width: '100%' }} />
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
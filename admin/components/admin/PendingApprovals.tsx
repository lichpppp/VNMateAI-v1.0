'use client';

import React from 'react';
import { cn } from '@/lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useDashboard } from '@/hooks/useDashboard';
import { Loader2, AlertTriangle, CheckCircle, XCircle, Clock, User, Shield, MessageSquare } from 'lucide-react';
import { formatDate } from '@/lib/utils';

const riskConfig = {
  1: { label: 'Low', color: 'text-vnmate-emerald', bg: 'bg-vnmate-emerald/10 border-vnmate-emerald/30' },
  2: { label: 'Low-Medium', color: 'text-vnmate-cyan', bg: 'bg-vnmate-cyan/10 border-vnmate-cyan/30' },
  3: { label: 'Medium', color: 'text-vnmate-amber', bg: 'bg-vnmate-amber/10 border-vnmate-amber/30' },
  4: { label: 'High', color: 'text-vnmate-orange', bg: 'bg-vnmate-orange/10 border-vnmate-orange/30' },
  5: { label: 'Critical', color: 'text-vnmate-pink', bg: 'bg-vnmate-pink/10 border-vnmate-pink/30' },
};

export function PendingApprovals({ maxApprovals = 10 }: { maxApprovals?: number }) {
  const { approvals, loading, approveRequest, refetch } = useDashboard();

  const filteredApprovals = approvals
    .filter(a => a.status === 'pending')
    .slice(0, maxApprovals);

  const handleApprove = async (id: string, approved: boolean) => {
    const note = approved ? 'Approved via Admin Dashboard' : 'Rejected via Admin Dashboard';
    await approveRequest(id, approved, note);
  };

  if (loading && filteredApprovals.length === 0) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-vnmate-cyan" />
          <span className="ml-3 text-vnmate-slate-400">Loading approvals...</span>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="h-full">
      <CardHeader className="border-b border-vnmate-slate-800">
        <div className="flex items-center justify-between">
          <CardTitle className="text-vnmate-pink flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Pending Approvals
          </CardTitle>
          {filteredApprovals.length > 0 && (
            <span className="px-2 py-1 text-xs font-mono bg-vnmate-pink/10 text-vnmate-pink rounded">
              {filteredApprovals.length} pending
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {filteredApprovals.length === 0 ? (
          <div className="p-8 text-center text-vnmate-slate-500">
            <CheckCircle className="h-12 w-12 text-vnmate-emerald/50 mx-auto mb-4" />
            <p className="text-vnmate-neon">All caught up!</p>
            <p className="text-sm mt-1">No pending approval requests</p>
          </div>
        ) : (
          <div className="divide-y divide-vnmate-slate-800">
            {filteredApprovals.map((approval) => {
              const risk = riskConfig[approval.riskLevel];
              return (
                <div
                  key={approval.id}
                  className={cn(
                    'p-4 hover:bg-vnmate-slate-900/50 transition-colors',
                    approval.riskLevel >= 4 && 'border-l-4 border-vnmate-pink bg-vnmate-pink/5'
                  )}
                >
                  <div className="flex items-start gap-4">
                    <div className={cn('flex-shrink-0 p-2 rounded-lg', risk.bg)}>
                      <AlertTriangle className={cn('h-5 w-5', risk.color)} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-vnmate-neon">{approval.taskType}</span>
                        <span className={cn('px-2 py-0.5 text-xs rounded font-medium', risk.bg)}>
                          Risk Level {approval.riskLevel} - {risk.label}
                        </span>
                        <span className="text-xs text-vnmate-slate-500">
                          {formatDate(approval.timestamp)}
                        </span>
                      </div>
                      <p className="mt-2 text-vnmate-slate-300 text-sm">{approval.description}</p>
                      <div className="mt-2 flex items-center gap-3 text-xs text-vnmate-slate-500">
                        <span className="flex items-center gap-1"><User className="h-3 w-3" /> {approval.requestedBy}</span>
                        <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> Requested {formatDate(approval.timestamp)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <Button
                        variant="cyber-emerald"
                        size="sm"
                        onClick={() => handleApprove(approval.id, true)}
                        disabled={loading}
                      >
                        <CheckCircle className="mr-1.5 h-3.5 w-3.5" />
                        Approve
                      </Button>
                      <Button
                        variant="cyber-destructive"
                        size="sm"
                        onClick={() => handleApprove(approval.id, false)}
                        disabled={loading}
                      >
                        <XCircle className="mr-1.5 h-3.5 w-3.5" />
                        Reject
                      </Button>
                    </div>
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
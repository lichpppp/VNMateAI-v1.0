'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { api, type DashboardStats, type AuditLog, type ApprovalRequest, type Worker } from '@/lib/api';
import { useToast } from '@/hooks/useToast';

export function useDashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { success: toastSuccess, error: toastError, warning: toastWarning } = useToast();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();

  const fetchStats = useCallback(async () => {
    try {
      const data = await api.getDashboardStats();
      setStats(data);
    } catch (err) {
      console.error('Failed to fetch stats:', err);
    }
  }, []);

  const fetchAuditLogs = useCallback(async () => {
    try {
      const data = await api.getAuditLogs(100);
      setAuditLogs(data);
    } catch (err) {
      console.error('Failed to fetch audit logs:', err);
    }
  }, []);

  const fetchApprovals = useCallback(async () => {
    try {
      const data = await api.getPendingApprovals();
      setApprovals(data);
    } catch (err) {
      console.error('Failed to fetch approvals:', err);
    }
  }, []);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      await Promise.all([fetchStats(), fetchAuditLogs(), fetchApprovals()]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch dashboard data');
    } finally {
      setLoading(false);
    }
  }, [fetchStats, fetchAuditLogs, fetchApprovals]);

  // WebSocket connection for real-time updates
  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const wsUrl = `${process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000'}/ws/admin`;
      wsRef.current = new WebSocket(wsUrl);

      wsRef.current.onopen = () => {
        console.log('Admin WebSocket connected');
      };

      wsRef.current.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          handleWebSocketMessage(message);
        } catch (err) {
          console.error('Failed to parse WS message:', err);
        }
      };

      wsRef.current.onclose = () => {
        console.log('Admin WebSocket disconnected, reconnecting...');
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 5000);
      };

      wsRef.current.onerror = (error) => {
        console.error('WebSocket error:', error);
      };
    } catch (err) {
      console.error('Failed to create WebSocket:', err);
    }
  }, []);

  const handleWebSocketMessage = (message: { type: string; data: unknown }) => {
    switch (message.type) {
      case 'audit_log':
        setAuditLogs(prev => [message.data as AuditLog, ...prev.slice(0, 99)]);
        break;
      case 'approval_request':
        setApprovals(prev => [message.data as ApprovalRequest, ...prev]);
        toastWarning('New approval request', (message.data as ApprovalRequest).description);
        break;
      case 'approval_update':
        setApprovals(prev => prev.map(a => a.id === (message.data as ApprovalRequest).id ? message.data as ApprovalRequest : a));
        break;
      case 'stats_update':
        setStats(message.data as DashboardStats);
        break;
      case 'worker_status':
        // Handled by useWorkers hook
        break;
    }
  };

  const approveRequest = async (id: string, approved: boolean, note?: string): Promise<boolean> => {
    try {
      await api.approveRequest(id, approved, note);
      toastSuccess(
        approved ? 'Request approved' : 'Request rejected',
        `Approval request has been ${approved ? 'approved' : 'rejected'}`
      );
      await fetchApprovals();
      return true;
    } catch (err) {
      toastError('Failed to process approval', err instanceof Error ? err.message : 'Unknown error');
      return false;
    }
  };

  useEffect(() => {
    fetchAll();
    connectWebSocket();

    // Poll stats every 30 seconds
    const statsInterval = setInterval(fetchStats, 30000);
    // Poll audit logs every 10 seconds
    const logsInterval = setInterval(fetchAuditLogs, 10000);
    // Poll approvals every 15 seconds
    const approvalsInterval = setInterval(fetchApprovals, 15000);

    return () => {
      clearInterval(statsInterval);
      clearInterval(logsInterval);
      clearInterval(approvalsInterval);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close();
    };
  }, [fetchAll, connectWebSocket]);

  return {
    stats,
    auditLogs,
    approvals,
    loading,
    error,
    refetch: fetchAll,
    approveRequest,
  };
}

export function useWorkers() {
  const [workers, setWorkers] = useState<Worker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();

  const fetchWorkers = useCallback(async () => {
    try {
      const data = await api.getWorkers();
      setWorkers(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch workers');
    } finally {
      setLoading(false);
    }
  }, []);

  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const wsUrl = `${process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000'}/ws/workers`;
      wsRef.current = new WebSocket(wsUrl);

      wsRef.current.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === 'worker_status') {
            setWorkers(prev => prev.map(w => 
              w.id === message.data.id ? { ...w, ...message.data } : w
            ));
          }
        } catch (err) {
          console.error('Failed to parse worker WS message:', err);
        }
      };

      wsRef.current.onclose = () => {
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 5000);
      };
    } catch (err) {
      console.error('Failed to create worker WebSocket:', err);
    }
  }, []);

  useEffect(() => {
    fetchWorkers();
    connectWebSocket();

    const interval = setInterval(fetchWorkers, 30000);

    return () => {
      clearInterval(interval);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      wsRef.current?.close();
    };
  }, [fetchWorkers, connectWebSocket]);

  return {
    workers,
    loading,
    error,
    refetch: fetchWorkers,
  };
}
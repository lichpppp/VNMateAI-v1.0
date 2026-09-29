'use client';

import React from 'react';
import { DashboardStats, SystemHealth } from '@/components/admin/DashboardComponents';
import { AuditLogFeed } from '@/components/admin/AuditLogFeed';
import { PendingApprovals } from '@/components/admin/PendingApprovals';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';

export default function AdminDashboard() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <DashboardStats />
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2">
              <AuditLogFeed maxLogs={40} />
            </div>
            <div className="space-y-6">
              <SystemHealth />
              <PendingApprovals />
            </div>
          </div>
        </main>
      </div>
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <Toaster />
    </div>
  );
}

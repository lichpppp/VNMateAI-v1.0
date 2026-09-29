'use client';

import React from 'react';
import { WorkerStatus } from '@/components/admin/WorkerStatus';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';
import { Loader2, RefreshCw, Plus } from 'lucide-react';
import { Button } from '@/components/ui/Button';

export default function AdminWorkers() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-orbitron font-bold text-vnmate-cyan">External Workers</h1>
              <p className="text-vnmate-slate-400 mt-1">Monitor and manage distributed compute workers</p>
            </div>
            <Button variant="cyber">
              <Plus className="mr-2 h-4 w-4" />
              Add Worker
            </Button>
          </div>
          <WorkerStatus />
        </main>
      </div>
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          onClick={() => setSidebarOpen(false)}
          aria-hidden="true"
        />
      )}
      <Toaster />
    </div>
  );
}
'use client';

import React from 'react';
import { RoutingBuilder } from '@/components/admin/RoutingBuilder';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';

export default function AdminRouting() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8">
          <RoutingBuilder onRulesChange={() => {}} />
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
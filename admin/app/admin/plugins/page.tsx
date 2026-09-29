'use client';

import React from 'react';
import { usePlugins } from '@/hooks/usePlugins';
import { PluginCard } from '@/components/admin/PluginCard';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';
import { Button } from '@/components/ui/Button';
import { RefreshCw, CircleAlert, ServerOff } from 'lucide-react';
import { cn } from '@/lib/utils';

export default function AdminPlugins() {
  const { plugins, loading, error, refetch, testConnection, testing } = usePlugins();
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  const waitingCount = plugins.filter((p) => !p.configured).length;

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h1 className="text-3xl font-orbitron font-bold text-cyan-300">Kho Connector</h1>
              <p className="text-slate-400 mt-1">
                Danh mục kết nối do backend khai báo — chỉ connector có thật mới hiện.
              </p>
            </div>
            <Button variant="outline" onClick={refetch} disabled={loading}>
              <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
              Tải lại
            </Button>
          </div>

          {error ? (
            <div className="p-4 rounded-lg bg-pink-500/10 border border-pink-500/30 flex items-start gap-3">
              <ServerOff className="w-5 h-5 text-pink-400 shrink-0 mt-0.5" />
              <div>
                <p className="text-pink-300 font-medium">Không tải được danh mục connector</p>
                <p className="text-sm text-pink-200/70 mt-1">
                  {error} — kiểm tra backend có chạy ở cổng 8000 và token đã đăng nhập.
                </p>
              </div>
            </div>
          ) : loading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-56 rounded-xl bg-slate-800/50 border border-slate-800 animate-pulse" />
              ))}
            </div>
          ) : plugins.length === 0 ? (
            <div className="py-16 text-center">
              <ServerOff className="w-12 h-12 text-slate-600 mx-auto mb-4" />
              <h2 className="text-lg font-semibold text-cyan-300 mb-2">Chưa có connector nào</h2>
              <p className="text-slate-400 max-w-md mx-auto">
                Backend chưa đăng ký connector nào trong <code>CONNECTOR_REGISTRY</code>.
                Trang này không tự chế danh sách để tránh hiển thị thứ hệ thống chưa có.
              </p>
            </div>
          ) : (
            <>
              {waitingCount > 0 && (
                <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-center gap-3">
                  <CircleAlert className="w-4 h-4 text-amber-400 shrink-0" />
                  <p className="text-sm text-amber-300">
                    {waitingCount}/{plugins.length} connector chưa đủ thông tin đăng nhập.
                  </p>
                </div>
              )}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {plugins.map((plugin) => (
                  <PluginCard
                    key={plugin.id}
                    plugin={plugin}
                    onTestConnection={testConnection}
                    testing={testing}
                  />
                ))}
              </div>
            </>
          )}
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

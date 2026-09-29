'use client';

import React from 'react';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';
import { Card, CardContent } from '@/components/ui/Card';
import { ServerOff, Info } from 'lucide-react';

/**
 * Trang "Worker" — hiện thông báo "chưa có nguồn dữ liệu" thay vì danh sách
 * máy bịa.
 *
 * Briefing mô tả "🟢 17/17 Mac Minis Online" và "Ubuntu Gateway Active", nhưng
 * hệ thống không có endpoint nào trả danh sách cụm máy (đã thử
 * /api/v1/workers → 404, /api/v1/domain/computers chỉ là máy trong AD chứ không
 * phải worker RPA). Vẽ 17/17 là bịa một con số có vẻ rất cụ thể — kiểu dữ liệu
 * mà người đọc không bao giờ nghi ngờ và cũng không bao giờ kiểm chứng được.
 *
 * Vì vậy trang này nói rõ là chưa có nguồn, và chỉ ra đúng endpoint cần có để
 * lấp đầy. Khi backend có endpoint thật, chỉ cần thay `useWorkers` — phần
 * hiển thị đã sẵn cấu trúc.
 */
export default function AdminWorkers() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);
  const reason =
    'Hệ thống chưa đăng ký cụm máy ngoại vi nào và chưa có endpoint nào trả danh sách worker.';

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <div>
            <h1 className="text-3xl font-orbitron font-bold text-cyan-300">Worker ngoại vi</h1>
            <p className="text-slate-400 mt-1">
              Theo dõi các máy chạy tác vụ RPA (Mac Mini, Ubuntu Gateway, Robot ESP32).
            </p>
          </div>

          <Card className="border-amber-500/30">
            <CardContent className="p-6">
              <div className="flex items-start gap-4">
                <ServerOff className="w-6 h-6 text-amber-400 shrink-0 mt-0.5" />
                <div>
                  <h2 className="text-lg font-semibold text-amber-300 mb-1.5">
                    Chưa có nguồn dữ liệu
                  </h2>
                  <p className="text-slate-300 text-sm leading-relaxed max-w-2xl">{reason}</p>
                  <p className="text-slate-400 text-sm leading-relaxed mt-3 max-w-2xl">
                    Trang này không hiển thị danh sách máy bịa. Để có dữ liệu, backend
                    cần một endpoint trả về worker, ví dụ{' '}
                    <code className="text-cyan-400">GET /api/v1/workers</code> với các
                    trường <code className="text-cyan-400">id</code>,{' '}
                    <code className="text-cyan-400">name</code>,{' '}
                    <code className="text-cyan-400">type</code>,{' '}
                    <code className="text-cyan-400">status</code>,{' '}
                    <code className="text-cyan-400">last_seen</code>.
                  </p>
                  <p className="text-xs text-slate-500 mt-4 flex items-start gap-1.5">
                    <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    Endpoint <code className="text-cyan-400">/api/v1/domain/computers</code>{' '}
                    có tồn tại nhưng trả máy tính trong Active Directory, không phải
                    worker RPA — nên không dùng làm nguồn cho trang này.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
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

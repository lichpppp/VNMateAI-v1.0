'use client';

import React from 'react';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';
import { Card, CardContent } from '@/components/ui/Card';
import { GitBranch, Database, Info } from 'lucide-react';

/**
 * Trang "Định tuyến" — báo rõ chưa có nguồn dữ liệu.
 *
 * Briefing yêu cầu lưu quy tắc định tuyến vào bảng `routing_rules` và để
 * backend đọc bảng đó ra quyết định. Đã kiểm tra thực tế:
 *
 *   - KHÔNG có bảng `routing_rules` trong CSDL (và cũng không có bảng nào
 *     chứa "rule").
 *   - KHÔNG có endpoint CRUD nào: /api/v1/routing/rules trả 404.
 *   - "routing" trong config.json chỉ là `router_models` — đó là định tuyến
 *     LLM (chọn model nào), KHÔNG phải định tuyến tác vụ (gửi tin nhắn qua
 *     kênh nào). Hai thứ trùng tên nhưng không liên quan.
 *   - Orchestrator không đọc bất kỳ bảng quy tắc nào.
 *
 * Nên hiện bảng rỗng rồi bấm "Thêm quy tắc" là một cái hộp thoại không bao
 * giờ lưu được. Thà nói thẳng là chưa có gì, kèm phần backend còn thiếu.
 *
 * `RoutingBuilder` (components/admin/RoutingBuilder.tsx) vẫn giữ nguyên — sẵn
 * sàng dùng ngay khi có endpoint CRUD.
 */
export default function AdminRouting() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <div>
            <h1 className="text-3xl font-orbitron font-bold text-cyan-300">Định tuyến tác vụ</h1>
            <p className="text-slate-400 mt-1">
              Quy tắc: khi gặp loại tác vụ này thì gửi qua kênh nào.
            </p>
          </div>

          <Card className="border-amber-500/30">
            <CardContent className="p-6">
              <div className="flex items-start gap-4">
                <GitBranch className="w-6 h-6 text-amber-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <h2 className="text-lg font-semibold text-amber-300 mb-1.5">
                    Chưa có nơi lưu quy tắc
                  </h2>
                  <p className="text-slate-300 text-sm leading-relaxed max-w-3xl">
                    Hệ thống chưa có bảng <code className="text-cyan-400">routing_rules</code>{' '}
                    và cũng không có endpoint quản lý quy tắc. Nên không có chỗ để
                    lưu, không có chỗ để đọc, và orchestrator chưa dùng quy tắc nào
                    để định tuyến.
                  </p>

                  <p className="text-slate-400 text-sm leading-relaxed mt-3 max-w-3xl">
                    Trang này không hiện bảng quy tắc bịa và không có nút lưu, vì
                    cả hai sẽ thành giao diện có vẻ hoạt động nhưng mọi thao tác
                    đều đi vào chỗ trống.
                  </p>

                  <div className="mt-5 p-4 rounded-lg bg-slate-900/60 border border-slate-800">
                    <p className="text-sm text-cyan-300 font-medium flex items-center gap-2 mb-2">
                      <Database className="w-4 h-4" />
                      Phần backend còn thiếu
                    </p>
                    <ul className="text-sm text-slate-400 space-y-1.5 list-disc pl-5">
                      <li>
                        Bảng <code className="text-cyan-400">routing_rules</code> cột:
                        id, name, condition (JSON), action (JSON), priority, enabled
                      </li>
                      <li>
                        <code className="text-cyan-400">GET /api/v1/routing/rules</code>{' '}
                        và <code className="text-cyan-400">POST</code> /{' '}
                        <code className="text-cyan-400">PATCH</code> /{' '}
                        <code className="text-cyan-400">DELETE /routing/rules/:id</code>
                      </li>
                      <li>Cho orchestrator đọc bảng này khi quyết định gửi tin qua kênh nào</li>
                    </ul>
                    <p className="text-xs text-slate-500 mt-3 flex items-start gap-1.5">
                      <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      Giao diện bảng và form quy tắc đã dựng sẵn trong{' '}
                      <code className="text-cyan-400">RoutingBuilder.tsx</code> — có
                      backend là nối vào chạy, không phải làm lại giao diện.
                    </p>
                  </div>
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

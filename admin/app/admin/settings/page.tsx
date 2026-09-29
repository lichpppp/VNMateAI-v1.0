'use client';

import React from 'react';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';
import { Card, CardContent } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Settings2, ArrowRight, ShieldCheck, Info } from 'lucide-react';

/**
 * Trang "Cài đặt" — không dựng lại trình chỉnh cấu hình.
 *
 * Backend có sẵn `GET`/`PUT /api/v1/config`, và trong docstring của nó ghi rõ:
 * "Sensitive fields (API keys) are returned to allow editing in the portal".
 * Nghĩa là portal ĐÃ CÓ trình chỉnh cấu hình — đó là chỗ chủ đích để sửa
 * khoá API, bot token, model, cổng.
 *
 * Dựng thêm một trình chỉnh bí mật ở Admin chỉ tạo ra hai nơi sửa cùng một
 * thứ: vừa là chỗ thứ hai phải giữ cho khớp, vừa làm tăng bề mặt để lộ
 * khoá. Endpoint đó trả khoá dạng chữ thường, nên càng nhiều nơi hiển thị
 * thì càng nhiều chỗ có thể lọt khoá ra.
 *
 * Vì vậy trang này chỉ dẫn đường về đúng chỗ, không chứa form ghi đè lên
 * config.
 */
export default function AdminSettings() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <div>
            <h1 className="text-3xl font-orbitron font-bold text-cyan-300">Cài đặt</h1>
            <p className="text-slate-400 mt-1">
              Cấu hình hệ thống, khoá API và thông tin nhận dạng.
            </p>
          </div>

          <Card className="border-cyan-500/30">
            <CardContent className="p-6">
              <div className="flex items-start gap-4">
                <Settings2 className="w-6 h-6 text-cyan-400 shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <h2 className="text-lg font-semibold text-cyan-300 mb-1.5">
                    Cấu hình nằm ở trình chỉnh của portal
                  </h2>
                  <p className="text-slate-300 text-sm leading-relaxed max-w-3xl">
                    Portal đã có sẵn màn hình cấu hình, và đó là nơi chủ đích để sửa
                    khoá API, bot token, model và cổng. Trang này không dựng lại
                    trình chỉnh đó — vì endpoint cấu hình trả khoá bí mật dạng
                    chữ thường, nên có thêm một chỗ hiển thị cũng là thêm một
                    chỗ có thể lộ khoá.
                  </p>

                  <a href="/#config" className="inline-block mt-4">
                    <Button variant="cyber">
                      Mở màn hình Cấu hình trong portal
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </Button>
                  </a>

                  <div className="mt-5 p-4 rounded-lg bg-slate-900/60 border border-slate-800">
                    <p className="text-sm text-cyan-300 font-medium flex items-center gap-2 mb-2">
                      <ShieldCheck className="w-4 h-4" />
                      Ai được sửa cấu hình
                    </p>
                    <p className="text-sm text-slate-400 leading-relaxed">
                      Cả đọc lẫn ghi cấu hình đều giới hạn cho vai trò{' '}
                      <code className="text-cyan-400">manager</code> và{' '}
                      <code className="text-cyan-400">admin</code>. Vai trò{' '}
                      <code className="text-cyan-400">viewer</code> không đọc được.
                    </p>
                  </div>

                  <p className="text-xs text-slate-500 mt-4 flex items-start gap-1.5">
                    <Info className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    Lưu ý bảo mật: <code className="text-cyan-400">GET /api/v1/config</code>{' '}
                    hiện trả khoá API và bot token ở dạng chữ thường cho mọi tài
                    khoản manager/admin. Đây là hành vi có sẵn từ trước, được
                    chủ đích để sửa khoá trong portal — nhưng nếu cần siết thì
                    nên che khoá và yêu cầu nhập lại khi muốn đổi.
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

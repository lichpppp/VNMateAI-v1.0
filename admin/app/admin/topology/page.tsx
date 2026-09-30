'use client';

import React from 'react';
import dynamic from 'next/dynamic';
import { Loader2 } from 'lucide-react';

// Next.js Lazy Loading with ssr: false
const SystemCanvas = dynamic(() => import('@/components/topology/SystemCanvas'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-screen bg-[#01060e] flex flex-col items-center justify-center border-0 text-cyan-400">
      <Loader2 className="w-10 h-10 animate-spin text-cyan-400 mb-4" />
      <span className="font-orbitron font-bold text-sm tracking-wider text-slate-300">
        KHỞI TẠO TOPOLOGY CANVAS...
      </span>
      <span className="text-xs font-mono text-slate-500 mt-1">
        Đang nạp các khối kiến trúc và luồng dữ liệu thời gian thực
      </span>
    </div>
  ),
});

export default function TopologyPage() {
  return (
    <div className="w-full h-screen -m-6 lg:-m-8 p-0 overflow-hidden bg-[#01060e]">
      {/* Full-screen Lazy-loaded React Flow Canvas */}
      <SystemCanvas />
    </div>
  );
}

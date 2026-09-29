'use client';

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/Button';
import { 
  LayoutDashboard, 
  PlugZap, 
  GitBranch, 
  Settings, 
  Users, 
  Shield, 
  LogOut, 
  ChevronLeft, 
  Menu, 
  Bell, 
  HelpCircle,
  Server,
  Sun
} from 'lucide-react';

const navigation = [
  { name: 'Dashboard', href: '/admin/dashboard', icon: LayoutDashboard },
  { name: 'Plugins', href: '/admin/plugins', icon: PlugZap },
  { name: 'Routing', href: '/admin/routing', icon: GitBranch },
  { name: 'Workers', href: '/admin/workers', icon: Server },
  { name: 'Settings', href: '/admin/settings', icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = React.useState(false);

  return (
    <aside
      className={cn(
        'fixed left-0 top-0 z-40 h-screen bg-vnmate-slate-900/95 backdrop-blur-xl border-r border-vnmate-slate-800 transition-all duration-300',
        collapsed ? 'w-20' : 'w-64'
      )}
    >
      <div className="flex flex-col h-full">
        {/* Logo */}
        <div className={cn('flex items-center justify-between h-16 px-4 border-b border-vnmate-slate-800', collapsed && 'justify-center')}>
          <Link href="/admin/dashboard" className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-vnmate-cyan to-vnmate-blue flex items-center justify-center">
              <Shield className="h-6 w-6 text-vnmate-darker" />
            </div>
            {!collapsed && (
              <span className="font-orbitron font-bold text-xl text-vnmate-cyan">VN-MateAI</span>
            )}
          </Link>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setCollapsed(!collapsed)}
            className="text-vnmate-slate-400 hover:text-vnmate-neon"
          >
            {collapsed ? <ChevronLeft className="h-5 w-5 rotate-180" /> : <Menu className="h-5 w-5" />}
          </Button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-4 space-y-1 overflow-y-auto" role="navigation" aria-label="Main navigation">
          {navigation.map((item) => {
            const isActive = pathname === item.href || (item.href !== '/admin/dashboard' && pathname.startsWith(item.href));
            return (
              <Link
                key={item.name}
                href={item.href}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200',
                  isActive
                    ? 'bg-vnmate-cyan/10 text-vnmate-cyan border border-vnmate-cyan/30 shadow-glow-cyan'
                    : 'text-vnmate-slate-400 hover:text-vnmate-neon hover:bg-vnmate-slate-800/50',
                  collapsed && 'justify-center'
                )}
                title={collapsed ? item.name : undefined}
              >
                <item.icon className="h-5 w-5 flex-shrink-0" aria-hidden="true" />
                {!collapsed && <span className="font-medium">{item.name}</span>}
              </Link>
            );
          })}
        </nav>

        {/* Bottom section */}
        <div className={cn('p-4 border-t border-vnmate-slate-800', collapsed && 'hidden')}>
          <div className="space-y-2">
            <Link href="/admin/profile" className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-vnmate-slate-400 hover:text-vnmate-neon hover:bg-vnmate-slate-800/50 transition-all">
              <Users className="h-5 w-5" />
              <span>Profile</span>
            </Link>
            <Link href="/admin/security" className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-vnmate-slate-400 hover:text-vnmate-neon hover:bg-vnmate-slate-800/50 transition-all">
              <Shield className="h-5 w-5" />
              <span>Security</span>
            </Link>
            <Link href="/admin/help" className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-vnmate-slate-400 hover:text-vnmate-neon hover:bg-vnmate-slate-800/50 transition-all">
              <HelpCircle className="h-5 w-5" />
              <span>Help & Docs</span>
            </Link>
          </div>
          <div className="mt-4 pt-4 border-t border-vnmate-slate-800">
            <Button variant="outline" className="w-full justify-start gap-2 text-vnmate-pink hover:text-vnmate-pink border-vnmate-pink/30 hover:border-vnmate-pink/50" onClick={() => { /* logout */ }}>
              <LogOut className="h-4 w-4" />
              <span>Sign Out</span>
            </Button>
          </div>
        </div>
      </div>
    </aside>
  );
}

export function TopBar({ onMenuClick }: { onMenuClick?: () => void }) {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-30 h-16 bg-vnmate-slate-900/80 backdrop-blur-xl border-b border-vnmate-slate-800">
      <div className="flex items-center justify-between h-full px-6">
        <div className="flex items-center gap-4">
          {onMenuClick && (
            <Button variant="ghost" size="icon" onClick={onMenuClick} className="md:hidden">
              <Menu className="h-5 w-5" />
            </Button>
          )}
          <h1 className="text-xl font-orbitron font-bold text-vnmate-cyan hidden md:block">
            {pathname.split('/').pop()?.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase()) || 'Dashboard'}
          </h1>
        </div>

        <div className="flex items-center gap-4">
          {/* Notifications */}
          <Button variant="ghost" size="icon" className="relative">
            <Bell className="h-5 w-5" />
            <span className="absolute -top-1 -right-1 w-4 h-4 bg-vnmate-pink text-xs font-bold rounded-full flex items-center justify-center">
              3
            </span>
          </Button>

          {/* Theme toggle */}
          <Button variant="ghost" size="icon">
            <Sun className="h-5 w-5" />
          </Button>

          {/* User menu */}
          <div className="flex items-center gap-3 pl-4 border-l border-vnmate-slate-800">
            <div className="w-8 h-8 rounded-full bg-vnmate-cyan/20 flex items-center justify-center text-vnmate-cyan font-semibold">
              A
            </div>
            <div className="hidden md:block text-left">
              <p className="text-sm font-medium text-vnmate-neon">Admin User</p>
              <p className="text-xs text-vnmate-slate-500">Administrator</p>
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}


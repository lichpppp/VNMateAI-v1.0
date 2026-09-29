'use client';

import React from 'react';
import { Sidebar, TopBar } from '@/components/admin/Layout';
import { Toaster } from '@/components/ui/Toast';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Switch } from '@/components/ui/Switch';
import { Label } from '@/components/ui/Label';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/Tabs';
import { Save, Shield, Bell, Database, Globe, Key, Plus, Copy } from 'lucide-react';

export default function AdminSettings() {
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  return (
    <div className="min-h-screen bg-vnmate-dark cyber-grid">
      <Sidebar />
      <div className="lg:pl-64">
        <TopBar onMenuClick={() => setSidebarOpen(true)} />
        <main className="p-6 lg:p-8 space-y-6">
          <div>
            <h1 className="text-3xl font-orbitron font-bold text-vnmate-cyan">Settings</h1>
            <p className="text-vnmate-slate-400 mt-1">Configure system preferences and integrations</p>
          </div>

          <Tabs defaultValue="general" className="w-full">
            <TabsList className="grid w-full grid-cols-5">
              <TabsTrigger value="general"><Globe className="mr-2 h-4 w-4" /> General</TabsTrigger>
              <TabsTrigger value="security"><Shield className="mr-2 h-4 w-4" /> Security</TabsTrigger>
              <TabsTrigger value="notifications"><Bell className="mr-2 h-4 w-4" /> Notifications</TabsTrigger>
              <TabsTrigger value="database"><Database className="mr-2 h-4 w-4" /> Database</TabsTrigger>
              <TabsTrigger value="api"><Key className="mr-2 h-4 w-4" /> API Keys</TabsTrigger>
            </TabsList>

            <TabsContent value="general" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Globe className="h-5 w-5" /> General Settings</CardTitle>
                  <CardDescription>Basic application configuration</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <Label htmlFor="app-name">Application Name</Label>
                      <Input id="app-name" defaultValue="VN-MateAI" />
                    </div>
                    <div>
                      <Label htmlFor="app-url">Application URL</Label>
                      <Input id="app-url" type="url" defaultValue="https://vnmateai.example.com" />
                    </div>
                    <div>
                      <Label htmlFor="timezone">Timezone</Label>
                      <Input id="timezone" defaultValue="Asia/Ho_Chi_Minh" />
                    </div>
                    <div>
                      <Label htmlFor="language">Default Language</Label>
                      <Input id="language" defaultValue="vi-VN" />
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Maintenance Mode</Label>
                      <p className="text-sm text-vnmate-slate-400">Put the application in maintenance mode</p>
                    </div>
                    <Switch defaultChecked={false} />
                  </div>
                  <Button variant="cyber"><Save className="mr-2 h-4 w-4" /> Save Changes</Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="security" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Shield className="h-5 w-5" /> Security Settings</CardTitle>
                  <CardDescription>Authentication and access control</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <Label htmlFor="session-timeout">Session Timeout (minutes)</Label>
                      <Input id="session-timeout" type="number" defaultValue="60" />
                    </div>
                    <div>
                      <Label htmlFor="max-login-attempts">Max Login Attempts</Label>
                      <Input id="max-login-attempts" type="number" defaultValue="5" />
                    </div>
                  </div>
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>Two-Factor Authentication</Label>
                        <p className="text-sm text-vnmate-slate-400">Require 2FA for all admin users</p>
                      </div>
                      <Switch defaultChecked={true} />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>IP Whitelist Enforcement</Label>
                        <p className="text-sm text-vnmate-slate-400">Restrict admin access to whitelisted IPs</p>
                      </div>
                      <Switch defaultChecked={false} />
                    </div>
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>Audit Logging</Label>
                        <p className="text-sm text-vnmate-slate-400">Log all admin actions for compliance</p>
                      </div>
                      <Switch defaultChecked={true} />
                    </div>
                  </div>
                  <Button variant="cyber"><Save className="mr-2 h-4 w-4" /> Save Security Settings</Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="notifications" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Bell className="h-5 w-5" /> Notifications</CardTitle>
                  <CardDescription>Configure notification channels and preferences</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="space-y-4">
                    {['Email', 'Telegram', 'Slack', 'Webhook', 'SMS'].map((channel) => (
                      <div key={channel} className="flex items-center justify-between p-4 bg-vnmate-slate-800/50 rounded-lg border border-vnmate-slate-700">
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-lg bg-vnmate-cyan/10 flex items-center justify-center text-vnmate-cyan">
                            <Bell className="h-5 w-5" />
                          </div>
                          <div>
                            <p className="font-medium text-vnmate-neon">{channel} Notifications</p>
                            <p className="text-sm text-vnmate-slate-400">Configure {channel.toLowerCase()} delivery settings</p>
                          </div>
                        </div>
                        <Switch defaultChecked={channel === 'Email' || channel === 'Telegram'} />
                      </div>
                    ))}
                  </div>
                  <Button variant="cyber"><Save className="mr-2 h-4 w-4" /> Save Notification Settings</Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="database" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Database className="h-5 w-5" /> Database</CardTitle>
                  <CardDescription>Database connection and maintenance</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div>
                      <Label htmlFor="db-host">Database Host</Label>
                      <Input id="db-host" defaultValue="localhost" />
                    </div>
                    <div>
                      <Label htmlFor="db-port">Port</Label>
                      <Input id="db-port" type="number" defaultValue="5432" />
                    </div>
                    <div>
                      <Label htmlFor="db-name">Database Name</Label>
                      <Input id="db-name" defaultValue="vnmateai" />
                    </div>
                    <div>
                      <Label htmlFor="db-user">Username</Label>
                      <Input id="db-user" defaultValue="postgres" />
                    </div>
                  </div>
                  <div>
                    <Label htmlFor="db-password">Password</Label>
                    <Input id="db-password" type="password" defaultValue="••••••••" />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Auto Backup</Label>
                      <p className="text-sm text-vnmate-slate-400">Enable automatic daily backups</p>
                    </div>
                    <Switch defaultChecked={true} />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <Label>Backup Retention (days)</Label>
                      <p className="text-sm text-vnmate-slate-400">How long to keep backups</p>
                    </div>
                    <Input id="backup-retention" type="number" defaultValue="30" className="w-32" />
                  </div>
                  <Button variant="cyber"><Save className="mr-2 h-4 w-4" /> Save Database Config</Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="api" className="space-y-6 mt-6">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2"><Key className="h-5 w-5" /> API Keys</CardTitle>
                  <CardDescription>Manage external API integrations</CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                  <div className="space-y-4">
                    {[
                      { name: 'OpenAI API', key: 'sk-••••••••••••••••', env: 'OPENAI_API_KEY' },
                      { name: 'Anthropic API', key: 'sk-ant-••••••••••••••••', env: 'ANTHROPIC_API_KEY' },
                      { name: 'Microsoft Graph', key: '••••••••••••••••••••', env: 'MS_GRAPH_CLIENT_SECRET' },
                      { name: 'AWS Access Key', key: 'AKIA••••••••••••••••', env: 'AWS_SECRET_ACCESS_KEY' },
                      { name: 'Telegram Bot Token', key: '••••••••••:••••••••••••••••••••••••', env: 'TELEGRAM_BOT_TOKEN' },
                    ].map((api) => (
                      <div key={api.name} className="flex items-center justify-between p-4 bg-vnmate-slate-800/50 rounded-lg border border-vnmate-slate-700">
                        <div>
                          <p className="font-medium text-vnmate-neon">{api.name}</p>
                          <p className="text-sm text-vnmate-slate-400 font-mono">{api.key}</p>
                          <p className="text-xs text-vnmate-slate-500">Env: {api.env}</p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button variant="outline" size="sm">Rotate</Button>
                          <Button variant="ghost" size="icon"><Copy className="h-4 w-4" /></Button>
                        </div>
                      </div>
                    ))}
                  </div>
                  <Button variant="cyber"><Plus className="mr-2 h-4 w-4" /> Add API Key</Button>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
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
// Phase 78: Admin được FastAPI phục vụ ở /admin — CÙNG origin với backend.
// Nên mặc định gọi API bằng đường dẫn TƯƠNG ĐỐI '/api/v1/...': không qua
// CORS, không cần cấu hình thêm. Đường dẫn tuyệt đối chỉ dùng khi
// NEXT_PUBLIC_API_BASE được đặt tường minh (tách Admin ra domain riêng).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || '/api/v1';

interface RequestOptions extends RequestInit {
  params?: Record<string, string>;
}

class ApiClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
    if (typeof window !== 'undefined') {
      // Đọc cả 2 khoá: portal (web/app.js) và admin dùng tên khác nhau.
      // Đọc chung token nên mở /admin không phải đăng nhập lại lần nữa.
      this.token =
        localStorage.getItem('vnmateai_token') ||
        localStorage.getItem('vnmate_token');
    }
  }

  setToken(token: string) {
    this.token = token;
    if (typeof window !== 'undefined') {
      localStorage.setItem('vnmateai_token', token);
      localStorage.setItem('vnmate_token', token);
    }
  }

  clearToken() {
    this.token = null;
    if (typeof window !== 'undefined') {
      localStorage.removeItem('vnmateai_token');
      localStorage.removeItem('vnmate_token');
    }
  }

  private async request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
    const { params, headers, ...restOptions } = options;

    // Mặc định: đường dẫn TƯƠNG ĐỐI trên chính origin — FastAPI phục vụ cả
    // /admin lẫn /api/v1 nên không qua CORS, không cần cấu hình thêm.
    // Chỉ khi NEXT_PUBLIC_API_BASE được đặt tường minh (tách domain ở
    // production) thì mới ghép thành URL tuyệt đối.
    const query = params ? '?' + new URLSearchParams(params).toString() : '';
    const path = this.baseUrl
      ? `${this.baseUrl}${endpoint}${query}`
      : `${endpoint}${query}`;

    const defaultHeaders: HeadersInit = {
      'Content-Type': 'application/json',
      ...(this.token && { Authorization: `Bearer ${this.token}` }),
      ...headers,
    };

    const response = await fetch(path, {
      ...restOptions,
      headers: defaultHeaders,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ message: 'Unknown error' }));
      throw new Error(error.message || `HTTP ${response.status}`);
    }

    if (response.status === 204) {
      return {} as T;
    }

    return response.json();
  }

  // Auth
  async login(username: string, password: string) {
    return this.request<{ access_token: string; token_type: string }>('/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    });
  }

  /**
   * Danh mục connector + JSON Schema cấu hình.
   *
   * Đây là nguồn DUY NHẤT cho trang Plugin Vault. Trước đây trang này gọi
   * `/plugins` — một endpoint không hề tồn tại — nên lưới luôn trống. Không tạo
   * danh sách plugin giả ở frontend: connector nào không có trong registry thì
   * không được hiện, vì giao diện không được hứa thứ hệ thống chưa có.
   */
  async getConnectorCatalog() {
    const res = await this.request<{
      status: string;
      connectors: Record<string, ConnectorCatalogEntry>;
    }>('/enterprise/connectors/catalog');
    return Object.values(res.connectors ?? {});
  }

  /** Trạng thái sống của connector (dùng để đồng bộ lại sau khi có thay đổi). */
  async getConnectorHealth() {
    const res = await this.request<{
      status: string;
      connectors: Record<string, { configured: boolean; enabled: boolean; missing_fields?: string[] }>;
    }>('/enterprise/connectors/health');
    return res.connectors ?? {};
  }

  // Routing Rules
  async getRoutingRules() {
    return this.request<RoutingRule[]>('/routing/rules');
  }

  async createRoutingRule(rule: Omit<RoutingRule, 'id' | 'createdAt' | 'updatedAt'>) {
    return this.request<RoutingRule>('/routing/rules', {
      method: 'POST',
      body: JSON.stringify(rule),
    });
  }

  async updateRoutingRule(id: string, rule: Partial<RoutingRule>) {
    return this.request<RoutingRule>(`/routing/rules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(rule),
    });
  }

  async deleteRoutingRule(id: string) {
    return this.request<void>(`/routing/rules/${id}`, {
      method: 'DELETE',
    });
  }

  // Dashboard
  async getDashboardStats() {
    return this.request<DashboardStats>('/dashboard/stats');
  }

  async getAuditLogs(limit = 50) {
    return this.request<AuditLog[]>(`/dashboard/audit-logs`, { params: { limit: String(limit) } });
  }

  async getPendingApprovals() {
    return this.request<ApprovalRequest[]>('/dashboard/approvals');
  }

  async approveRequest(id: string, approved: boolean, note?: string) {
    return this.request<ApprovalRequest>(`/dashboard/approvals/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ approved, note }),
    });
  }

  // Workers
  async getWorkers() {
    return this.request<Worker[]>('/workers');
  }
}

export const api = new ApiClient(API_BASE);

// Types
/**
 * Một connector lấy từ `GET /enterprise/connectors/catalog`.
 *
 * `configured: false` KHÔNG có nghĩa connector hỏng — nghĩa là chưa điền đủ
 * khoá bắt buộc. Giao diện phải hiện "chờ kết nối"/"chưa cấu hình", tuyệt đối
 * không vẽ trạng thái "Đã kết nối" cho thứ chưa từng kiểm tra.
 */
export interface ConnectorCatalogEntry {
  id: string;
  display_name: string;
  description: string;
  configured: boolean;
  missing_fields: string[];
  actions: string[];
  max_risk_level: number | null;
  config_schema: JsonSchema;
}

/** Trạng thái hiển thị của một connector, suy ra từ dữ liệu thật. */
export type ConnectorStatus = 'connected' | 'waiting' | 'error';

export function connectorStatus(entry: ConnectorCatalogEntry): ConnectorStatus {
  if (entry.configured) return 'connected';
  return 'waiting';
}

export interface JsonSchema {
  type: 'object';
  properties: Record<string, JsonSchemaProperty>;
  required?: string[];
}

export interface JsonSchemaProperty {
  type: 'string' | 'number' | 'boolean' | 'array' | 'object';
  title: string;
  description?: string;
  format?: 'password' | 'email' | 'uri' | 'secret';
  enum?: string[];
  default?: unknown;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
}

export interface RoutingRule {
  id: string;
  name: string;
  description: string;
  condition: RoutingCondition;
  action: RoutingAction;
  priority: number;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface RoutingCondition {
  type: 'task_type' | 'risk_level' | 'user_role' | 'time_range' | 'custom';
  field: string;
  operator: 'equals' | 'contains' | 'greater_than' | 'less_than' | 'in' | 'regex';
  value: unknown;
}

export interface RoutingAction {
  type: 'm365_direct' | 'legacy_bot' | 'esp32_voice' | 'webhook' | 'email';
  target: string;
  config: Record<string, unknown>;
}

export interface DashboardStats {
  workers: {
    total: number;
    online: number;
    offline: number;
  };
  plugins: {
    total: number;
    connected: number;
    disconnected: number;
  };
  tasks: {
    totalToday: number;
    completed: number;
    failed: number;
    pending: number;
  };
  approvals: {
    pending: number;
    approved: number;
    rejected: number;
  };
  system: {
    cpu: number;
    memory: number;
    uptime: string;
  };
}

export interface AuditLog {
  id: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error' | 'success';
  source: string;
  message: string;
  metadata?: Record<string, unknown>;
}

export interface ApprovalRequest {
  id: string;
  timestamp: string;
  taskType: string;
  riskLevel: 1 | 2 | 3 | 4 | 5;
  description: string;
  requestedBy: string;
  status: 'pending' | 'approved' | 'rejected';
  approvedBy?: string;
  approvedAt?: string;
  note?: string;
}

export interface Worker {
  id: string;
  name: string;
  type: 'mac-mini' | 'ubuntu' | 'esp32' | 'custom';
  status: 'online' | 'offline' | 'busy' | 'error';
  ip: string;
  lastSeen: string;
  capabilities: string[];
  currentTask?: string;
  specs: {
    cpu: string;
    memory: string;
    gpu?: string;
  };
}
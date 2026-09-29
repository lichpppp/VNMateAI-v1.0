'use client';

import { useState, useEffect, useCallback } from 'react';
import {
  api,
  type ConnectorCatalogEntry,
  type ConnectorStatus,
  connectorStatus,
} from '@/lib/api';
import { useToast } from '@/hooks/useToast';

/**
 * Nguồn duy nhất cho trang Plugin Vault: danh mục connector thật từ backend.
 *
 * Trước đây hook này gọi `/plugins` — endpoint không tồn tại — nên mọi thứ trả
 * về rỗng và trang hiện lưới trống. Không được thay bằng danh sách viết tay
 * trong frontend: connector nào không có trong registry thì hệ thống không có,
 * và hiện nó ra là hứa một thứ không tồn tại.
 *
 * "Chưa cấu hình" KHÔNG phải "lỗi": connector thiếu khoá thì hiện "chờ kết nối",
 * đúng quy ước đã dùng ở Phase 73-76.
 */
export function usePlugins() {
  const [plugins, setPlugins] = useState<ConnectorCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { success: toastSuccess, error: toastError } = useToast();

  const fetchPlugins = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.getConnectorCatalog();
      setPlugins(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to fetch connectors');
      setPlugins([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPlugins();
  }, [fetchPlugins]);

  /**
   * Thử kết nối. Hiện chưa có endpoint ping riêng, nên ta chỉ đọc lại trạng
   * thái cấu hình từ health endpoint và báo đúng mức độ sẵn sàng.
   *
   * Cố tình KHÔNG báo "thành công" khi chưa thật sự gọi ra ngoài: báo thành
   * công khi chỉ đọc lại cache là báo cáo thành công giả — đúng cái lỗi mà
   * Phase 59 đã sửa trong chính endpoint health.
   */
  const testConnection = async (pluginId: string): Promise<void> => {
    try {
      const health = await api.getConnectorHealth();
      const entry = health[pluginId];
      if (!entry) {
        toastError('Không tìm thấy connector', `${pluginId} không có trong registry`);
      } else if (entry.configured) {
        toastSuccess('Đã đủ thông tin đăng nhập', 'Chưa có lời gọi thật nào được gửi đi bởi thao tác này');
      } else {
        const missing = entry.missing_fields ?? [];
        toastError(
          'Chưa cấu hình xong',
          missing.length ? `Còn thiếu: ${missing.join(', ')}` : 'Thiếu thông tin đăng nhập',
        );
      }
      await fetchPlugins();
    } catch (err) {
      toastError('Không kiểm tra được', err instanceof Error ? err.message : 'Unknown error');
    }
  };

  return {
    plugins,
    loading,
    error,
    refetch: fetchPlugins,
    testConnection,
  };
}

export type { ConnectorCatalogEntry, ConnectorStatus };
export { connectorStatus };

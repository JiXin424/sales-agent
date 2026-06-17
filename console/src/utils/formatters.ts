/** Formatters for display. */

import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

export function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return '-';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n == null) return '-';
  return n.toLocaleString('zh-CN');
}

export function formatPercentage(value: number | null | undefined): string {
  if (value == null) return '-';
  return `${(value * 100).toFixed(1)}%`;
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-';
  return dayjs(iso).format('YYYY-MM-DD HH:mm');
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '-';
  return dayjs(iso).fromNow();
}

export function truncate(str: string | null | undefined, maxLen: number = 50): string {
  if (!str) return '-';
  return str.length > maxLen ? str.slice(0, maxLen) + '...' : str;
}

/** Feedback API wrappers. */

import { apiGet, apiPatch } from './client';
import type { PaginatedResponse, FeedbackItem, FeedbackFilters, ReviewStatus } from './types';

function base(tid: string) {
  return `/tenants/${tid}/feedback`;
}

export function listFeedback(tenantId: string, filters?: FeedbackFilters) {
  return apiGet<PaginatedResponse<FeedbackItem>>(
    base(tenantId),
    filters as Record<string, string | number | undefined>,
  );
}

export function getFeedbackDetail(tenantId: string, feedbackId: string) {
  return apiGet<FeedbackItem>(`${base(tenantId)}/${feedbackId}`);
}

export function updateFeedbackReviewStatus(
  tenantId: string,
  feedbackId: string,
  reviewStatus: ReviewStatus,
) {
  return apiPatch<FeedbackItem>(`${base(tenantId)}/${feedbackId}/review`, {
    review_status: reviewStatus,
  });
}

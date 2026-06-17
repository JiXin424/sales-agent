-- Phase C: Add review_status column to feedbacks table
-- Allows tracking feedback triage status: open / reviewed / ignored

ALTER TABLE feedbacks ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'open';

COMMENT ON COLUMN feedbacks.review_status IS 'Feedback triage status: open (pending review), reviewed (actioned), ignored (dismissed)';

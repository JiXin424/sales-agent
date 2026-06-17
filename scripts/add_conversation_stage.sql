-- Phase B: Add sales stage column to conversations table
-- This column stores the canonical sales stage from user request context.

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS stage TEXT;

-- Add comment for documentation
COMMENT ON COLUMN conversations.stage IS 'Sales stage from request context (e.g. lead_discovery, visit_preparation, deal_closing). Populated from context.stage field.';

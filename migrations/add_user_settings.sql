-- User settings table for persisting export and notification preferences
CREATE TABLE IF NOT EXISTS user_settings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  -- Export preferences
  export_format TEXT NOT NULL DEFAULT 'csv',
  export_date_format TEXT NOT NULL DEFAULT 'ISO',
  export_include_metadata BOOLEAN NOT NULL DEFAULT true,
  export_include_confidence BOOLEAN NOT NULL DEFAULT true,
  -- Notification preferences
  notify_email BOOLEAN NOT NULL DEFAULT true,
  notify_browser BOOLEAN NOT NULL DEFAULT true,
  notify_extraction_completed BOOLEAN NOT NULL DEFAULT true,
  notify_extraction_failed BOOLEAN NOT NULL DEFAULT true,
  notify_code_generation BOOLEAN NOT NULL DEFAULT true,
  -- Timestamps
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id)
);

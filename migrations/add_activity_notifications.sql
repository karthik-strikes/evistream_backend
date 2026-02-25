-- Migration: Add Activity Feed and Notifications tables
-- Date: 2026-02-11

-- ============================================================================
-- Activity Feed Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    action_type VARCHAR(50) NOT NULL,  -- upload, extraction, export, code_generation, form_create, project_create
    action VARCHAR(255) NOT NULL,
    description TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    status VARCHAR(20),  -- success, failed, pending
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Indexes for common queries
    INDEX idx_activities_user (user_id, created_at DESC),
    INDEX idx_activities_project (project_id, created_at DESC),
    INDEX idx_activities_action_type (action_type, created_at DESC),
    INDEX idx_activities_created_at (created_at DESC)
);

COMMENT ON TABLE activities IS 'User activity feed tracking all actions across the system';
COMMENT ON COLUMN activities.action_type IS 'Type of action performed: upload, extraction, export, code_generation, form_create, project_create';
COMMENT ON COLUMN activities.status IS 'Status of the action: success, failed, pending, null';
COMMENT ON COLUMN activities.metadata IS 'Additional contextual data in JSON format';

-- ============================================================================
-- Notifications Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL,  -- success, error, info, warning
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    read BOOLEAN DEFAULT FALSE,
    action_label VARCHAR(100),
    action_url VARCHAR(500),
    related_entity_type VARCHAR(50),  -- job, extraction, document, form, project
    related_entity_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP WITH TIME ZONE,

    -- Indexes for common queries
    INDEX idx_notifications_user_unread (user_id, read, created_at DESC),
    INDEX idx_notifications_user_created (user_id, created_at DESC),
    INDEX idx_notifications_entity (related_entity_type, related_entity_id)
);

COMMENT ON TABLE notifications IS 'User notifications for system events and alerts';
COMMENT ON COLUMN notifications.type IS 'Notification type: success, error, info, warning';
COMMENT ON COLUMN notifications.read IS 'Whether the notification has been read by the user';
COMMENT ON COLUMN notifications.action_label IS 'Label for action button (e.g., "View Results")';
COMMENT ON COLUMN notifications.action_url IS 'URL to navigate when action is clicked';
COMMENT ON COLUMN notifications.related_entity_type IS 'Type of entity this notification relates to';
COMMENT ON COLUMN notifications.related_entity_id IS 'ID of the related entity';

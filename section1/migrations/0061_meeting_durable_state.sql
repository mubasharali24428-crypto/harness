-- 0061: durable Meeting Area state (Phase 7 remediation).
--
-- Meetings remain tenant-scoped through meetings.project_id -> projects.team_id;
-- no team_id is duplicated onto the frozen meeting tables.  The additive
-- columns preserve lifecycle/brief state, while the two new tables persist
-- artifacts that had previously lived only in Section 6 process memory.

ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE meetings
    ADD COLUMN IF NOT EXISTS brief_id VARCHAR(36);

ALTER TABLE meeting_messages
    ADD COLUMN IF NOT EXISTS sender_id VARCHAR(36);
ALTER TABLE meeting_messages
    ADD COLUMN IF NOT EXISTS is_ai_generated BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS meeting_summaries (
    id VARCHAR(36) NOT NULL,
    meeting_id VARCHAR(36) NOT NULL,
    content TEXT NOT NULL,
    format VARCHAR(20) NOT NULL DEFAULT 'markdown',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_meeting_summaries_meeting_id FOREIGN KEY(meeting_id)
        REFERENCES meetings (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_meeting_summaries_meeting_id
    ON meeting_summaries (meeting_id, created_at);

CREATE TABLE IF NOT EXISTS meeting_prompts (
    id VARCHAR(36) NOT NULL,
    meeting_id VARCHAR(36) NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    linked_task_id VARCHAR(36),
    linked_branch VARCHAR(300),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_meeting_prompts_meeting_id FOREIGN KEY(meeting_id)
        REFERENCES meetings (id) ON DELETE CASCADE,
    CONSTRAINT uq_meeting_prompts_version UNIQUE (meeting_id, version)
);
CREATE INDEX IF NOT EXISTS ix_meeting_prompts_meeting_id
    ON meeting_prompts (meeting_id, version);

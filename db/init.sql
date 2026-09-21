-- Message queue schema (initialized when the container's data directory is empty).

CREATE TYPE message_status AS ENUM ('PENDING', 'SENT', 'FAILED');

CREATE TABLE message_queue (
    id SERIAL PRIMARY KEY,
    moodle_user_id VARCHAR(50) NOT NULL,
    conversation_id VARCHAR(50),
    content TEXT NOT NULL,
    status message_status DEFAULT 'PENDING',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP WITH TIME ZONE,
    error_log TEXT
);

-- Partial index for pending-message lookups.
CREATE INDEX idx_message_status ON message_queue(status) WHERE status = 'PENDING';

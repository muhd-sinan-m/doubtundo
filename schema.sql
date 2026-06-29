-- ============================================================
-- doubtundo.app — Supabase PostgreSQL Schema
-- Run this in the Supabase SQL Editor for your project
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    padikku_user_id VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    nickname VARCHAR(50) UNIQUE,
    nickname_changed_at TIMESTAMP WITH TIME ZONE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_padikku_user_id ON users(padikku_user_id);
CREATE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname);

-- ============================================================
-- DOUBTS
-- ============================================================
CREATE TABLE IF NOT EXISTS doubts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    description TEXT,
    subject VARCHAR(100),
    semester INTEGER CHECK (semester BETWEEN 1 AND 6),
    tags TEXT[] DEFAULT '{}',
    is_anonymous BOOLEAN DEFAULT FALSE,
    is_resolved BOOLEAN DEFAULT FALSE,
    upvotes INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doubts_user_id ON doubts(user_id);
CREATE INDEX IF NOT EXISTS idx_doubts_subject ON doubts(subject);
CREATE INDEX IF NOT EXISTS idx_doubts_semester ON doubts(semester);
CREATE INDEX IF NOT EXISTS idx_doubts_created_at ON doubts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doubts_upvotes ON doubts(upvotes DESC);

-- ============================================================
-- REPLIES
-- ============================================================
CREATE TABLE IF NOT EXISTS replies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doubt_id UUID NOT NULL REFERENCES doubts(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    is_admin_answer BOOLEAN DEFAULT FALSE,
    is_helpful BOOLEAN DEFAULT FALSE,
    upvotes INTEGER DEFAULT 0,
    is_hidden BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_replies_doubt_id ON replies(doubt_id);
CREATE INDEX IF NOT EXISTS idx_replies_user_id ON replies(user_id);
CREATE INDEX IF NOT EXISTS idx_replies_is_admin_answer ON replies(is_admin_answer);

-- Only one admin answer per doubt
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_admin_answer_per_doubt
    ON replies(doubt_id) WHERE is_admin_answer = TRUE;

-- ============================================================
-- UPVOTES
-- ============================================================
CREATE TABLE IF NOT EXISTS upvotes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_id UUID NOT NULL,
    target_type VARCHAR(10) NOT NULL CHECK (target_type IN ('doubt', 'reply')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (user_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_upvotes_target_id ON upvotes(target_id);
CREATE INDEX IF NOT EXISTS idx_upvotes_user_id ON upvotes(user_id);

-- ============================================================
-- TRIGGER: auto-update doubts.updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_doubts_updated_at ON doubts;
CREATE TRIGGER update_doubts_updated_at
    BEFORE UPDATE ON doubts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- VIEWS: Doubt list with reply count (handy for feed)
-- ============================================================
CREATE OR REPLACE VIEW doubts_with_counts AS
SELECT
    d.*,
    u.nickname,
    u.email,
    COUNT(DISTINCT r.id) AS reply_count,
    COUNT(DISTINCT CASE WHEN r.is_admin_answer THEN r.id END) AS has_admin_answer
FROM doubts d
LEFT JOIN users u ON d.user_id = u.id
LEFT JOIN replies r ON d.id = r.doubt_id AND r.is_hidden = FALSE
GROUP BY d.id, u.nickname, u.email;

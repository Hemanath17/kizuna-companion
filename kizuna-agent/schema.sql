CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    name TEXT,
    last_summarized_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    role TEXT NOT NULL,               -- 'user' | 'assistant'
    content TEXT NOT NULL,
    safety_flag BOOLEAN DEFAULT false,
    emotion_label TEXT,
    emotion_score FLOAT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE journal_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    mood INT, 
    source TEXT NOT NULL DEFAULT 'generated_summary',
    dominant_emotion TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE relationship_facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    fact_text TEXT NOT NULL,
    source_message_id UUID REFERENCES messages(id),
    last_referenced_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_messages_user_id ON messages(user_id, created_at DESC);
CREATE INDEX idx_journal_user_id ON journal_entries(user_id, created_at DESC);
CREATE INDEX idx_facts_user_id ON relationship_facts(user_id, last_referenced_at DESC);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE journal_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE relationship_facts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users manage their own profile" ON profiles
    FOR ALL USING (auth.uid() = id);

CREATE POLICY "Users manage their own messages" ON messages
    FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users manage their own journal entries" ON journal_entries
    FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users manage their own relationship facts" ON relationship_facts
    FOR ALL USING (auth.uid() = user_id);


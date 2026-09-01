import psycopg2
from config import DATABASE_URL
from psycopg2.extras import RealDictCursor

def _connect():
    return psycopg2.connect(DATABASE_URL)

def get_or_create_profile(user_id: str, email: str, name: str = None) -> dict:
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO profiles (id, email, name)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email
                   RETURNING id, email, name, last_summarized_at""",
                (user_id, email, name),
            )
            profile = cur.fetchone()
        conn.commit()
    return profile

def get_last_summarized_at(user_id: str):
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT last_summarized_at FROM profiles WHERE id = %s""",
                (user_id,),
            )
            row = cur.fetchone()
    return row["last_summarized_at"] if row else None

def update_last_summarized_at(user_id: str, timestamp: datetime) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE profiles SET last_summarized_at = %s WHERE id = %s",
                (timestamp, user_id),
            )
        conn.commit()

def get_unsummarized_user_messages(user_id: str, since) -> list[dict]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT content, emotion_label, created_at FROM messages
                   WHERE user_id = %s AND role = 'user' AND created_at > %s
                   ORDER BY created_at""",
                (user_id, since),
            )
            rows = cur.fetchall()
    return rows

def save_message(user_id: str, role: str, content: str,
                  safety_flag: bool, emotion_label: str = None, emotion_score: float = None):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO messages (user_id, role, content, safety_flag, emotion_label, emotion_score)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (user_id, role, content, safety_flag, emotion_label, emotion_score),
            )
        conn.commit()
    
def save_journal_entry(user_id: str, text: str, mood: int = None,
                        source: str = "generated_summary", dominant_emotion: str = None):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO journal_entries (user_id, text, mood, source, dominant_emotion)
                   VALUES (%s, %s, %s, %s, %s)""",
                (user_id, text, mood, source, dominant_emotion),
            )
        conn.commit()
    
def insert_relationship_fact(user_id: str, fact_text: str, source_message_id: str = None):
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO relationship_facts (user_id, fact_text, source_message_id, last_referenced_at)
                   VALUES (%s, %s, %s, now())""",
                (user_id, fact_text, source_message_id),
            )
        conn.commit()


def get_relevant_facts(user_id: str, limit: int = 10) -> list[str]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT fact_text FROM relationship_facts
                   WHERE user_id = %s
                   ORDER BY last_referenced_at DESC
                   LIMIT %s""",
                (user_id, limit),
            )
            rows = cur.fetchall()
    return [r["fact_text"] for r in rows]


def get_recent_messages(user_id: str, limit: int = 12) -> list[dict]:
    with _connect() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT role, content FROM messages
                   WHERE user_id = %s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (user_id, limit),
            )
            rows = cur.fetchall()
    return list(reversed(rows))
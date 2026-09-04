import os
from pathlib import Path

from dotenv import load_dotenv

# Project-root .env (parent of kizuna-agent/), even when cwd is kizuna-agent
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MAIN_MODEL = "openai/gpt-oss-120b"
GROQ_FAST_MODEL = "openai/gpt-oss-20b" 

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
QDRANT_COLLECTION = "kizuna_reference_corpus"

EMBEDDING_MODEL = "BAAI/bge-m3"
SAFETY_MODEL = "Akashpaul123/bert-suicide-detection"
SAFETY_THRESHOLD_CRISIS = 0.5
SAFETY_THRESHOLD_UNCERTAIN = 0.25
DATABASE_URL = os.environ.get("DATABASE_URL") 
EMOTION_MODEL = "SamLowe/roberta-base-go_emotions"
RECENT_MESSAGE_WINDOW = 12  

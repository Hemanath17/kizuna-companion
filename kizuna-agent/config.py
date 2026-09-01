import os

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MAIN_MODEL = "llama-3.3-70b-versatile"  
GROQ_FAST_MODEL = "llama-3.1-8b-instant"   

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

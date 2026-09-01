from typing import TypedDict, Annotated, Literal, Optional
from langgraph.graph.message import add_messages

class KizunaState(TypedDict):
    messages: Annotated[list, add_messages]
    # mode: Literal["chat","journal"]
    user_id: str
    safety_score: Optional[float]
    safety_label: Optional[str]
    standalone_query: Optional[str]
    needs_retrieval: Optional[bool]
    retrieved_context: Optional[list[str]]
    relationship_facts: Optional[list[str]]
    detected_emotion_hint: Optional[str]
    detected_emotion_label: Optional[str] 
    detected_emotion_score: Optional[float] 
    final_response: Optional[str]

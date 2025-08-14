from typing import Any, Dict, Optional, TypedDict, Annotated

from langgraph.graph import add_messages


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    facts: Dict[str, Any]
    brief: Optional[str]
    last_ui_state: Optional[Dict[str, Any]]
    run_id: str
    thread: Dict[str, Optional[str]]
    scratch: Dict[str, Any]
    step_count: int



from pydantic import BaseModel



class AgentRequest(BaseModel):
    user_id: str
    session_id: str
    request: str


class AgentResponse(BaseModel):
    user_id: str
    session_id: str
    response: str

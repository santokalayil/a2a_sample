from pydantic import BaseModel


class FrontendRequest(BaseModel):
    user_id: str
    session_id: str
    request: str


class BackendResponse(BaseModel):
    user_id: str
    session_id: str
    response: str

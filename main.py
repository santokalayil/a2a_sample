from fastapi import FastAPI

from models import FrontendRequest, BackendResponse

import a2a_orchestrator as ao

app = FastAPI()


@app.post("/process")
async def process_frondend_request(request: FrontendRequest):
    # send to a2a organizaer agent by creating a2a client logic

    # ao.errors

    return BackendResponse(
        user_id=request.user_id,
        session_id=request.session_id,
        response=f"Response to '{request.request}'"
    )

# fastapi dev main.py
# http://127.0.0.1:8000/docs
# http://127.0.0.1:8000/process


from . import HOST, PORT




from . import fastapi_app

if __name__ == "__main__":
    print(f"Hi: {HOST}:{PORT}")
    import uvicorn
    uvicorn.run(fastapi_app, host=HOST, port=PORT)

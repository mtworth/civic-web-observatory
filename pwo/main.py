import uvicorn
from .api import app


def serve(host="0.0.0.0", port=8000, reload=False):
    uvicorn.run("pwo.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    serve()

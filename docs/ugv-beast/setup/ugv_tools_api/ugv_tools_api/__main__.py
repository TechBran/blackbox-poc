import os, uvicorn
from .server import app

def main():
    host = os.environ.get("UGV_TOOLS_HOST", "0.0.0.0")
    port = int(os.environ.get("UGV_TOOLS_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info", workers=1)

if __name__ == "__main__":
    main()

"""Entry point for `python -m ugv_tools_api.supervisor`.

Runs uvicorn with the FastAPI app defined in service.py. Systemd
invokes this module path from start_supervisor.sh (Task 10).
"""
import os

import uvicorn


def main() -> None:
    host = os.environ.get("SUPERVISOR_HOST", "0.0.0.0")
    port = int(os.environ.get("SUPERVISOR_PORT", "8083"))
    # log_level is env-configurable so `SUPERVISOR_LOG_LEVEL=debug
    # systemctl restart ugv-supervisor` flips on verbose logging
    # without a code edit.
    log_level = os.environ.get("SUPERVISOR_LOG_LEVEL", "info")
    uvicorn.run(
        "ugv_tools_api.supervisor.service:app",
        host=host,
        port=port,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()

"""
Production / development entry point.
Run with: python run.py

Listens on all network interfaces (0.0.0.0) so the dashboard is
accessible from any valid IP address on this system.
"""
import os
import uvicorn

if __name__ == "__main__":
    # Use reload only if explicitly requested (dev mode).
    # reload=True causes issues with APScheduler (restarts kill scheduled jobs).
    dev_mode = os.environ.get("WATCHTOWER_DEV", "").lower() in ("1", "true", "yes")

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=dev_mode,
        reload_dirs=["backend"] if dev_mode else None,
    )

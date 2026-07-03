"""
Production / development entry point.
Run with: python run.py

Listens on all network interfaces (0.0.0.0) so the dashboard is
accessible from any valid IP address on this system.
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["backend"],
    )

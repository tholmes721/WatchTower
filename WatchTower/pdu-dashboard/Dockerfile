FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Data directory for SQLite (mount a volume here in production)
RUN mkdir -p /app/data

EXPOSE 8000

ENV DATABASE_URL=sqlite+aiosqlite:////app/data/pdu.db

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

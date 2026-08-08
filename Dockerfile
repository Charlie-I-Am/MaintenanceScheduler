FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (this is what changes/rebuilds on upgrade)
COPY app/ ./app/

ENV PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/maintenance.db

# /data is where the SQLite DB lives - mount this as a volume so it
# survives image rebuilds/upgrades.
VOLUME ["/data"]

EXPOSE 5000

CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "60", "--bind", "0.0.0.0:5000", "app.main:app"]

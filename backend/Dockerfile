# backend/Dockerfile
FROM python:3.11-slim

# Prevent .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && rm -rf /var/lib/apt/lists/*

# Copy dependency list first for layer caching
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend source
COPY . /app

# Default environment (Render overrides these)
ENV FLASK_ENV=production
ENV MONGO_URI=mongodb://mongo:27017/NFG

# Expose port for Render / local dev
EXPOSE 5000

# Start app with Gunicorn; run via shell so ${PORT} expands at runtime
# You can tweak WEB_CONCURRENCY / GUNICORN_THREADS via env vars.
CMD ["sh", "-lc", "gunicorn -w ${WEB_CONCURRENCY:-3} -k gthread --threads ${GUNICORN_THREADS:-2} -b 0.0.0.0:${PORT:-5000} app:app"]

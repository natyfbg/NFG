# backend/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY . /app

# Default envs (Render will override as needed)
ENV FLASK_ENV=production
ENV MONGO_URI=mongodb://mongo:27017/NFG

# Add our start script and make it executable
COPY starter.sh /app/starter.sh
RUN chmod +x /app/starter.sh


EXPOSE 8000

# Use the wrapper script via /bin/sh so local bind mounts do not require an executable bit
CMD ["/bin/sh", "/app/starter.sh"]

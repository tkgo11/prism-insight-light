FROM python:3.11-slim

WORKDIR /app

# Install basic dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies first for better cache layers
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose FastAPI port
EXPOSE 8000

# Note: Ensure /opt/prism-insight-data on host contains the project files
# and has proper permissions for the container to write data (trading.db, token.dat, etc.)

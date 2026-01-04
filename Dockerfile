FROM python:3.11-slim

LABEL maintainer="OrchestratedChaos"
LABEL description="Curatarr - Personalized recommendations for your Plex library"

# Set working directory
WORKDIR /app

# Install git for auto-update feature
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY recommenders/ recommenders/
COPY utils/ utils/
COPY run.sh .
COPY docker-entrypoint.sh .
RUN sed -i 's/\r$//' run.sh docker-entrypoint.sh && \
    chmod +x run.sh docker-entrypoint.sh

# Create directories for mounted volumes
RUN mkdir -p cache logs recommendations/external

# Entrypoint validates config, then runs the recommendation engine
ENTRYPOINT ["/bin/bash", "./docker-entrypoint.sh"]

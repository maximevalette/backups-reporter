FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    borgbackup \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY backup_reporter.py .

# Create non-root user
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app

# Switch to non-root user
USER app

# Set entrypoint
ENTRYPOINT ["python", "backup_reporter.py"]
CMD ["config.yaml"]
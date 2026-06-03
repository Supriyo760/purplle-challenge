FROM python:3.11-slim

WORKDIR /code

# Install required system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY ./app /code/app
COPY ./pipeline /code/pipeline

# Create a non-root user for security
RUN useradd -m appuser && chown -R appuser:appuser /code
USER appuser

EXPOSE 8000

# Start FastAPI server using Gunicorn with Uvicorn workers for production stability
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "--bind", "0.0.0.0:8000"]

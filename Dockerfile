# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PORT 8080

# Install uv for fast dependency management
RUN pip install uv

# Set work directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --no-dev

# Copy project
COPY . /app/

# Collect static files
RUN uv run python manage.py collectstatic --noinput

# Run gunicorn
CMD uv run gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0

FROM python:3.12-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create app directory
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files
COPY pyproject.toml uv.lock /app/

# Install dependencies using uv
RUN uv pip install --system -r pyproject.toml && \
    uv pip install --system gunicorn

# Copy application code
COPY . /app/

# Copy entrypoint script
COPY docker-entrypoint.sh /app/
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "{{GUNICORN_WORKERS}}", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "{{WSGI_MODULE}}"]

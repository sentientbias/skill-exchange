FROM python:3.12-slim

WORKDIR /app

# asyncpg needs a C compiler at build time; pynacl ships wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY api/ api/
COPY mcp_server/ mcp_server/

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

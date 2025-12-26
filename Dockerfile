FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY mjytdlp /app/mjytdlp
COPY README.md /app/README.md

# Use gthread so SSE + POST share the same in-memory session store.
CMD ["sh", "-c", "gunicorn mjytdlp.wsgi:app --bind 0.0.0.0:${PORT:-8000} --worker-class gthread --workers 1 --threads ${MJYTDLP_THREADS:-8}"]

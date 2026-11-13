FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_PATH=/data/respostas.db

RUN useradd --create-home --uid 1000 app && mkdir /data && chown app:app /data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py .

USER app
VOLUME /data
EXPOSE 8000

# UM processo com várias threads: as respostas agendadas ficam na memória deste processo.
CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 8 --timeout 60 --bind 0.0.0.0:${PORT:-8000} wsgi:app"]

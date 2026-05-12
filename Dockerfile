FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --no-create-home appuser \
    && mkdir -p /app/transcripts \
    && chown appuser /app/transcripts
USER appuser

EXPOSE 8000

CMD ["python", "server.py"]

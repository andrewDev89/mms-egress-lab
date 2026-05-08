FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mms_queue ./mms_queue

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "mms_queue.api:app", "--host", "0.0.0.0", "--port", "8000"]

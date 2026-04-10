FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends tzdata && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY subscriber.py .
COPY trading ./trading
COPY .env.example ./

STOPSIGNAL SIGINT

CMD ["python", "subscriber.py"]

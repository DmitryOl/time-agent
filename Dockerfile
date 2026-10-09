FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -q -r requirements.txt

RUN mkdir -p /app/data && chown 10001:10001 /app/data
COPY --chown=10001:10001 app/ /app/

USER 10001:10001

CMD ["python", "main.py"]
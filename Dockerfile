FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p /data /config

EXPOSE 3856

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3856", "--log-level", "warning"]

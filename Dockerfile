FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config/*.example ./config/
COPY entrypoint.sh .

RUN mkdir -p /data /config && chmod +x /app/entrypoint.sh

EXPOSE 3856

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3856", "--log-level", "warning"]

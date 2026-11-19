FROM python:3.11-slim

# LibreOffice для конвертации .doc → .docx
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# У-9: сервис работает от непривилегированного пользователя
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin etl \
    && mkdir -p /app/artifacts && chown -R etl:etl /app/artifacts
USER etl
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

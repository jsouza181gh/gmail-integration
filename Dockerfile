FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/secrets \
    && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["uvicorn", "src.api.app:build_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

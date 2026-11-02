FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY mock_systems ./mock_systems
COPY sync_service ./sync_service
COPY dashboard ./dashboard

RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000 8001
CMD ["uvicorn", "sync_service.main:app", "--host", "0.0.0.0", "--port", "8001"]

ARG PYTHON_BASE=python:3.12-slim
FROM ${PYTHON_BASE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

ARG INSTALL_DEPS=true
COPY requirements.txt ./requirements.txt
RUN if [ "$INSTALL_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi

COPY risk_metrics ./risk_metrics
COPY api ./api
COPY ui/dist ./static
ENV STATIC_DIR=/app/static

EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

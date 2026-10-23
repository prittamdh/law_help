FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY law_help ./law_help
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "law_help.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]

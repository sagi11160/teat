FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway_hmi/ ./gateway_hmi/
COPY edge_mock/ ./edge_mock/
COPY cloud_ai_agent/ ./cloud_ai_agent/
CMD ["uvicorn", "gateway_hmi.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cloud_ai_agent/ ./cloud_ai_agent/
CMD ["uvicorn", "cloud_ai_agent.generator:app", "--host", "0.0.0.0", "--port", "8002"]

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY edge_mock/ ./edge_mock/
CMD ["uvicorn", "edge_mock.camera:app", "--host", "0.0.0.0", "--port", "8001"]

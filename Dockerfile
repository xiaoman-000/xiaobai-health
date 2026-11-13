FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    XIAOBAI_HOST=0.0.0.0 \
    PORT=8080

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt || true

COPY app.py xiaobai_agent.py ./
COPY web ./web
COPY 知识库 ./知识库

EXPOSE 8080
CMD ["python", "-u", "app.py"]

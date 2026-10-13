FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install chromium --with-deps || true
COPY app.py index.html login_worker.py start.sh ./
RUN mkdir -p downloads jobs exports pages cookies .tmp
EXPOSE 8756
CMD ["bash", "start.sh"]

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install --with-deps chromium
COPY . .
ENV HOST=0.0.0.0 PORT=8000 DATA_DIR=/app/data
EXPOSE 8000
VOLUME ["/app/data"]
CMD ["python", "app.py"]

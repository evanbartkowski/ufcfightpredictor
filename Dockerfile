FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt && pip check
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN python -m playwright install --with-deps chromium
COPY . .
ENV OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
RUN DATA_DIR=/app/prebuilt python app.py --prepare-model
ENV HOST=0.0.0.0 PORT=8000 DATA_DIR=/app/data PREBUILT_DATA_DIR=/app/prebuilt
EXPOSE 8000
VOLUME ["/app/data"]
CMD ["python", "app.py"]

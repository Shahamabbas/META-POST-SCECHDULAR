FROM python:3.12-slim
WORKDIR /app
COPY app.py integration.py ./
COPY static ./static
RUN useradd --uid 10001 --create-home scheduler && mkdir /data && chown scheduler:scheduler /data
USER scheduler
ENV HOST=0.0.0.0 PORT=8080 DATA_DIR=/data PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME ["/data"]
CMD ["python", "app.py"]

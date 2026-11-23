FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /service
RUN groupadd --gid 10001 worker && useradd --uid 10001 --gid worker --no-create-home worker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY config.py models.py processor.py state.py telegram_bot.py worker.py client.py healthcheck.py ./
RUN mkdir /service/data && chown worker:worker /service/data
USER worker
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 CMD ["python", "healthcheck.py"]
CMD ["python", "worker.py"]

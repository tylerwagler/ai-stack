FROM python:3.11-slim
WORKDIR /app
COPY proxy.py .
RUN mkdir -p /app/logs && pip install requests psycopg2-binary
EXPOSE 8081
CMD ["python", "proxy.py"]

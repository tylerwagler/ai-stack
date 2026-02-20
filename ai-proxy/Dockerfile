FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proxy.py models.json ./
EXPOSE 8081
CMD ["python", "-u", "proxy.py"]

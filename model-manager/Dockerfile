FROM python:3.11-slim
WORKDIR /app
ARG MODELS_INI=models.ini
COPY main.py .
COPY ${MODELS_INI} models.ini
EXPOSE 8100
CMD ["python", "-u", "main.py"]

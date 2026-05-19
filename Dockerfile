FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY seed.json ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "-m", "visa_tracker"]

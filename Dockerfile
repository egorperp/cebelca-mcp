FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
ENV CEBELCA_MODE=demo
EXPOSE 8000
CMD ["cebelca-mcp", "--http", "--host", "0.0.0.0", "--port", "8000"]

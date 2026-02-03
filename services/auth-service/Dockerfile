FROM docker.arvancloud.ir/python:3.13-slim

LABEL title="ShopStream Auth Service" \
      description="Auth service for JWT tokens + refresh sessions backed by MariaDB" \
      version="1.0.0" \
      maintainer="mhamini"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000  

RUN groupadd -r appgroup && useradd -r -g appgroup appuser

WORKDIR /app

COPY requirements.txt .

RUN pip install -i https://mirror.chabokan.net/repository/pypi-proxy/simple --no-cache-dir -r requirements.txt

COPY --chown=appuser:appgroup src/app.py .

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

CMD ["python", "app.py"]

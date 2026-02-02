FROM docker.arvancloud.ir/nginx:1.29-alpine

LABEL maintainer="mhamini" \
      version="1.0.0" \
      description="ShopStream frontend static site served by Nginx" \
      title="shopstream-frontend"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY src/ /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1

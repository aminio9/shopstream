FROM docker.arvancloud.ir/node:25.5-alpine3.22

LABEL title="ShopStream Product Service" \
      description="andles product catalog management with Elasticsearch integration." \
      version="1.0.0" \
      maintainer="mhamini"

WORKDIR /usr/src/app

RUN apk --no-cache add wget

COPY package*.json ./
RUN npm install --omit=dev --ignore-scripts

COPY src ./src

ENV PORT=3000 \
    NODE_ENV=production

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:3000/health || exit 1

USER node

CMD ["node", "src/index.js"]
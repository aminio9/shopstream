FROM docker.arvancloud.ir/node:25.5-alpine3.22

LABEL title="ShopStream API Gateway" \
      description="Central API gateway with rate limiting and routing" \
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
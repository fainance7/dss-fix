FROM node:20-alpine AS builder

WORKDIR /app

ARG BACKEND_URL=http://backend:8000
ARG AI_SERVICE_URL=http://ai-service:8001
ENV BACKEND_URL=${BACKEND_URL}
ENV AI_SERVICE_URL=${AI_SERVICE_URL}

COPY package.json package-lock.json* ./
RUN npm ci --prefer-offline

COPY . .
RUN npm run build

FROM node:20-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]

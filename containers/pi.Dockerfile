FROM node:24-slim
WORKDIR /app
COPY workers/pi/package.json workers/pi/package-lock.json ./
RUN npm ci
COPY workers/pi/tsconfig.json ./
COPY workers/pi/src ./src
RUN npm run build && npm prune --omit=dev
ENV LANGSMITH_TRACING=false
CMD ["node","dist/server.js"]

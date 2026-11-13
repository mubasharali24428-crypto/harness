FROM node:22-slim AS build
WORKDIR /app
COPY section7/package.json section7/package-lock.json* ./section7/
WORKDIR /app/section7
RUN npm ci --no-audit --no-fund
COPY contracts/ts ../contracts/ts/
COPY section7/ ./
ARG NEXT_PUBLIC_IDENTITY_BASE_URL=http://localhost:8001
ARG NEXT_PUBLIC_ORCHESTRATOR_BASE_URL=http://localhost:8004
ARG NEXT_PUBLIC_SANDBOX_BASE_URL=http://localhost:8005
ARG NEXT_PUBLIC_MEETING_URL=http://localhost:8006
ARG NEXT_PUBLIC_REVIEW_URL=http://localhost:8008
ARG NEXT_PUBLIC_IDENTITY_WS_URL=ws://localhost:8001
ARG NEXT_PUBLIC_GITHUB_URL=http://localhost:8002
ARG NEXT_PUBLIC_MODELS_BASE_URL=http://localhost:8003
ARG NEXT_PUBLIC_CONTEXT_BASE_URL=http://localhost:8011
ARG NEXT_PUBLIC_ONBOARDING_BASE_URL=http://localhost:8010
RUN npm run build

FROM node:22-slim
WORKDIR /app/section7
COPY --from=build /app/section7/ ./
EXPOSE 3000
CMD ["npm", "run", "start"]

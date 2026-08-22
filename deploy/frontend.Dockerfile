FROM node:22-slim AS build
WORKDIR /app
COPY section7/package.json section7/package-lock.json* ./section7/
WORKDIR /app/section7
RUN npm ci --no-audit --no-fund
COPY contracts/ts ../contracts/ts/
COPY section7/ ./
RUN npm run build

FROM node:22-slim
WORKDIR /app/section7
COPY --from=build /app/section7/ ./
EXPOSE 3000
CMD ["npm", "run", "start"]

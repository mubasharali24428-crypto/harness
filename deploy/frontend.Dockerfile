FROM node:22-slim AS build
WORKDIR /app
COPY section9/package.json section9/package-lock.json* ./section9/
WORKDIR /app/section9
RUN npm ci --no-audit --no-fund
COPY contracts/ts ../contracts/ts/
COPY section9/ ./
RUN npm run build

FROM node:22-slim
WORKDIR /app/section9
COPY --from=build /app/section9/ ./
EXPOSE 3000
CMD ["npm", "run", "start"]

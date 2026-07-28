FROM node:22.13.1-bookworm-slim AS web-build
WORKDIR /workspace/frontend
COPY frontend/package.json ./
RUN npm install --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.13.2-slim-bookworm AS application
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/workspace/backend
WORKDIR /workspace
COPY backend/requirements.lock ./backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock
COPY backend/ ./backend/
COPY --from=web-build /workspace/frontend/dist ./frontend/dist
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "app.main:application", "--factory", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]

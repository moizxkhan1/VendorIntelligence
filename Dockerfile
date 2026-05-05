# Playwright's official Python image — Chromium + all required system libs
# pre-installed. Avoids running `playwright install --with-deps` at build
# time and the apt/sudo dance that comes with it on Nixpacks/Heroku stacks.
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

WORKDIR /app

# Install Python deps first so Docker can cache the layer when only app code
# changes. Re-run `playwright install chromium` afterwards: it's a no-op when
# the package version matches the base image, but ensures correctness if the
# pinned playwright python lib drifts from the image's bundled binary.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install chromium

COPY app/ ./app/

# Railway injects $PORT at runtime; default to 8000 for `docker run` locally.
ENV PORT=8000
EXPOSE 8000

# Shell form so $PORT expands.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]

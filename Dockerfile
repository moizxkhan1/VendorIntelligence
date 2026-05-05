# Playwright's official Python image — Chromium + all required system libs
# pre-installed. Avoids running `playwright install --with-deps` at build
# time and the apt/sudo dance that comes with it on Nixpacks/Heroku stacks.
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

WORKDIR /app

# Install Python deps first so Docker can cache the layer when only app code
# changes. Re-run `playwright install chromium` afterwards: it's a no-op when
# the base-image binary matches, but ensures correctness if the pinned
# playwright python lib drifts from the image's bundled binary.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install chromium

COPY app/ ./app/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PORT=8000
EXPOSE 8000

# ENTRYPOINT (not CMD) so the orchestrator can't accidentally override it
# with an unshelled command. Any args passed by the platform's "Start
# Command" field arrive as positional params and are ignored by the script.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

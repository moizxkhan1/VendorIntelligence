#!/bin/sh
# Container entrypoint. Reads PORT from the environment so we don't depend
# on the orchestrator (Railway, Heroku, etc.) running the start command
# through a shell that can expand $PORT — some don't.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"

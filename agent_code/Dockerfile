# -----------------------------------------------------------------------------
# Dockerfile — Infra Copilot Agent
# -----------------------------------------------------------------------------
# Uses a slim Python base image to keep the container small (faster pulls,
# smaller attack surface — good practice for anything running with cluster
# permissions).
# -----------------------------------------------------------------------------

FROM python:3.12-slim

# Set the working directory inside the container
WORKDIR /app

# Copy only the requirements file first — this is a Docker layer-caching
# best practice: if your code changes but requirements.txt doesn't, Docker
# reuses the cached "pip install" layer instead of re-installing everything.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the actual application code
COPY infra_monitor_agent_api.py .

# NOTE: no .env file is copied into the image. Secrets (like OPENAI_API_KEY)
# are injected at runtime via Kubernetes Secrets/environment variables
# instead — baking secrets into an image is a common security mistake,
# since anyone who pulls the image can extract them.

# Run the agent when the container starts
CMD ["uvicorn", "infra_monitor_agent_api:app", "--host", "0.0.0.0", "--port", "8000"]

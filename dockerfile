# Dockerfile for the TestWriter agent.
#
# Note: the agent calls `docker exec` to reach its sandbox container, so
# running this in Docker requires mounting the host Docker socket. The
# simpler deployment is "agent on host, sandbox in Docker" — this file is
# provided for the "can it be containerized?" question, not as the default
# run path.
#
# Build:
#   docker build -t testwriter-agent .
#
# Run:
#   docker run --rm -it \
#       -v /var/run/docker.sock:/var/run/docker.sock \
#       --env-file ../.env \
#       testwriter-agent

FROM python:3.11-slim

# docker CLI so the agent can `docker exec` into its sandbox
RUN apt-get update && apt-get install -y --no-install-recommends \
        docker.io \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the agent code (everything except the .env which comes from --env-file)
COPY *.py config.json ./

# Don't bake secrets in. The user provides them via --env-file at run time.
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
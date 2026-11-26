# Denne filen er en proof of concept - det fungerer, men er ikke optimalisert for produksjon.
FROM ubuntu:26.04

ARG COMMIT_SHA
ENV DEBIAN_FRONTEND=noninteractive \
    R_HOME=/usr/lib/R \
    UV_LINK_MODE=copy \
    STATISTIKKPRODUKT_COMMIT_SHA=${COMMIT_SHA}

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      python3 \
      python3-dev \
      python3-pip \
      python3-venv \
      r-base \
      r-base-dev \
      build-essential \
      libffi-dev \
      libssl-dev \
    && python3 -m pip install --break-system-packages --root-user-action=ignore uv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml setup.py MANIFEST.in uv.lock README.md statistikkprodukt_register.md ./
COPY Statistikkprodukt ./Statistikkprodukt
COPY NavDataprodukt ./NavDataprodukt
# Run in two steps to improve docker caching. First dependencies, then the project.
RUN uv sync --no-dev --no-install-project
RUN uv sync --no-dev

RUN uv run --no-sync statistikkprodukt installer-r-pakker

ENTRYPOINT ["uv", "run", "--no-sync", "statistikkprodukt"]
CMD ["--help"]

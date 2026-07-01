# syntax=docker/dockerfile:1.4

ARG RUBY_VERSION=3.4

FROM ruby:${RUBY_VERSION}-slim-bookworm AS runtime

ENV BUNDLE_APP_CONFIG=/usr/local/bundle \
    BUNDLE_PATH=/usr/local/bundle \
    BUNDLE_WITHOUT="" \
    GEM_HOME=/usr/local/bundle \
    PATH=/usr/local/bundle/bin:$PATH

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      libpq5 \
      sqlite3 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM runtime AS build

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      libsqlite3-dev \
      pkg-config && \
    rm -rf /var/lib/apt/lists/*


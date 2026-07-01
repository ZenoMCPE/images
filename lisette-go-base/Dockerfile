# syntax=docker/dockerfile:1.4

ARG GO_VERSION=1.26.4
ARG LISETTE_REPO_URL=https://github.com/ivov/lisette.git
ARG LISETTE_COMMIT_HASH=e819ec010fe9f3758d45e961a0d9a11acb3dd26f

FROM golang:${GO_VERSION}-bookworm AS go-base

FROM rust:1-bookworm AS base
ARG LISETTE_REPO_URL
ARG LISETTE_COMMIT_HASH

COPY --from=go-base /usr/local/go /usr/local/go

ENV CGO_ENABLED=0
ENV GOPRIVATE=github.com/ZenoMCPE/*,github.com/zenomcpe/*,github.com/oomph-ac/*,github.com/df-mc/dragonfly
ENV GONOPROXY=github.com/ZenoMCPE/*,github.com/zenomcpe/*,github.com/oomph-ac/*,github.com/df-mc/dragonfly
ENV GONOSUMDB=github.com/ZenoMCPE/*,github.com/zenomcpe/*,github.com/oomph-ac/*,github.com/df-mc/dragonfly
ENV PATH=/usr/local/go/bin:/root/.cargo/bin:$PATH
ENV GOMAXPROCS=1
ENV RAYON_NUM_THREADS=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      openssh-client && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /root/.ssh && \
    ssh-keyscan github.com >> /etc/ssh/ssh_known_hosts

RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,target=/tmp/cargo-target,sharing=locked \
    CARGO_NET_GIT_FETCH_WITH_CLI=true CARGO_TARGET_DIR=/tmp/cargo-target cargo install --git "${LISETTE_REPO_URL}" --rev "${LISETTE_COMMIT_HASH}" --locked lisette

WORKDIR /app

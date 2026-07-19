# syntax=docker/dockerfile:1.7

ARG BOOTSTRAP_GO_VERSION=1.26.4
ARG GOPLUS_REPO_URL=https://github.com/RestartFU/goplus.git
ARG GOPLUS_COMMIT_HASH=aecf0b01bc447a22f4a646ce79df026e5ee2a2db

FROM golang:${BOOTSTRAP_GO_VERSION}-bookworm AS build
ARG GOPLUS_REPO_URL
ARG GOPLUS_COMMIT_HASH

RUN mkdir -p /usr/local/goplus && \
    cd /usr/local/goplus && \
    git init && \
    git remote add origin "${GOPLUS_REPO_URL}" && \
    git fetch --depth 1 origin "${GOPLUS_COMMIT_HASH}" && \
    git checkout --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "${GOPLUS_COMMIT_HASH}"

WORKDIR /usr/local/goplus

RUN make install \
      DESTDIR=/opt/goplus \
      INSTALL_GOROOT=/usr/local/go \
      BINDIR=/usr/local/bin

COPY goplus-base/enum_smoke.go /tmp/enum_smoke.go

RUN /opt/goplus/usr/local/go/bin/go run /tmp/enum_smoke.go

FROM debian:bookworm-slim AS base

ENV CGO_ENABLED=0
ENV GOPRIVATE=github.com/ZenoMCPE/*,github.com/zenomcpe/*,github.com/oomph-ac/*,github.com/df-mc/dragonfly
ENV GONOPROXY=github.com/ZenoMCPE/*,github.com/zenomcpe/*,github.com/oomph-ac/*,github.com/df-mc/dragonfly
ENV GONOSUMDB=github.com/ZenoMCPE/*,github.com/zenomcpe/*,github.com/oomph-ac/*,github.com/df-mc/dragonfly
ENV GOTOOLCHAIN=local
ENV GOMAXPROCS=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      openssh-client && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /root/.ssh && \
    ssh-keyscan github.com >> /root/.ssh/known_hosts

COPY --from=build /opt/goplus/usr/local/go /usr/local/go

RUN ln -s /usr/local/go/bin/go /usr/local/bin/go+ && \
    go+ version

WORKDIR /app

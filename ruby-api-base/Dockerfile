# syntax=docker/dockerfile:1.4

ARG RUBY_VERSION=3.4.12

FROM ruby:${RUBY_VERSION}-slim-bookworm AS patched-ruby
RUN apt-get update && apt-get install -y --no-install-recommends \
      bison build-essential ca-certificates curl libgmp-dev libssl-dev \
      libyaml-dev patch pkg-config python3 rustc zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*
COPY ruby-api-base/agent-build /usr/local/bin/agent-build
COPY ruby-api-base/rebuild-ruby /usr/local/bin/rebuild-ruby
COPY ruby-api-base/patches/signal-close.patch /tmp/signal-close.patch
RUN agent-build -- rebuild-ruby

FROM ruby:${RUBY_VERSION}-slim-bookworm AS runtime
LABEL org.zenomc.ruby.signal-close-backport="1565adc5b9d2dfc6d288dc9bdefb19b2423aa28c"
COPY --from=patched-ruby /opt/ruby-fix/lib/ /usr/local/lib/
COPY --from=patched-ruby /opt/ruby-fix/licenses/ /usr/local/share/licenses/ruby-signal-close/
COPY ruby-api-base/test-signal-close.rb /usr/local/libexec/ruby-signal-close-test.rb
RUN ldconfig && ruby /usr/local/libexec/ruby-signal-close-test.rb && ruby -e 'abort "unexpected Ruby runtime" unless RUBY_VERSION == "3.4.12" && RUBY_PATCHLEVEL == 148'

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


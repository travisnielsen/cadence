FROM ubuntu:22.04

ARG RUNNER_VERSION=2.332.0
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    jq \
    libicu70 \
    libkrb5-3 \
    liblttng-ust1 \
    libssl3 \
    libstdc++6 \
    openssl \
    unzip \
    wget \
    apt-transport-https \
    software-properties-common \
    && rm -rf /var/lib/apt/lists/*

RUN curl -sL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /tmp/microsoft.gpg \
    && install -o root -g root -m 644 /tmp/microsoft.gpg /etc/apt/trusted.gpg.d/microsoft.gpg \
    && sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/repos/azure-cli/ jammy main" > /etc/apt/sources.list.d/azure-cli.list' \
    && sh -c 'echo "deb [arch=amd64] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" > /etc/apt/sources.list.d/microsoft-prod.list' \
    && apt-get update \
    && apt-get install -y --no-install-recommends azure-cli powershell dotnet-sdk-8.0 \
    && rm -rf /var/lib/apt/lists/* /tmp/microsoft.gpg

RUN dotnet tool install --tool-path /usr/local/bin microsoft.sqlpackage

RUN useradd --create-home --shell /bin/bash runner
WORKDIR /home/runner/actions-runner

RUN curl -fsSL -o actions-runner.tar.gz "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && tar xzf actions-runner.tar.gz \
    && rm actions-runner.tar.gz \
    && ./bin/installdependencies.sh

COPY entrypoint.sh /home/runner/actions-runner/entrypoint.sh
RUN chmod +x /home/runner/actions-runner/entrypoint.sh \
    && chown -R runner:runner /home/runner

USER runner
ENTRYPOINT ["/home/runner/actions-runner/entrypoint.sh"]

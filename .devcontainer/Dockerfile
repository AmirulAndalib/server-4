# Use Debian-based Python image instead of Alpine
FROM python:3.13-bookworm

# Install system dependencies equivalent to what's in the Alpine base image
RUN set -x \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        tzdata \
        # cifs utils and libnfs are needed for smb and nfs support (file provider)
        cifs-utils \
        libnfs-dev \
        # openssl-dev equivalent for airplay
        libssl-dev \
        # Additional development tools
        git \
        zsh \
        bash \
        sudo \
        lsof \
        curl \
        wget \
        jq \
        # jemalloc for more efficient memory management (equivalent to Alpine base)
        libjemalloc2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install snapcast from GitHub releases (not available in standard Debian repos)
RUN set -x \
    && SNAPCAST_VERSION="0.32.3" \
    && ARCH=$(dpkg --print-architecture) \
    && curl -L "https://github.com/badaix/snapcast/releases/download/v${SNAPCAST_VERSION}/snapclient_${SNAPCAST_VERSION}-1_${ARCH}_bookworm.deb" -o /tmp/snapclient.deb \
    && curl -L "https://github.com/badaix/snapcast/releases/download/v${SNAPCAST_VERSION}/snapserver_${SNAPCAST_VERSION}-1_${ARCH}_bookworm.deb" -o /tmp/snapserver.deb \
    && dpkg -i /tmp/snapclient.deb /tmp/snapserver.deb || apt-get install -f -y \
    && rm /tmp/snapclient.deb /tmp/snapserver.deb

# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Get static ffmpeg builds from https://hub.docker.com/r/mwader/static-ffmpeg/
COPY --from=mwader/static-ffmpeg:7.1.1 /ffmpeg /usr/local/bin/
COPY --from=mwader/static-ffmpeg:7.1.1 /ffprobe /usr/local/bin/

# Copy widevine client files from base image
COPY --from=ghcr.io/music-assistant/base:latest /usr/local/bin/widevine_cdm/ /usr/local/bin/widevine_cdm/

# Set up permissions similar to the Alpine base
RUN chmod -R 777 /tmp

# JEMalloc for more efficient memory management (same as Alpine base)
ENV LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libjemalloc.so.2"

RUN git config --system --add safe.directory "*"

RUN pip install --upgrade pip && \
    pip install --upgrade uv

# Create user with proper sudo access (Debian style)
RUN useradd -m -s /bin/bash mass && \
    usermod -aG sudo mass && \
    echo "mass ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/mass
USER mass

# Pre-create the .musicassistant directory with proper permissions
RUN mkdir -p /home/mass/.musicassistant

ENV VIRTUAL_ENV=/home/mass/.local/music-assistant-venv
RUN uv venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV DEV_CONTAINER=1

WORKDIR /tmp
COPY requirements_all.txt .

# Use the standard PyPI wheels for Debian instead of musllinux wheels
RUN uv pip install -r requirements_all.txt

ENV SHELL=/bin/bash

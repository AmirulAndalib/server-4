ARG BASE_IMAGE_VERSION=latest

FROM ghcr.io/music-assistant/base:$BASE_IMAGE_VERSION

RUN apk add --no-cache git zsh bash github-cli

RUN git config --system --add safe.directory "*"

RUN pip install --upgrade pip && \
    pip install --upgrade uv

RUN adduser mass -D -G wheel
USER mass

ENV VIRTUAL_ENV=/home/mass/.local/music-assistant-venv
RUN uv venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV DEV_CONTAINER=1

WORKDIR /tmp
COPY requirements_all.txt .

RUN uv pip install \
    --find-links "https://wheels.home-assistant.io/musllinux/" \
    -r requirements_all.txt

ENV SHELL=/bin/bash

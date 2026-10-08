FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ENV PYTHONDONTWRITEBYTECODE=1
COPY install_codex.py /opt/install_codex.py
RUN python /opt/install_codex.py && codex --version
WORKDIR /workspace
COPY positive_sum.py /workspace/positive_sum.py

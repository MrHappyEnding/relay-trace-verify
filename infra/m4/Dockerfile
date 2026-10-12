# Separate executor daemon per Job, adapting Harbor's remote DinD pattern.
FROM docker:28.5.1-dind@sha256:ea9d20492ca1caaaba78e68453433895d256173c79281756e88b745647fcbcfd AS engine
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
COPY --from=engine /usr/local/bin/ /usr/local/bin/
COPY --from=engine /usr/local/libexec/docker/cli-plugins/ /usr/local/libexec/docker/cli-plugins/
RUN apt-get update && apt-get install -y --no-install-recommends iptables iproute2 git \
    && rm -rf /var/lib/apt/lists/* && pip install --no-cache-dir uv==0.12.3
WORKDIR /app
COPY pyproject.toml uv.lock README.en.md ./
RUN uv sync --locked --group runtime --no-dev --no-install-project --python /usr/local/bin/python
COPY src ./src
COPY examples ./examples
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-m", "rtv.cluster.runner"]

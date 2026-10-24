FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY examples ./examples
RUN uv sync --frozen --no-dev --no-editable && cp -a /app/.venv /opt/venv
COPY containers/strip_orderops_evaluation.py /tmp/strip_orderops_evaluation.py
# The working source tree and BOTH installed environments are readable by workers.
RUN /opt/venv/bin/python /tmp/strip_orderops_evaluation.py /app/src /app/.venv/lib /opt/venv/lib
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 LANGSMITH_TRACING=false
CMD ["python", "-m", "harness_clone.worker"]

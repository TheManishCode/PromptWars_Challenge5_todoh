# syntax=docker/dockerfile:1.7
# to;doh-the event planner: one image, shared (multi-visitor) mode capable. Build: docker build -t todoh .
# Stage 1 builds the two planning tools (Fast Downward, VAL) the way CI does; stage 2 is the small runtime, running as an unprivileged user.
FROM python:3.14-slim-bookworm AS solvers
RUN apt-get update && apt-get install -y --no-install-recommends cmake g++ make flex bison git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN git clone --depth 1 --branch release-24.06.1 https://github.com/aibasel/downward.git && cd downward && python3 build.py
RUN git clone --depth 1 https://github.com/KCL-Planning/VAL.git && cd VAL && mkdir -p build && cd build && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j"$(nproc)"

FROM python:3.14-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 EVENTMATE_FD=/opt/solvers/downward/fast-downward.py EVENTMATE_VAL=/opt/solvers/VAL/build/bin/Validate \
    EVENTMATE_DATA_DIR=/data EVENTMATE_MODE=shared EVENTMATE_ACCESS_LOG=stderr
RUN useradd --system --uid 10001 --create-home todoh && mkdir /data && chown todoh /data && chmod 700 /data
COPY --from=solvers /build/downward /opt/solvers/downward
COPY --from=solvers /build/VAL/build/bin /opt/solvers/VAL/build/bin
WORKDIR /app
COPY requirements-runtime.lock pyproject.toml README.md ./
COPY eventmate ./eventmate
# The installer is not needed at run time and carries its own vendored copies of libraries (which scanners flag): it is removed once the install is done.
RUN pip install --retries 10 --timeout 60 --require-hashes -r requirements-runtime.lock && pip install --no-deps . && rm -rf /app/eventmate && pip uninstall -y pip setuptools wheel
USER todoh
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=2).status==200 else 1)"
# Shared mode refuses to start without EVENTMATE_SECRET, EVENTMATE_TRUSTED_PROXY, EVENTMATE_PUBLIC_URL and an allowed host: see deploy/env.example and docs/HOSTING.md.
CMD ["python", "-m", "eventmate.web", "--host", "0.0.0.0", "--port", "8000", "--allow-remote"]

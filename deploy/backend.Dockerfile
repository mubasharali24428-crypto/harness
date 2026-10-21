FROM python:3.13-slim

WORKDIR /opt/harness

COPY contracts/ ./contracts/
COPY section1/ ./section1/
COPY section2/ ./section2/
COPY section3/ ./section3/
COPY section4/ ./section4/
COPY section5/ ./section5/
COPY section6/ ./section6/
COPY section8/ ./section8/
COPY deploy/runners/ ./deploy/runners/
COPY deploy/entrypoint.sh ./deploy/entrypoint.sh
COPY section10/demo_repo/ ./demo_repo/

# AGENTS.md rule: PIN the DeepSeek Harness SDK version; bump deliberately.
RUN pip install --no-cache-dir "deepseek-harness-sdk==0.1.1rc1" \
      ./contracts \
      ./section1 ./section2 ./section3 ./section4 \
      ./section5 ./section6 ./section8 \
    && rm -rf /root/.cache

# Non-root runtime: services never need privileges beyond /tmp workspaces.
# /var/lib/harness-store is the mountpoint of the shared store volume (compose `harness_store`):
# owning it here makes a fresh named volume writable by uid harness.
RUN useradd --system --create-home --shell /usr/sbin/nologin harness \
    && mkdir -p /tmp/harness-dsh /tmp/harness_sandboxes /var/lib/harness-store \
    && chown -R harness:harness /tmp/harness-dsh /tmp/harness_sandboxes /var/lib/harness-store
USER harness

EXPOSE 8001 8002 8003 8004 8005 8006 8008

ENTRYPOINT ["/opt/harness/deploy/entrypoint.sh"]

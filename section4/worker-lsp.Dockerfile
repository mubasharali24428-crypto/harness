# W7 overlay: a pinned, real Python language server on top of the current worker.
#
# The worker image is deliberately language-neutral (Python 3.12 + pip, no
# pyright/pylsp/clangd). Rather than widen every run's image, W7 proves the LSP
# integration on one overlay so the cost and the attack surface stay visible:
#
#   docker build -f section4/worker-lsp.Dockerfile -t harness-engine-worker-lsp:vdev-local .
#
# Everything is version-pinned. The image digest — not this file — is what the
# drill records as the environment, because a resolver can otherwise drift
# under an unchanged requirements line.
FROM harness-engine-worker:vdev-local

USER root
RUN python -m pip install --no-cache-dir --no-compile \
        "python-lsp-server[pyflakes,rope]==1.12.2" \
        "pyflakes==3.2.0" \
        "rope==1.13.0"
# The client is baked in beside the gates so the drill does not depend on a
# bind mount for the code under test (`--network none`, workspace read-only).
COPY section5/src/harness_sandbox/lsp_client.py /opt/harness-lsp/lsp_client.py
ENV HARNESS_LSP_CLIENT=/opt/harness-lsp/lsp_client.py
USER 10001:10001
WORKDIR /tmp
ENTRYPOINT ["python", "-m", "harness_orchestrator.engine_worker"]

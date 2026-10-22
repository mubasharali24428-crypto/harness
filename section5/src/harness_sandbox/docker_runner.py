class DockerSandboxRunner:
    """Reserved container runner. NOT IMPLEMENTED — refuse rather than lie.

    The previous class subclassed LocalSandboxRunner with ``pass``, so a
    deployment that asked for container isolation silently got host-process
    execution. Fail loudly until a real per-task container boundary exists.
    """

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "DockerSandboxRunner is not implemented; use LocalSandboxRunner with"
            " an OS boundary, or run the whole service in a container"
        )

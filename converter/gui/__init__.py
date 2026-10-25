"""Japanese Windows GUI; imported lazily by the command line entry point."""

def launch(sources=None, smoke_test=False):
    from .app import launch as run
    return run(sources or [], smoke_test=smoke_test)

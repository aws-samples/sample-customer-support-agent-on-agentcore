"""
Entry point for running the runtime module with: python -m agent.runtime
"""

if __name__ == "__main__":
    from .entrypoint import run_local
    run_local()

"""
runtime.local.hooks — Optional startup/shutdown hooks for local mode
======================================================================
Define any of these functions to add custom behaviour around the
local app lifecycle. All hooks are optional and failure-safe — a
missing or failing hook never blocks startup.

Available hooks:
    on_startup(config: dict) -> None    — called before Gradio launches
    on_shutdown(config: dict) -> None   — called on clean exit

Example:
    def on_startup(config):
        print("Custom startup logic here")
"""


def on_startup(config: dict) -> None:
    """Called once before the Gradio UI starts."""
    pass


def on_shutdown(config: dict) -> None:
    """Called once on clean process exit."""
    pass

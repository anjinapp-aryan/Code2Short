"""Typer entrypoint. `code2shorts generate <topic>` lands in Phase 1+."""

from __future__ import annotations

import typer

from code2shorts import __version__

app = typer.Typer(help="Generate short-form educational programming videos.")


@app.command()
def version() -> None:
    """Print the installed Code2Shorts version."""
    typer.echo(__version__)


if __name__ == "__main__":
    app()

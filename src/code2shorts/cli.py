"""Typer entrypoint. `code2shorts generate <topic>` lands in Phase 1+."""

from __future__ import annotations

import typer

from code2shorts import __version__

app = typer.Typer(help="Generate short-form educational programming videos.")


@app.command()
def version() -> None:
    """Print the installed Code2Shorts version."""
    typer.echo(__version__)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, help="Port to bind."),
) -> None:
    """Start the Code2Shorts web UI.

    Imports the web layer lazily, so `code2shorts version` and every other
    command keep working when the optional `web` extra is not installed.
    """
    try:
        from code2shorts.webapp import serve as _serve
    except ImportError as error:  # pragma: no cover - depends on the install
        raise typer.BadParameter(
            "the web UI needs its optional dependencies: "
            'pip install -e ".[web]"'
        ) from error
    typer.echo(f"Code2Shorts UI on http://{host}:{port}")
    _serve(host=host, port=port)


if __name__ == "__main__":
    app()

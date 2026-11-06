"""Run ``python -m bs_simulators`` or the installed bs-simulators command."""

from .cli import app


def main(argv=None):
    """Invoke the Typer application with ordinary command-line exit behavior."""
    app(args=argv, prog_name='bs-simulators')


if __name__ == '__main__':
    main()

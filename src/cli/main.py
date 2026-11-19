"""CLI: classifica e-mails não lidos; retoma de onde parou.

Uso: python -m src.cli.main [--query Q] [--reset] [--max-pages N]
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()  # before importing modules that read env at import time

from src.cli.classify_unread import DEFAULT_QUERY, run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classifica e-mails não lidos; retoma de onde parou."
    )
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument(
        "--reset", action="store_true", help="começa uma run nova em vez de retomar"
    )
    parser.add_argument(
        "--max-pages", type=int, default=None, help="páginas a buscar nesta execução"
    )
    args = parser.parse_args()
    sys.exit(run(query=args.query, reset=args.reset, max_pages=args.max_pages))


if __name__ == "__main__":
    main()

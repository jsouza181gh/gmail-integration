"""CLI: reclassifica com a taxonomia ativa os e-mails já salvos; retoma de onde parou.

Uso: python -m src.cli.reclassify [--all]
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()  # before importing modules that read env at import time

from src.classification.classifier import Abort, classify_pending  # noqa: E402
from src.cli.classify_unread import build_classifier, print_report  # noqa: E402
from src.db.store import Store  # noqa: E402
from src.utils.console import log  # noqa: E402


def run(all_emails: bool = False) -> int:
    store = Store()
    try:
        run_id = store.start_reclassify_run(only_outdated=not all_emails)
        log(f"reclassificação run #{run_id}: {store.count_pending(run_id)} pendentes")
        classify_pending(store, build_classifier(store), run_id)
        if not store.count_pending(run_id):
            print_report(store, run_id)
        return 0
    except Abort as exc:
        log(f"ABORTADO: {exc}")
        return 1
    except KeyboardInterrupt:
        log("interrompido; progresso salvo")
        return 130
    except Exception as exc:  # noqa: BLE001
        log(f"ERRO: {type(exc).__name__}: {exc}; progresso salvo, rode de novo")
        return 1
    finally:
        store.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassifica e-mails salvos com a taxonomia ativa."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="todos os e-mails, não só os classificados com outra versão",
    )
    args = parser.parse_args()
    sys.exit(run(all_emails=bool(args.all)))


if __name__ == "__main__":
    main()

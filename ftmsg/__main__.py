import argparse
import logging

from .tui import run_tui


def main() -> None:
    parser = argparse.ArgumentParser(description="42msg terminal client")
    parser.add_argument("--login", help="Login 42 affiché sur le réseau", default=None)
    parser.add_argument("--debug", action="store_true", help="Active les logs de debug")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    run_tui(login=args.login)


if __name__ == "__main__":
    main()

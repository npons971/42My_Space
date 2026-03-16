import argparse

from .tui import run_tui


def main() -> None:
    parser = argparse.ArgumentParser(description="42msg terminal client")
    parser.add_argument("--login", help="Login 42 affiché sur le réseau", default=None)
    args = parser.parse_args()
    run_tui(login=args.login)


if __name__ == "__main__":
    main()

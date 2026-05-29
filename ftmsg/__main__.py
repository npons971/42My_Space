import argparse
import asyncio
import logging
import sys

from .client import FTMessageClient
from .tui import run_tui


def main() -> None:
    parser = argparse.ArgumentParser(description="42msg terminal client")
    parser.add_argument("--login", help="Login 42 affiché sur le réseau", default=None)
    parser.add_argument("--debug", action="store_true", help="Active les logs de debug")
    parser.add_argument("--host", metavar="NAME", help="Créer un salon et quitter (mode headless)")
    parser.add_argument("--max", dest="max_users", type=int, default=10, help="Max users pour --host")
    parser.add_argument("--password", default="", help="Mot de passe pour --host")
    parser.add_argument("--join", metavar="IP:PORT", help="Rejoindre un salon et quitter (mode headless)")
    parser.add_argument("--join-password", default="", dest="join_password", help="Mot de passe pour --join")
    parser.add_argument("--relay", metavar="URL", help="URL du serveur relais WebSocket (ex: wss://relay.render.com)")
    args = parser.parse_args()

    if args.relay:
        import os
        os.environ["FTMSG_RELAY_URL"] = args.relay

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    if args.host:
        asyncio.run(_run_headless_host(args))
    elif args.join:
        asyncio.run(_run_headless_join(args))
    else:
        run_tui(login=args.login)


async def _run_headless_host(args) -> None:
    login = args.login or args.host
    client = FTMessageClient(login)
    await client.start()
    is_public = (args.password == "")
    status = await client.create_channel(args.host, args.password, args.max_users, is_public)
    if status != "created":
        print(f"Échec création salon: {status}", file=sys.stderr)
        sys.exit(1)
    print(f"Salon '{args.host}' créé. Ctrl+C pour arrêter.")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await client.stop()
        print("\nSalon fermé.")


async def _run_headless_join(args) -> None:
    parts = args.join.rsplit(":", 1)
    if len(parts) != 2:
        print("Format: IP:PORT", file=sys.stderr)
        sys.exit(1)
    host_ip, host_port = parts[0], int(parts[1])
    login = args.login or "guest"
    client = FTMessageClient(login)
    await client.start()
    status, detail = await client.join_channel(host_ip, host_port, args.join_password)
    if status != "connected":
        print(f"Échec connexion: {status} ({detail})", file=sys.stderr)
        sys.exit(1)
    print(f"Connecté à '{detail}'. Tape un message et Entrée. /quit pour sortir.")
    try:
        while True:
            line = await asyncio.to_thread(input, "> ")
            if line.strip() == "/quit":
                break
            result = await client.send_channel_message(line.strip())
            if result == "not_in_channel":
                print("Déconnecté du salon.")
                break
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        await client.stop()
        print("Déconnecté.")


if __name__ == "__main__":
    main()

import argparse
import asyncio
import logging
import sys

from .client import FTMessageClient
from .tui import run_tui


def _check_update() -> None:
    """Tente de mettre à jour le code et les dépendances via git pull."""
    import os
    import subprocess
    import shutil

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git_dir = os.path.join(project_dir, ".git")

    if not os.path.exists(git_dir):
        return

    try:
        # Git pull avec timeout court
        result = subprocess.run(
            ["git", "pull", "--rebase", "--quiet"],
            cwd=project_dir,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Si le pull a réussi, mettre à jour les dépendances
        if result.returncode == 0:
            pip_cmd = None
            if shutil.which("uv"):
                pip_cmd = ["uv", "pip", "install", "--quiet", "-e", "."]
            elif shutil.which("pip3"):
                pip_cmd = ["pip3", "install", "--quiet", "-e", "."]
            elif shutil.which("pip"):
                pip_cmd = ["pip", "install", "--quiet", "-e", "."]
            if pip_cmd:
                try:
                    subprocess.run(
                        pip_cmd,
                        cwd=project_dir,
                        timeout=30,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
    except Exception:
        pass

def main() -> None:
    _check_update()

    parser = argparse.ArgumentParser(description="42msg terminal client")
    parser.add_argument("--debug", action="store_true", help="Active les logs de debug")
    parser.add_argument("--host", metavar="NAME", help="Créer un salon et quitter (mode headless)")
    parser.add_argument("--max", dest="max_users", type=int, default=10, help="Max users pour --host")
    parser.add_argument("--password", default="", help="Mot de passe pour --host")
    parser.add_argument("--join", metavar="IP:PORT", help="Rejoindre un salon et quitter (mode headless)")
    parser.add_argument("--join-password", default="", dest="join_password", help="Mot de passe pour --join")
    parser.add_argument("--relay", metavar="URL", default="wss://four2my-space.onrender.com", help="URL du serveur relais WebSocket (ex: wss://relay.render.com)")
    parser.add_argument("--no-relay", action="store_true", help="Force le mode P2P local (sans relais WebSocket)")
    parser.add_argument("--login", metavar="LOGIN", help="Pseudo à utiliser (défaut: $USER)")
    parser.add_argument("--data-dir", metavar="DIR", help="Dossier de données (défaut: ~/.42msg)")
    args = parser.parse_args()

    if args.relay and not args.no_relay:
        import os
        os.environ["FTMSG_RELAY_URL"] = args.relay

    if args.data_dir:
        import os
        os.environ["FTMSG_DATA_DIR"] = args.data_dir

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

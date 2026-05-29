#!/usr/bin/env python3
"""
42msg — Diagnostic réseau pour postes École 42
================================================
Aucune dépendance externe (stdlib Python 3 uniquement).

ÉTAPE 1 — Sur les DEUX postes :
    python3 scripts/diag_network.py --scan

ÉTAPE 2 — Sur le poste A (celui qui écoute) :
    python3 scripts/diag_network.py --listen

ÉTAPE 3 — Sur le poste B (celui qui teste), avec l'IP du poste A :
    python3 scripts/diag_network.py --test <IP_DU_POSTE_A>

Le rapport final s'affiche et est sauvegardé dans diag_report.txt.
"""

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

# ─── Ports utilisés pour les tests ─────────────────────────────────────────
DIAG_TCP_PORT = 44401
DIAG_UDP_PORT = 44402
DIAG_BCAST_PORT = 44403
DIAG_MCAST_PORT = 44404
MCAST_GROUP = "239.255.42.69"
MAGIC = b"42MSG_DIAG"

REPORT_FILE = "diag_report.txt"


# ═══════════════════════════════════════════════════════════════════════════
#  Utilitaires
# ═══════════════════════════════════════════════════════════════════════════

def ok(msg: str) -> str:
    return f"  ✅ {msg}"


def fail(msg: str) -> str:
    return f"  ❌ {msg}"


def warn(msg: str) -> str:
    return f"  ⚠️  {msg}"


def info(msg: str) -> str:
    return f"  ℹ️  {msg}"


def section(title: str) -> str:
    return f"\n{'─' * 60}\n  {title}\n{'─' * 60}"


def resolve_local_ip_8888() -> str | None:
    """Méthode classique : connect UDP vers 8.8.8.8."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip != "0.0.0.0" else None
    except OSError:
        return None


def resolve_local_ip_hostname() -> list[str]:
    """Méthode alternative : résolution du hostname."""
    try:
        hostname = socket.gethostname()
        results = socket.getaddrinfo(hostname, None, socket.AF_INET)
        ips = list({r[4][0] for r in results if not r[4][0].startswith("127.")})
        return ips
    except OSError:
        return []


def get_interfaces_ip() -> list[tuple[str, str]]:
    """Récupère les IPs depuis ip addr (Linux)."""
    pairs = []
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"], text=True, timeout=5
        )
        for line in out.strip().splitlines():
            parts = line.split()
            # format: "2: eth0    inet 10.11.1.42/22 brd 10.11.3.255 ..."
            iface = parts[1].rstrip(":")
            for i, p in enumerate(parts):
                if p == "inet" and i + 1 < len(parts):
                    ip_cidr = parts[i + 1]
                    ip = ip_cidr.split("/")[0]
                    if not ip.startswith("127."):
                        pairs.append((iface, ip))
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return pairs


def get_broadcast_and_mask() -> list[tuple[str, str, str, str]]:
    """Récupère iface, ip, broadcast, masque depuis ip addr."""
    results = []
    try:
        out = subprocess.check_output(
            ["ip", "-4", "-o", "addr", "show"], text=True, timeout=5
        )
        for line in out.strip().splitlines():
            parts = line.split()
            iface = parts[1].rstrip(":")
            ip_addr = ""
            brd = ""
            cidr = ""
            for i, p in enumerate(parts):
                if p == "inet" and i + 1 < len(parts):
                    ip_cidr = parts[i + 1]
                    ip_addr, cidr = ip_cidr.split("/") if "/" in ip_cidr else (ip_cidr, "?")
                if p == "brd" and i + 1 < len(parts):
                    brd = parts[i + 1]
            if ip_addr and not ip_addr.startswith("127."):
                results.append((iface, ip_addr, brd, cidr))
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return results


def check_port_available(port: int) -> bool:
    """Vérifie si un port TCP est libre."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", port))
        s.close()
        return True
    except OSError:
        return False


def check_internet() -> tuple[bool, str]:
    """Vérifie si 8.8.8.8 est joignable (UDP connect, pas ICMP)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2.0)
        s.connect(("8.8.8.8", 53))
        local_ip = s.getsockname()[0]
        s.close()
        return True, local_ip
    except OSError as e:
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════
#  MODE --scan : informations locales
# ═══════════════════════════════════════════════════════════════════════════

def run_scan() -> list[str]:
    lines: list[str] = []
    lines.append(section("INFORMATIONS SYSTÈME"))

    # Python
    lines.append(info(f"Python: {sys.version.split()[0]}"))
    lines.append(info(f"Hostname: {socket.gethostname()}"))
    lines.append(info(f"User: {os.environ.get('USER', '?')}"))

    # Interfaces réseau
    lines.append(section("INTERFACES RÉSEAU"))
    ifaces = get_broadcast_and_mask()
    if ifaces:
        for iface, ip, brd, cidr in ifaces:
            lines.append(info(f"{iface}: {ip}/{cidr}  broadcast={brd}"))
    else:
        lines.append(warn("Impossible de lister les interfaces (commande 'ip' absente ?)"))

    # Résolution IP locale
    lines.append(section("RÉSOLUTION IP LOCALE"))
    ip_8888 = resolve_local_ip_8888()
    if ip_8888:
        lines.append(ok(f"Méthode connect(8.8.8.8): {ip_8888}"))
    else:
        lines.append(fail("Méthode connect(8.8.8.8): ÉCHOUÉ — resolve_local_ip() retournera 127.0.0.1"))

    ips_hostname = resolve_local_ip_hostname()
    if ips_hostname:
        lines.append(ok(f"Méthode hostname: {', '.join(ips_hostname)}"))
    else:
        lines.append(warn("Méthode hostname: aucune IP non-loopback trouvée"))

    # Internet
    lines.append(section("CONNECTIVITÉ INTERNET"))
    has_internet, detail = check_internet()
    if has_internet:
        lines.append(ok(f"Route vers 8.8.8.8 OK (IP locale vue: {detail})"))
    else:
        lines.append(fail(f"Pas de route vers 8.8.8.8: {detail}"))

    # Ports
    lines.append(section("DISPONIBILITÉ DES PORTS"))
    for port, name in [(42069, "Discovery (42069)"), (DIAG_TCP_PORT, f"Diag TCP ({DIAG_TCP_PORT})")]:
        if check_port_available(port):
            lines.append(ok(f"Port {name}: LIBRE"))
        else:
            lines.append(fail(f"Port {name}: OCCUPÉ"))

    # Vérifier SO_BROADCAST
    lines.append(section("CAPACITÉS SOCKET"))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.close()
        lines.append(ok("SO_BROADCAST: supporté"))
    except OSError as e:
        lines.append(fail(f"SO_BROADCAST: {e}"))

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", DIAG_BCAST_PORT))
        mreq = struct.pack("4sL", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.close()
        lines.append(ok(f"Multicast ({MCAST_GROUP}): join OK"))
    except OSError as e:
        lines.append(warn(f"Multicast ({MCAST_GROUP}): {e}"))

    # Firewall
    lines.append(section("RÈGLES FIREWALL (iptables)"))
    try:
        out = subprocess.check_output(
            ["iptables", "-L", "-n", "--line-numbers"],
            text=True, timeout=5, stderr=subprocess.STDOUT,
        )
        # Montrer seulement les premières lignes pertinentes
        relevant = [l for l in out.splitlines()[:30]]
        if relevant:
            for l in relevant:
                lines.append(info(l))
        else:
            lines.append(info("(pas de règles)"))
    except (subprocess.SubprocessError, FileNotFoundError):
        lines.append(warn("iptables indisponible (pas root ou commande absente)"))

    # Résumé
    lines.append(section("RÉSUMÉ SCAN"))
    local_ip = ip_8888 or (ips_hostname[0] if ips_hostname else None)
    if local_ip:
        lines.append(ok(f"IP locale détectée: {local_ip}"))
        lines.append(info(f"Prochaine étape: lance --listen sur CE poste"))
        lines.append(info(f"Puis sur l'AUTRE poste: python3 scripts/diag_network.py --test {local_ip}"))
    else:
        lines.append(fail("Impossible de déterminer l'IP locale !"))
        if ifaces:
            lines.append(info(f"Utilise une IP d'interface manuellement: {ifaces[0][1]}"))

    return lines


# ═══════════════════════════════════════════════════════════════════════════
#  MODE --listen : démarre les serveurs de test
# ═══════════════════════════════════════════════════════════════════════════

def run_listen() -> None:
    local_ip = resolve_local_ip_8888()
    ips_hostname = resolve_local_ip_hostname()
    ip = local_ip or (ips_hostname[0] if ips_hostname else "???")

    print(section("MODE ÉCOUTE — En attente de tests"))
    print(info(f"IP locale: {ip}"))
    print(info(f"Sur l'autre poste, lance:"))
    print(info(f"  python3 scripts/diag_network.py --test {ip}"))
    print()

    results_lock = threading.Lock()
    results: dict[str, str] = {}

    def tcp_listener() -> None:
        """Écoute TCP sur DIAG_TCP_PORT."""
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.settimeout(120.0)
            srv.bind(("0.0.0.0", DIAG_TCP_PORT))
            srv.listen(1)
            print(ok(f"TCP listener démarré sur :{DIAG_TCP_PORT}"))
            conn, addr = srv.accept()
            data = conn.recv(1024)
            if MAGIC in data:
                conn.sendall(MAGIC + b":TCP_OK")
                with results_lock:
                    results["tcp"] = f"reçu de {addr[0]}:{addr[1]}"
                print(ok(f"TCP: connexion reçue de {addr[0]}:{addr[1]}"))
            conn.close()
            srv.close()
        except socket.timeout:
            print(warn("TCP: timeout (2 min), aucune connexion reçue"))
        except OSError as e:
            print(fail(f"TCP listener échoué: {e}"))

    def udp_listener() -> None:
        """Écoute UDP unicast sur DIAG_UDP_PORT."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(120.0)
            s.bind(("0.0.0.0", DIAG_UDP_PORT))
            print(ok(f"UDP unicast listener démarré sur :{DIAG_UDP_PORT}"))
            data, addr = s.recvfrom(1024)
            if MAGIC in data:
                s.sendto(MAGIC + b":UDP_OK", addr)
                with results_lock:
                    results["udp"] = f"reçu de {addr[0]}:{addr[1]}"
                print(ok(f"UDP unicast: paquet reçu de {addr[0]}:{addr[1]}"))
            s.close()
        except socket.timeout:
            print(warn("UDP unicast: timeout (2 min)"))
        except OSError as e:
            print(fail(f"UDP unicast listener échoué: {e}"))

    def bcast_listener() -> None:
        """Écoute UDP broadcast sur DIAG_BCAST_PORT."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(120.0)
            s.bind(("", DIAG_BCAST_PORT))
            print(ok(f"UDP broadcast listener démarré sur :{DIAG_BCAST_PORT}"))
            data, addr = s.recvfrom(1024)
            if MAGIC in data:
                with results_lock:
                    results["broadcast"] = f"reçu de {addr[0]}:{addr[1]}"
                print(ok(f"UDP broadcast: paquet reçu de {addr[0]}:{addr[1]}"))
            s.close()
        except socket.timeout:
            print(warn("UDP broadcast: timeout (2 min)"))
        except OSError as e:
            print(fail(f"UDP broadcast listener échoué: {e}"))

    def mcast_listener() -> None:
        """Écoute multicast sur DIAG_MCAST_PORT."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(120.0)
            s.bind(("", DIAG_MCAST_PORT))
            mreq = struct.pack("4sL", socket.inet_aton(MCAST_GROUP), socket.INADDR_ANY)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            print(ok(f"Multicast listener démarré ({MCAST_GROUP}:{DIAG_MCAST_PORT})"))
            data, addr = s.recvfrom(1024)
            if MAGIC in data:
                with results_lock:
                    results["multicast"] = f"reçu de {addr[0]}:{addr[1]}"
                print(ok(f"Multicast: paquet reçu de {addr[0]}:{addr[1]}"))
            s.close()
        except socket.timeout:
            print(warn("Multicast: timeout (2 min)"))
        except OSError as e:
            print(fail(f"Multicast listener échoué: {e}"))

    threads = [
        threading.Thread(target=tcp_listener, daemon=True),
        threading.Thread(target=udp_listener, daemon=True),
        threading.Thread(target=bcast_listener, daemon=True),
        threading.Thread(target=mcast_listener, daemon=True),
    ]

    for t in threads:
        t.start()

    print()
    print(info("En attente pendant 2 minutes max... (Ctrl+C pour arrêter)"))
    print()

    try:
        for t in threads:
            t.join(timeout=130)
    except KeyboardInterrupt:
        print("\nInterrompu.")

    print(section("RÉSULTATS ÉCOUTE"))
    for test_name, label in [("tcp", "TCP"), ("udp", "UDP unicast"), ("broadcast", "UDP broadcast"), ("multicast", "Multicast")]:
        if test_name in results:
            print(ok(f"{label}: {results[test_name]}"))
        else:
            print(fail(f"{label}: rien reçu"))


# ═══════════════════════════════════════════════════════════════════════════
#  MODE --test : teste la connectivité vers un poste distant
# ═══════════════════════════════════════════════════════════════════════════

def run_test(target_ip: str) -> list[str]:
    lines: list[str] = []
    lines.append(section(f"TEST DE CONNECTIVITÉ VERS {target_ip}"))

    # D'abord, scan local rapide
    local_ip = resolve_local_ip_8888()
    ips_hostname = resolve_local_ip_hostname()
    my_ip = local_ip or (ips_hostname[0] if ips_hostname else "???")
    lines.append(info(f"Mon IP: {my_ip}"))
    lines.append(info(f"Cible: {target_ip}"))
    lines.append("")

    # ── Test 1 : Ping ICMP ──
    lines.append(info("Test 1/6: Ping ICMP..."))
    try:
        ret = subprocess.run(
            ["ping", "-c", "2", "-W", "2", target_ip],
            capture_output=True, text=True, timeout=10,
        )
        if ret.returncode == 0:
            lines.append(ok(f"Ping: SUCCÈS"))
            # Extraire le RTT
            for l in ret.stdout.splitlines():
                if "rtt" in l or "avg" in l:
                    lines.append(info(f"  {l.strip()}"))
        else:
            lines.append(fail("Ping: ÉCHOUÉ (ICMP probablement bloqué)"))
    except (subprocess.SubprocessError, FileNotFoundError):
        lines.append(fail("Ping: commande indisponible"))

    # ── Test 2 : TCP connect ──
    lines.append(info(f"Test 2/6: TCP connect vers {target_ip}:{DIAG_TCP_PORT}..."))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((target_ip, DIAG_TCP_PORT))
        s.sendall(MAGIC + b":TCP_TEST")
        resp = s.recv(1024)
        s.close()
        if MAGIC in resp:
            lines.append(ok("TCP: CONNECTÉ et réponse reçue ✨"))
        else:
            lines.append(warn("TCP: connecté mais réponse inattendue"))
    except socket.timeout:
        lines.append(fail("TCP: timeout (le listener est-il lancé sur l'autre poste ?)"))
    except ConnectionRefusedError:
        lines.append(fail("TCP: connexion refusée (port fermé ou listener pas lancé)"))
    except OSError as e:
        lines.append(fail(f"TCP: {e}"))

    # ── Test 3 : UDP unicast ──
    lines.append(info(f"Test 3/6: UDP unicast vers {target_ip}:{DIAG_UDP_PORT}..."))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(5.0)
        s.sendto(MAGIC + b":UDP_TEST", (target_ip, DIAG_UDP_PORT))
        try:
            resp, _ = s.recvfrom(1024)
            if MAGIC in resp:
                lines.append(ok("UDP unicast: REÇU et réponse confirmée ✨"))
            else:
                lines.append(warn("UDP unicast: réponse inattendue"))
        except socket.timeout:
            lines.append(warn("UDP unicast: envoyé mais pas de réponse (peut être bloqué ou perdu)"))
        s.close()
    except OSError as e:
        lines.append(fail(f"UDP unicast: {e}"))

    # ── Test 4 : UDP broadcast (subnet) ──
    lines.append(info(f"Test 4/6: UDP broadcast..."))
    bcast_addrs_to_try = set()

    # Calculer le broadcast depuis les interfaces
    ifaces = get_broadcast_and_mask()
    for iface, ip, brd, cidr in ifaces:
        if brd:
            bcast_addrs_to_try.add(brd)

    # Aussi essayer le /24 classique de la cible
    parts = target_ip.split(".")
    bcast_addrs_to_try.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
    bcast_addrs_to_try.add("255.255.255.255")

    bcast_ok = False
    for bcast_addr in bcast_addrs_to_try:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(1.0)
            s.sendto(MAGIC + b":BCAST_TEST", (bcast_addr, DIAG_BCAST_PORT))
            lines.append(info(f"  broadcast envoyé vers {bcast_addr}:{DIAG_BCAST_PORT}"))
            s.close()
            bcast_ok = True
        except OSError as e:
            lines.append(warn(f"  broadcast vers {bcast_addr} échoué: {e}"))

    if bcast_ok:
        lines.append(ok("Broadcast: paquets envoyés (vérifier le listener)"))
    else:
        lines.append(fail("Broadcast: impossible d'envoyer"))

    # ── Test 5 : Multicast ──
    lines.append(info(f"Test 5/6: Multicast ({MCAST_GROUP}:{DIAG_MCAST_PORT})..."))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.settimeout(1.0)
        ttl = struct.pack("b", 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
        s.sendto(MAGIC + b":MCAST_TEST", (MCAST_GROUP, DIAG_MCAST_PORT))
        lines.append(ok("Multicast: paquet envoyé (vérifier le listener)"))
        s.close()
    except OSError as e:
        lines.append(fail(f"Multicast: {e}"))

    # ── Test 6 : TCP sur les ports de 42msg ──
    lines.append(info(f"Test 6/6: TCP port 42069 (discovery port de 42msg)..."))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect((target_ip, 42069))
        s.close()
        lines.append(ok("Port 42069 TCP: accessible"))
    except ConnectionRefusedError:
        lines.append(info("Port 42069 TCP: refusé (normal si 42msg n'est pas lancé)"))
    except socket.timeout:
        lines.append(warn("Port 42069 TCP: timeout (possiblement filtré)"))
    except OSError as e:
        lines.append(fail(f"Port 42069 TCP: {e}"))

    # ── Résumé ──
    lines.append(section("RÉSUMÉ CONNECTIVITÉ"))
    lines.append(info("Compare avec les résultats du --listen sur l'autre poste."))
    lines.append(info("Copie ce rapport et partage-le pour le diagnostic."))

    return lines


# ═══════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════

def save_report(lines: list[str], filename: str) -> None:
    # Strip ANSI/emoji for clean file
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"42msg diagnostic — {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Hostname: {socket.gethostname()}\n")
        f.write(f"User: {os.environ.get('USER', '?')}\n\n")
        for line in lines:
            f.write(line + "\n")
    print(f"\n📄 Rapport sauvegardé dans: {filename}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="42msg — Diagnostic réseau pour postes École 42",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Utilisation en 3 étapes :
  1. Sur les DEUX postes :  python3 scripts/diag_network.py --scan
  2. Sur le poste A :       python3 scripts/diag_network.py --listen
  3. Sur le poste B :       python3 scripts/diag_network.py --test <IP_POSTE_A>
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Infos réseau locales")
    group.add_argument("--listen", action="store_true", help="Démarrer les listeners de test")
    group.add_argument("--test", metavar="IP", help="Tester la connectivité vers un poste")

    args = parser.parse_args()

    print("=" * 60)
    print("  42msg — Diagnostic réseau")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Hostname: {socket.gethostname()}")
    print("=" * 60)

    if args.scan:
        lines = run_scan()
        for line in lines:
            print(line)
        save_report(lines, REPORT_FILE)

    elif args.listen:
        run_listen()

    elif args.test:
        lines = run_test(args.test)
        for line in lines:
            print(line)
        save_report(lines, REPORT_FILE)


if __name__ == "__main__":
    main()

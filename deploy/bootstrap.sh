#!/usr/bin/env bash
# Server-Hardening fuer dev.anlaufstelle.app (Refs #671).
#
# Zielsystem: Hetzner CX22, Debian 13. Idempotent — kann beliebig oft
# ausgefuehrt werden, kippt schon vorhandene Konfiguration nicht.
#
# Anwendung (vom Operator-Laptop oder dieser Sandbox):
#   scp deploy/bootstrap.sh root@dev.anlaufstelle.app:/root/
#   ssh root@dev.anlaufstelle.app bash /root/bootstrap.sh
#
# Benoetigt root.

set -euo pipefail
IFS=$'\n\t'

if [[ $EUID -ne 0 ]]; then
	echo "bootstrap.sh muss als root laufen." >&2
	exit 1
fi

log() { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }

# === 1) APT: Repos, Pakete ===
log "apt update + base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
	ca-certificates curl gnupg lsb-release \
	ufw fail2ban unattended-upgrades \
	restic \
	git

# === 2) Docker-CE-Repo + Engine ===
if ! command -v docker >/dev/null 2>&1; then
	log "install docker-ce"
	install -m 0755 -d /etc/apt/keyrings
	curl -fsSL https://download.docker.com/linux/debian/gpg | \
		gpg --dearmor -o /etc/apt/keyrings/docker.gpg
	chmod a+r /etc/apt/keyrings/docker.gpg
	cat >/etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable
EOF
	apt-get update -qq
	apt-get install -y -qq \
		docker-ce docker-ce-cli containerd.io \
		docker-buildx-plugin docker-compose-plugin
fi

# === 3) Docker daemon log rotation ===
log "docker daemon log rotation"
mkdir -p /etc/docker
cat >/etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
systemctl restart docker

# === 4) Service-User anlaufstelle ===
if ! id anlaufstelle >/dev/null 2>&1; then
	log "create user anlaufstelle"
	useradd --create-home --shell /bin/bash anlaufstelle
	usermod -aG docker,sudo anlaufstelle
fi

# SSH-Key vom root uebernehmen, damit auch der Service-User per Key reinkommt.
if [[ -f /root/.ssh/authorized_keys && ! -f /home/anlaufstelle/.ssh/authorized_keys ]]; then
	log "propagate root authorized_keys to anlaufstelle"
	install -d -m 0700 -o anlaufstelle -g anlaufstelle /home/anlaufstelle/.ssh
	install -m 0600 -o anlaufstelle -g anlaufstelle \
		/root/.ssh/authorized_keys /home/anlaufstelle/.ssh/authorized_keys
fi

# Passwortlos sudo fuer den Operator (idempotent ueber Drop-in).
cat >/etc/sudoers.d/anlaufstelle <<'EOF'
anlaufstelle ALL=(ALL) NOPASSWD:ALL
EOF
chmod 0440 /etc/sudoers.d/anlaufstelle

# === 5) SSH Hardening ===
log "ssh hardening"
mkdir -p /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/00-hardening.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
X11Forwarding no
EOF
# Erst SSHD neu laden, NACHDEM der anlaufstelle-User per Key reinkommt —
# sonst sperrt sich der Operator bei der allerersten Bootstrap-Iteration aus.
if [[ -f /home/anlaufstelle/.ssh/authorized_keys ]]; then
	systemctl reload ssh
fi

# === 6) ufw-docker-Helper installieren (ohne Aktivierung) ===
# Docker bypasst ufw via iptables. ufw-docker schliesst die Luecke,
# indem es after.rules / after6.rules patched. `ufw-docker install`
# selbst kommt erst weiter unten — nach `ufw enable`, weil das Tool
# einen aktiven UFW voraussetzt.
if ! command -v ufw-docker >/dev/null 2>&1; then
	log "install ufw-docker helper"
	curl -fsSL -o /usr/local/bin/ufw-docker \
		https://github.com/chaifeng/ufw-docker/raw/master/ufw-docker
	chmod +x /usr/local/bin/ufw-docker
fi

# === 7) fail2ban (sshd) ===
log "fail2ban sshd jail"
cat >/etc/fail2ban/jail.d/sshd.conf <<'EOF'
[sshd]
enabled = true
maxretry = 3
findtime = 10m
bantime = 1h
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

# === 8) Unattended Upgrades (mit nightly reboot) ===
log "unattended-upgrades + automatic reboot"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat >/etc/apt/apt.conf.d/52unattended-upgrades-local <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "03:00";
EOF

# === 9) /opt/anlaufstelle Layout ===
log "/opt/anlaufstelle layout"
install -d -m 0750 -o anlaufstelle -g anlaufstelle /opt/anlaufstelle
install -d -m 0750 -o anlaufstelle -g anlaufstelle /opt/anlaufstelle/deploy
install -d -m 0750 -o root -g root /var/backups/anl

# === 10) UFW (LETZTER Schritt — bewusst am Ende) ===
# Reihenfolge:
#   1) Default-Policies + Allow-Regeln setzen (kein Effekt — UFW noch inaktiv).
#   2) `ufw --force enable` — wendet alle Regeln in einem Schritt an;
#      Allow-22/tcp ist drin, SSH bleibt offen.
#   3) `ufw-docker install` — patched after.rules. Braucht aktives UFW.
#   4) `ufw reload` — sanftes Re-Read. NICHT `systemctl restart ufw`,
#      das kappt aktive Connections (siehe Vorfall 2026-05-08).
#
# Kommt bewusst als letzter Schritt: falls das Skript hier abbricht, hat
# der Server zumindest Docker/SSH/fail2ban/anlaufstelle-User — UFW
# nachzuziehen ist dann ein Einzeiler, kein Re-Bootstrap.

log "ufw default deny + allow 22/80/443 + enable"
ufw --force default deny incoming
ufw --force default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp  # HTTP/3
ufw --force enable

log "ufw-docker install (after.rules-Patches)"
ufw-docker install >/dev/null

log "ufw reload (sanftes Re-Read — keine Connection-Drops)"
ufw reload

log "bootstrap done. Check: ufw status verbose, systemctl status fail2ban docker"

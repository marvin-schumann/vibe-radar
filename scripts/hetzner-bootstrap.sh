#!/usr/bin/env bash
# Frequenz — Hetzner CX22 one-shot bootstrap.
#
# Run this once on a fresh Ubuntu 24.04 Hetzner Cloud server, immediately after
# first SSH login as root. Replaces ~30 min of manual hardening + Coolify
# install with a single command.
#
# Usage (from your laptop):
#   scp scripts/hetzner-bootstrap.sh root@<HETZNER_IP>:/root/
#   ssh root@<HETZNER_IP> "bash /root/hetzner-bootstrap.sh frequenz '<your-ssh-public-key>'"
#
# The script will print a fail2ban + ufw status block at the end. After it
# finishes, you should be able to SSH in as the new user instead of root, and
# the Coolify UI is reachable at http://<HETZNER_IP>:8000.
#
# Idempotent: safe to re-run if it fails partway through.

set -euo pipefail

NEW_USER="${1:-frequenz}"
SSH_PUBKEY="${2:-}"

if [[ -z "${SSH_PUBKEY}" ]]; then
    echo "ERROR: SSH public key required as second argument."
    echo "Usage: bash hetzner-bootstrap.sh <username> '<ssh-public-key>'"
    exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: must be run as root."
    exit 1
fi

log() { echo -e "\n\033[1;32m== $* ==\033[0m"; }

# ──────────────────────────────────────────────────────────────────────────
# 1. System update + base packages
# ──────────────────────────────────────────────────────────────────────────
log "Updating apt + installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"
apt-get install -y \
    ufw \
    fail2ban \
    unattended-upgrades \
    curl \
    ca-certificates \
    htop \
    git \
    rsync

# Auto security updates
log "Enabling unattended security upgrades"
echo 'APT::Periodic::Update-Package-Lists "1";' > /etc/apt/apt.conf.d/20auto-upgrades
echo 'APT::Periodic::Unattended-Upgrade "1";' >> /etc/apt/apt.conf.d/20auto-upgrades

# ──────────────────────────────────────────────────────────────────────────
# 2. Enable 2 GB swap (prevents numpy/Pillow OOM during first Docker build)
# ──────────────────────────────────────────────────────────────────────────
if [[ ! -f /swapfile ]]; then
    log "Creating 2 GB swap file"
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    if ! grep -q '^/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    # Lower swappiness so swap is only used under real pressure
    echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
    sysctl -p /etc/sysctl.d/99-swappiness.conf
else
    log "Swap file already present, skipping"
fi

# ──────────────────────────────────────────────────────────────────────────
# 3. Firewall (ufw)
# ──────────────────────────────────────────────────────────────────────────
log "Configuring ufw"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp comment 'HTTP (Coolify Traefik)'
ufw allow 443/tcp comment 'HTTPS (Coolify Traefik)'
ufw allow 8000/tcp comment 'Coolify UI - close after first login'
ufw --force enable

# ──────────────────────────────────────────────────────────────────────────
# 4. fail2ban defaults are good for SSH; just ensure it's running
# ──────────────────────────────────────────────────────────────────────────
log "Enabling fail2ban"
systemctl enable --now fail2ban

# ──────────────────────────────────────────────────────────────────────────
# 5. Create non-root user with passwordless sudo + SSH key
# ──────────────────────────────────────────────────────────────────────────
if id "${NEW_USER}" &>/dev/null; then
    log "User ${NEW_USER} already exists, skipping creation"
else
    log "Creating user ${NEW_USER}"
    adduser --disabled-password --gecos '' "${NEW_USER}"
    usermod -aG sudo "${NEW_USER}"
    echo "${NEW_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-${NEW_USER}
    chmod 440 /etc/sudoers.d/90-${NEW_USER}
fi

log "Installing SSH public key for ${NEW_USER}"
USER_HOME="/home/${NEW_USER}"
mkdir -p "${USER_HOME}/.ssh"
echo "${SSH_PUBKEY}" > "${USER_HOME}/.ssh/authorized_keys"
chmod 700 "${USER_HOME}/.ssh"
chmod 600 "${USER_HOME}/.ssh/authorized_keys"
chown -R "${NEW_USER}:${NEW_USER}" "${USER_HOME}/.ssh"

# ──────────────────────────────────────────────────────────────────────────
# 6. Lock down SSH (disable root + password auth)
# ──────────────────────────────────────────────────────────────────────────
log "Hardening sshd"
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%s)
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' /etc/ssh/sshd_config
sed -i 's/^#*ChallengeResponseAuthentication.*/ChallengeResponseAuthentication no/' /etc/ssh/sshd_config
# Drop in a sanity-check file too in case the main file is overridden later
cat > /etc/ssh/sshd_config.d/99-frequenz.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
PermitEmptyPasswords no
ChallengeResponseAuthentication no
EOF
systemctl restart ssh

# ──────────────────────────────────────────────────────────────────────────
# 7. Install Coolify
# ──────────────────────────────────────────────────────────────────────────
if [[ -d /data/coolify ]]; then
    log "Coolify already installed, skipping"
else
    log "Installing Coolify (this takes ~5-10 min, downloads + starts containers)"
    curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
fi

# ──────────────────────────────────────────────────────────────────────────
# 8. Final status
# ──────────────────────────────────────────────────────────────────────────
log "Bootstrap complete. Final status:"
echo
echo "── ufw ──"
ufw status verbose
echo
echo "── fail2ban ──"
systemctl is-active fail2ban
echo
echo "── swap ──"
free -h | grep Swap
echo
echo "── Coolify ──"
docker ps --filter "name=coolify" --format "{{.Names}}\t{{.Status}}" 2>/dev/null || echo "(coolify containers starting...)"
echo
echo "✓ Done."
echo
echo "Next steps:"
echo "  1. From your laptop: ssh ${NEW_USER}@$(hostname -I | awk '{print $1}')"
echo "     (Verify the new user works BEFORE closing this session.)"
echo "  2. Open http://$(hostname -I | awk '{print $1}'):8000 in your browser"
echo "  3. Set a strong Coolify admin password"
echo "  4. After first Coolify login: sudo ufw delete allow 8000/tcp"
echo "     (Coolify proxies through 80/443 once a domain is configured.)"

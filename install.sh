#!/bin/sh
# Turntable-1: install, or update. Run it again any time to update to the latest release.
#
#   curl -fsSL https://github.com/TorinML/turntable-1/releases/latest/download/install.sh | sh
#
# Options, as environment variables:
#   TURNTABLE_DIR=~/turntable-1   where it lives
#   ASSUME_YES=1                   answer yes to every question
#
# It installs Docker if needed, sets up the folder and .env, brings an existing (non-Docker)
# Turntable-1 install across if it finds one, then starts the station.
set -eu

REPO="TorinML/turntable-1"
DIR="${TURNTABLE_DIR:-$HOME/turntable-1}"
BASE="https://github.com/$REPO/releases/latest/download"
HERE=$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)
WAIT="${WAIT_SECONDS:-120}"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
info() { printf '  %s\n' "$*"; }
warn() { printf '  \033[33m%s\033[0m\n' "$*"; }
ask() {                                  # ask "question" -> yes (0) or no (1); default yes
  [ "${ASSUME_YES:-}" = "1" ] && return 0
  printf '  %s [Y/n] ' "$1"
  ans=""
  read -r ans < /dev/tty 2>/dev/null || true
  case "$ans" in [Nn]*) return 1 ;; *) return 0 ;; esac
}
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

bold "Turntable-1"

# --- 1. Docker ---------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  ask "Docker isn't installed. Install it now (from get.docker.com)?" || { warn "Turntable-1 needs Docker."; exit 1; }
  curl -fsSL https://get.docker.com | $SUDO sh
  $SUDO usermod -aG docker "$(id -un)" 2>/dev/null || true
  info "Docker installed. (Log out and back in to use it without sudo.)"
fi
DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="$SUDO docker"
if ! $DOCKER compose version >/dev/null 2>&1; then
  warn "Docker Compose isn't available. Install the compose plugin (docker-compose-plugin) and run this again."
  exit 1
fi

# --- 2. the folder, the Compose file and .env ------------------------------------------------------------
mkdir -p "$DIR" && cd "$DIR"
fetch() {                                # from beside this script (a checkout), or the latest release
  if [ -n "$HERE" ] && [ "$HERE" != "$DIR" ] && [ -f "$HERE/$1" ]; then cp "$HERE/$1" "$1"
  else curl -fsSL "$BASE/$1" -o "$1"; fi
}
fetch docker-compose.yml                 # holds no choices, so it's always refreshed
FIRST=""
if [ ! -f .env ]; then
  FIRST=1
  fetch .env.example && cp .env.example .env
  tz=$(timedatectl show -p Timezone --value 2>/dev/null || true)
  [ -n "$tz" ] || tz=$(cat /etc/timezone 2>/dev/null || true)
  [ -n "$tz" ] || tz=$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##' || true)
  gid=$(getent group audio 2>/dev/null | cut -d: -f3 || true)
  sed -i "s#^TZ=.*#TZ=${tz:-UTC}#; s#^AUDIO_GID=.*#AUDIO_GID=${gid:-29}#" .env
  info "Created .env (time zone ${tz:-UTC}). Your choices for Docker live there."
fi
set -a; . ./.env; set +a
CONFIG_DIR="${CONFIG_DIR:-./config}"; DATA_DIR="${DATA_DIR:-./data}"; ARCHIVE_DIR="${ARCHIVE_DIR:-./archive}"; HTTP_PORT="${HTTP_PORT:-8080}"
mkdir -p "$CONFIG_DIR" "$DATA_DIR"
[ -d "$ARCHIVE_DIR" ] || mkdir -p "$ARCHIVE_DIR"
# the containers run as user 1000 (a NAS archive folder is left as it is)
for d in "$CONFIG_DIR" "$DATA_DIR" $(case "$ARCHIVE_DIR" in ./*) echo "$ARCHIVE_DIR" ;; esac); do
  [ "$(stat -c %u "$d")" = "1000" ] || $SUDO chown -R 1000:1000 "$d"
done

# --- 3. an existing install on this machine? ------------------------------------------------------------
MIGRATED=""
if [ ! -f "$CONFIG_DIR/turntable.toml" ] && { [ -f /etc/turntable/turntable.toml ] || [ -f /etc/turntable-pet.toml ]; }; then
  bold "An existing Turntable-1 install"
  if ask "Bring its listening history, collection, Beepnik and settings across? (The old install is stopped, not removed.)"; then
    $SUDO systemctl stop 'turntable-*' icecast2 2>/dev/null || true
    info "Copying your data (this can take a minute)..."
    $SUDO cp -a /var/lib/turntable/. "$DATA_DIR"/
    mkdir -p legacy
    $SUDO cp -a /etc/turntable legacy/ 2>/dev/null || true
    $SUDO cp /etc/turntable-pet.toml /etc/turntable-id.env legacy/ 2>/dev/null || true
    $SUDO sh -c 'cp /etc/systemd/system/turntable-*.service "$1"/' _ legacy 2>/dev/null || true
    $SUDO chown -R 1000:1000 "$DATA_DIR" legacy
    if mountpoint -q /mnt/turntable-nas 2>/dev/null; then
      sed -i "s#^ARCHIVE_DIR=.*#ARCHIVE_DIR=/mnt/turntable-nas#" .env && ARCHIVE_DIR=/mnt/turntable-nas
      info "Your album archive stays on /mnt/turntable-nas."
    fi
    $DOCKER compose run --rm --no-deps -v "$PWD/legacy:/legacy" core migrate
    MIGRATED=1
  fi
fi

# --- 4. the turntable ---------------------------------------------------------------------------------------
bold "Audio"
if grep -qi "usb" /proc/asound/cards 2>/dev/null; then
  info "Found USB audio: $(grep -i -m1 'usb' /proc/asound/cards | sed 's/^ *//')"
else
  warn "No USB audio device found yet. Plug in the turntable and switch it on; the station finds it on its own."
fi

# --- 5. start ---------------------------------------------------------------------------------------------
bold "Starting"
if ! $DOCKER compose pull -q; then
  warn "Couldn't pull the image. If it's private, sign Docker in to GitHub first:"
  warn "  echo YOUR_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin"
  exit 1
fi
$DOCKER compose up -d --remove-orphans
$DOCKER image prune -f >/dev/null 2>&1 || true

printf '  Waiting for the site'
i=0
until curl -fsS "http://127.0.0.1:$HTTP_PORT/api/now" >/dev/null 2>&1; do
  i=$((i + 2)); [ "$i" -ge "$WAIT" ] && { printf '\n'; warn "The site hasn't answered yet. Check: docker compose logs web"; break; }
  printf '.'; sleep 2
done
printf '\n'

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
bold "Done"
info "Open http://${ip:-localhost}:$HTTP_PORT"
if [ -n "$FIRST" ] && [ -z "$MIGRATED" ]; then
  info "Click the gear at the top, create the admin password, and add your Discogs details."
fi
if [ -n "$MIGRATED" ]; then
  info "Your history and settings came across. At the gear, sign in with your admin password (or create one, if you hadn't yet)."
  if ask "Stop the old install from starting at boot?"; then
    $SUDO systemctl disable 'turntable-*' icecast2 >/dev/null 2>&1 || true
    info "Done. To go back: docker compose down, then sudo systemctl enable --now icecast2 'turntable-*'"
  fi
fi
info "To update later, run this installer again."

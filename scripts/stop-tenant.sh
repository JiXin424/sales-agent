#!/usr/bin/env bash
# 停止 sales-agent 租户服务（交互式箭头键选择）。
# 用法: scripts/stop-tenant.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ── ANSI helpers ──
CURSOR_HIDE=$'\e[?25l'
CURSOR_SHOW=$'\e[?25h'
CLEAR_LINE=$'\e[2K'
CURSOR_UP_N() { printf '\e[%sA' "$1"; }

usage() {
  cat <<USAGE
Usage: scripts/stop-tenant.sh [OPTIONS]

Stop sales-agent tenant containers interactively.

Options:
  --tenant ID   Stop a specific tenant directly (no menu).
                Use "all" to stop all tenants, "everything" for tenants + db + traefik.
  --yes         Skip confirmation prompt.
  -h, --help    Show this help.
USAGE
}

SKIP_CONFIRM=0
DIRECT_TENANT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --yes)       SKIP_CONFIRM=1 ;;
    --tenant)    shift; DIRECT_TENANT="$1" ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# ── Discover running tenants ──
discover_tenants() {
  docker ps --format '{{.Names}}' 2>/dev/null \
    | command grep '^sales-agent-' \
    | command grep -E '\-(api|worker|stream)$' \
    | sed 's/^sales-agent-//;s/-api$//;s/-worker$//;s/-stream$//' \
    | sort -u
}

RUNNING_TENANTS=()
while IFS= read -r t; do
  [ -n "$t" ] && RUNNING_TENANTS+=("$t")
done < <(discover_tenants)

# ── Non-interactive: --tenant specified ──
if [ -n "$DIRECT_TENANT" ]; then
  case "$DIRECT_TENANT" in
    all)
      if [ ${#RUNNING_TENANTS[@]} -eq 0 ]; then
        echo "No sales-agent tenant containers running."
        exit 0
      fi
      SELECTED=("${RUNNING_TENANTS[@]}")
      STOP_DB_TRAEFIK=0
      ;;
    everything)
      SELECTED=("${RUNNING_TENANTS[@]}")
      STOP_DB_TRAEFIK=1
      ;;
    *)
      SELECTED=("$DIRECT_TENANT")
      STOP_DB_TRAEFIK=0
      ;;
  esac
else
  # ── Interactive menu ──
  if [ ${#RUNNING_TENANTS[@]} -eq 0 ]; then
    echo "No sales-agent tenant containers running."
    exit 0
  fi

  # Build menu items
  MENU_ITEMS=()
  for tid in "${RUNNING_TENANTS[@]}"; do
    # Count containers for this tenant
    count=$(docker ps --format '{{.Names}}' 2>/dev/null | command grep "^sales-agent-${tid}-" | wc -l)
    MENU_ITEMS+=("${tid}  (${count} containers)")
  done
  MENU_ITEMS+=("All tenants (keep db/traefik)")
  MENU_ITEMS+=("Everything (including db/traefik)")
  MENU_ITEMS+=("Quit")

  selected=0
  total=${#MENU_ITEMS[@]}

  draw_menu() {
    if [ "$1" -gt 0 ]; then
      CURSOR_UP_N "$1"
    fi
    for idx in "${!MENU_ITEMS[@]}"; do
      printf '%s' "$CLEAR_LINE"
      if [ "$idx" -eq "$selected" ]; then
        printf ' \e[7m  ▶ %s  \e[0m\n' "${MENU_ITEMS[$idx]}"
      else
        printf '    %s\n' "${MENU_ITEMS[$idx]}"
      fi
    done
  }

  printf '%s' "$CURSOR_HIDE"
  echo
  echo "  Found ${#RUNNING_TENANTS[@]} tenant(s) running.  ↑↓ to select, Enter to confirm:"
  echo
  draw_menu 0

  while true; do
    IFS= read -rsn1 key
    if [ "$key" = $'\x1b' ]; then
      read -rsn2 -t 0.01 rest || true
      key+="$rest"
    fi

    case "$key" in
      $'\x1b[A') # Up
        [ "$selected" -gt 0 ] && ((selected--))
        draw_menu "$total"
        ;;
      $'\x1b[B') # Down
        [ "$selected" -lt $((total - 1)) ] && ((selected++))
        draw_menu "$total"
        ;;
      q|Q)
        printf '%s' "$CURSOR_SHOW"
        echo; echo "Aborted."; exit 0
        ;;
      '') # Enter
        printf '%s' "$CURSOR_SHOW"
        echo; echo; break
        ;;
    esac
  done

  # Resolve selection
  if [ "$selected" -eq $((total - 1)) ]; then
    echo "Aborted."; exit 0
  elif [ "$selected" -eq $((total - 2)) ]; then
    SELECTED=("${RUNNING_TENANTS[@]}")
    STOP_DB_TRAEFIK=1
  elif [ "$selected" -eq $((total - 3)) ]; then
    SELECTED=("${RUNNING_TENANTS[@]}")
    STOP_DB_TRAEFIK=0
  else
    SELECTED=("${RUNNING_TENANTS[$selected]}")
    STOP_DB_TRAEFIK=0
  fi
fi

# ── Show what will be stopped ──
echo "Will stop:"
for tid in "${SELECTED[@]}"; do
  echo "  tenant: $tid"
  docker ps --format '{{.Names}}' 2>/dev/null | command grep "^sales-agent-${tid}-" | while read -r c; do
    echo "    - $c"
  done
done
if [ "$STOP_DB_TRAEFIK" -eq 1 ]; then
  echo "  shared: sales-agent-db, sales-agent-traefik"
fi

# ── Confirmation ──
if [ "$SKIP_CONFIRM" -ne 1 ]; then
  echo
  read -r -p "Type STOP to confirm: " answer
  if [ "$answer" != "STOP" ]; then
    echo "Aborted."
    exit 0
  fi
fi

# ── Stop containers ──
echo
for tid in "${SELECTED[@]}"; do
  containers=$(docker ps --format '{{.Names}}' 2>/dev/null | command grep "^sales-agent-${tid}-" || true)
  if [ -n "$containers" ]; then
    echo "Stopping $tid containers..."
    for c in $containers; do
      echo "  stopping $c"
      docker stop "$c" 2>/dev/null || true
      docker rm "$c" 2>/dev/null || true
    done
  fi
done

if [ "$STOP_DB_TRAEFIK" -eq 1 ]; then
  for svc in sales-agent-db sales-agent-traefik; do
    if docker ps --format '{{.Names}}' 2>/dev/null | command grep -q "^${svc}$"; then
      echo "Stopping $svc"
      docker stop "$svc" 2>/dev/null || true
      docker rm "$svc" 2>/dev/null || true
    fi
  done
fi

echo
echo "Done. Remaining sales-agent containers:"
docker ps --format '{{.Names}}' 2>/dev/null | command grep '^sales-agent-' || echo "  (none)"

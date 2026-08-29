#!/usr/bin/env bash
# Patch one or more libvirt (QEMU/KVM) Debian/Ubuntu guests and restore their
# original power state.
#
# Per VM: record prior power state -> start if needed -> resolve IP -> wait for
# SSH -> apt update/upgrade/autopurge -> fstrim -> poweroff (only if the VM was
# off before we touched it).
#
# VMs are processed in parallel; each one writes its own log.

set -uo pipefail

URI="qemu:///system"
SSH_USER="${USER}"
NET=""                 # libvirt network to read leases from; auto-detected per VM
LOG_DIR=""
CHECK_ONLY=0
POWEROFF_MODE="restore"   # restore | all | none
BOOT_TIMEOUT=300          # seconds to wait for lease + sshd
OFF_TIMEOUT=180           # seconds to wait for a clean shutdown
VMS=()

usage() {
    cat <<'EOF'
Usage: vm-apt-maintenance.sh [options] VM [VM ...]

Options:
  --check, --dry-run     Verify only: start the VM if needed, resolve its IP,
                         confirm SSH and passwordless sudo, report how many
                         upgrades are pending, then restore its power state.
                         Touches no packages. Note this DOES boot a VM that is
                         off, because that is the only way to reach it.
  --user USER            SSH user for the guests (default: $USER).
  --connect URI          libvirt URI (default: qemu:///system).
  --network NAME         libvirt network to read DHCP leases from
                         (default: taken from each VM's interface).
  --poweroff-all         Power off every VM at the end, even ones that were
                         already running before this ran.
  --no-poweroff          Leave every VM running at the end.
  --log-dir DIR          Where to write per-VM logs (default: a mktemp dir).
  --boot-timeout SECS    Seconds to wait for DHCP lease + SSH (default: 300).
  --off-timeout SECS     Seconds to wait for a clean shutdown (default: 180).
  -h, --help             This message.

Exit status is 0 only if every VM completed its maintenance chain.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --check|--dry-run) CHECK_ONLY=1; shift ;;
        --user)          SSH_USER="$2"; shift 2 ;;
        --connect)       URI="$2"; shift 2 ;;
        --network)       NET="$2"; shift 2 ;;
        --poweroff-all)  POWEROFF_MODE="all"; shift ;;
        --no-poweroff)   POWEROFF_MODE="none"; shift ;;
        --log-dir)       LOG_DIR="$2"; shift 2 ;;
        --boot-timeout)  BOOT_TIMEOUT="$2"; shift 2 ;;
        --off-timeout)   OFF_TIMEOUT="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        -*)              echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
        *)               VMS+=("$1"); shift ;;
    esac
done

[ ${#VMS[@]} -gt 0 ] || { echo "error: name at least one VM" >&2; usage >&2; exit 2; }

if [ -z "$LOG_DIR" ]; then
    LOG_DIR="$(mktemp -d -t vm-apt-maint-XXXXXX)"
fi
mkdir -p "$LOG_DIR"

v() { virsh -c "$URI" "$@"; }

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
rsh() { ssh "${SSH_OPTS[@]}" "${SSH_USER}@$1" "$2"; }

# Run a multi-line script on the guest, fed on stdin to an explicit bash.
# sshd hands a plain command string to the user's *login* shell, which is often
# zsh - and zsh does not word-split unquoted variables, so a script written with
# bash assumptions silently mangles its own arguments. Naming the interpreter
# removes that whole class of surprise.
rsh_script() { ssh "${SSH_OPTS[@]}" "${SSH_USER}@$1" 'bash -s'; }

# --- helpers ---------------------------------------------------------------

domain_exists() { v domstate "$1" >/dev/null 2>&1; }

# MAC of the VM's first interface, and the network it is attached to.
vm_mac()     { v dumpxml "$1" 2>/dev/null | grep -oPm1 "(?<=<mac address=')[^']+"; }
vm_network() { v dumpxml "$1" 2>/dev/null | grep -oPm1 "(?<=<source network=')[^']+"; }

# Resolve a VM's IPv4. The guest agent is authoritative when installed; the
# DHCP lease table is the fallback that works on a plain NAT network.
resolve_ip() {
    local vm="$1" mac net ip
    ip=$(v domifaddr "$vm" --source agent 2>/dev/null \
         | awk '$3=="ipv4"{print $4}' | cut -d/ -f1 | grep -v '^127\.' | head -1)
    [ -n "$ip" ] && { echo "$ip"; return 0; }

    mac=$(vm_mac "$vm") || return 1
    [ -n "$mac" ] || return 1
    net="${NET:-$(vm_network "$vm")}"
    [ -n "$net" ] || return 1
    v net-dhcp-leases "$net" 2>/dev/null \
        | awk -v m="$mac" '$0 ~ m {print $5}' | cut -d/ -f1 | head -1
}

wait_for_ip() {
    local vm="$1" deadline=$(( SECONDS + BOOT_TIMEOUT )) ip
    while [ $SECONDS -lt $deadline ]; do
        ip=$(resolve_ip "$vm")
        [ -n "$ip" ] && { echo "$ip"; return 0; }
        sleep 5
    done
    return 1
}

wait_for_ssh() {
    local ip="$1" deadline=$(( SECONDS + BOOT_TIMEOUT ))
    while [ $SECONDS -lt $deadline ]; do
        rsh "$ip" 'true' >/dev/null 2>&1 && return 0
        sleep 5
    done
    return 1
}

wait_for_off() {
    local vm="$1" deadline=$(( SECONDS + OFF_TIMEOUT ))
    while [ $SECONDS -lt $deadline ]; do
        [ "$(v domstate "$vm" 2>/dev/null)" = "shut off" ] && return 0
        sleep 5
    done
    return 1
}

# Pull the numbers we report out of an apt/fstrim log.
# apt-get prints a "N upgraded, N newly installed, N to remove" summary for each
# invocation: the first one comes from the upgrade, the last from the autoremove.
summarize() {
    local log="$1" pat='^\d+ upgraded, \d+ newly installed, \d+ to remove'
    local first last up new rem trim reboot
    first=$(grep -oPm1 "$pat" "$log" 2>/dev/null)
    last=$(grep -oP "$pat" "$log" 2>/dev/null | tail -1)
    up=$(printf '%s' "$first"  | grep -oP '^\d+')
    new=$(printf '%s' "$first" | grep -oP '\d+(?= newly)')
    rem=$(printf '%s' "$last"  | grep -oP '\d+(?= to remove)')
    trim=$(grep -c 'trimmed' "$log" 2>/dev/null)
    reboot=$(grep -c 'REBOOT_REQUIRED' "$log" 2>/dev/null)
    echo "${up:-?}|${new:-?}|${rem:-?}|${trim:-0}|${reboot:-0}"
}

# --- per-VM worker ---------------------------------------------------------

# Writes one line to $LOG_DIR/<vm>.status:
#   vm|prior_state|ip|final_state|chain_rc|upgraded|new|removed|trimmed_fs|reboot_required
maintain() {
    local vm="$1"
    local log="$LOG_DIR/${vm}.log"
    local status="$LOG_DIR/${vm}.status"
    local prior ip rc final

    prior=$(v domstate "$vm" 2>/dev/null)
    if [ -z "$prior" ]; then
        echo "$vm|missing|-|-|127|-|-|-|-|-" > "$status"
        echo "ERROR: no such domain: $vm" | tee -a "$log" >&2
        return 1
    fi

    if [ "$prior" != "running" ]; then
        echo "[$vm] starting (was: $prior)" | tee -a "$log"
        v start "$vm" >>"$log" 2>&1 || {
            echo "$vm|$prior|-|$prior|1|-|-|-|-|-" > "$status"
            echo "ERROR: could not start $vm" | tee -a "$log" >&2
            return 1
        }
    else
        echo "[$vm] already running - will be left running unless --poweroff-all" | tee -a "$log"
    fi

    echo "[$vm] waiting for an IP address" | tee -a "$log"
    ip=$(wait_for_ip "$vm") || {
        echo "$vm|$prior|-|$(v domstate "$vm")|1|-|-|-|-|-" > "$status"
        echo "ERROR: $vm got no DHCP lease within ${BOOT_TIMEOUT}s" | tee -a "$log" >&2
        return 1
    }
    echo "[$vm] ip=$ip - waiting for sshd" | tee -a "$log"
    wait_for_ssh "$ip" || {
        echo "$vm|$prior|$ip|$(v domstate "$vm")|1|-|-|-|-|-" > "$status"
        echo "ERROR: no SSH on $vm ($ip) within ${BOOT_TIMEOUT}s as $SSH_USER" | tee -a "$log" >&2
        return 1
    }

    # Fail fast rather than letting a sudo password prompt hang the run.
    if ! rsh "$ip" 'sudo -n true' >/dev/null 2>&1; then
        echo "$vm|$prior|$ip|$(v domstate "$vm")|1|-|-|-|-|-" > "$status"
        echo "ERROR: $SSH_USER needs a sudo password on $vm ($ip); unattended patching not possible" \
            | tee -a "$log" >&2
        return 1
    fi

    local fields
    if [ "$CHECK_ONLY" -eq 1 ]; then
        echo "[$vm] check only - reachable at $ip, sudo works, no packages touched" | tee -a "$log"
        # Simulated upgrade against the cached index - deliberately does not run
        # apt-get update, since "check" promises to change nothing on the guest.
        rsh_script "$ip" >>"$log" 2>&1 <<'REMOTE'
. /etc/os-release 2>/dev/null
echo "guest: ${PRETTY_NAME:-unknown}"
n=$(sudo -n apt-get -s -qq --with-new-pkgs upgrade 2>/dev/null | grep -c '^Inst ')
echo "pending upgrades (per cached index, may be stale): $n"
REMOTE
        rc=0
        fields="-|-|-|0|0"
    else
        echo "[$vm] running apt maintenance" | tee -a "$log"
        # apt-get, not apt: same effect, stable output, no "unstable CLI" warning.
        #   apt upgrade    == apt-get upgrade --with-new-pkgs
        #   apt autopurge  == apt-get autoremove --purge
        # noninteractive + the dpkg options keep a conffile prompt from hanging the run.
        rsh_script "$ip" >>"$log" 2>&1 <<'REMOTE'
export DEBIAN_FRONTEND=noninteractive
# --force-conf* keeps a changed config file from turning into an interactive
# "keep or replace?" prompt that would stall the whole unattended run.
APTOPTS="-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"
sudo -E apt-get update &&
sudo -E apt-get $APTOPTS --with-new-pkgs upgrade &&
sudo -E apt-get $APTOPTS --purge autoremove &&
sudo fstrim -av
rc=$?
[ -f /var/run/reboot-required ] && echo REBOOT_REQUIRED
exit $rc
REMOTE
        rc=$?
        echo "CHAIN_EXIT=$rc" >> "$log"
        fields=$(summarize "$log")
    fi

    # Power state: leave a VM the way we found it. Someone may be using a VM
    # that was already running, so shutting it down would be a surprise.
    local want_off=0
    case "$POWEROFF_MODE" in
        all)     want_off=1 ;;
        none)    want_off=0 ;;
        restore) [ "$prior" != "running" ] && want_off=1 ;;
    esac

    # A guest whose apt chain failed stays up: its dpkg state is unverified and
    # the user will want to look at it.
    if [ "$rc" -ne 0 ] && [ "$want_off" -eq 1 ]; then
        echo "[$vm] chain failed (rc=$rc) - leaving it running for inspection" | tee -a "$log"
        want_off=0
    fi

    if [ "$want_off" -eq 1 ]; then
        echo "[$vm] powering off" | tee -a "$log"
        # The connection dies with the guest, so a nonzero ssh status is normal.
        rsh "$ip" 'sudo systemctl poweroff' >>"$log" 2>&1
        if ! wait_for_off "$vm"; then
            echo "[$vm] still up after ${OFF_TIMEOUT}s, sending ACPI shutdown" | tee -a "$log"
            v shutdown "$vm" >>"$log" 2>&1
            wait_for_off "$vm" || echo "WARNING: $vm did not shut down" | tee -a "$log" >&2
        fi
    fi

    final=$(v domstate "$vm" 2>/dev/null)
    printf '%s|%s|%s|%s|%s|%s\n' "$vm" "$prior" "$ip" "$final" "$rc" "$fields" > "$status"
    return $rc
}

# --- run -------------------------------------------------------------------

echo "libvirt URI : $URI"
echo "ssh user    : $SSH_USER"
echo "poweroff    : $POWEROFF_MODE$([ "$CHECK_ONLY" -eq 1 ] && echo ' (check only)')"
echo "logs        : $LOG_DIR"
echo "VMs         : ${VMS[*]}"
echo

missing=0
for vm in "${VMS[@]}"; do
    domain_exists "$vm" || { echo "error: no such domain: $vm" >&2; missing=1; }
done
if [ "$missing" -eq 1 ]; then
    echo >&2 "known domains:"; v list --all >&2
    exit 2
fi

for vm in "${VMS[@]}"; do
    maintain "$vm" &
done
wait

# --- report ----------------------------------------------------------------

echo
printf '%-28s %-10s %-16s %-10s %-6s %-9s %-8s %-8s %s\n' \
    VM WAS IP NOW EXIT UPGRADED NEW REMOVED NOTES
overall=0
for vm in "${VMS[@]}"; do
    st="$LOG_DIR/${vm}.status"
    [ -f "$st" ] || { printf '%-28s %s\n' "$vm" "no status written"; overall=1; continue; }
    IFS='|' read -r n prior ip final rc up new rem trim reboot < "$st"
    notes=""
    [ "${trim:-0}" != "0" ] && notes="trimmed ${trim} fs"
    [ "${reboot:-0}" != "0" ] && notes="${notes}${notes:+, }reboot-required"
    [ "$rc" != "0" ] && { notes="${notes}${notes:+, }FAILED - see $LOG_DIR/${vm}.log"; overall=1; }
    printf '%-28s %-10s %-16s %-10s %-6s %-9s %-8s %-8s %s\n' \
        "$n" "$prior" "$ip" "$final" "$rc" "$up" "$new" "$rem" "$notes"
done

echo
echo "logs: $LOG_DIR"
exit $overall

#!/bin/sh
set -eu
: "${MBUNI_SEND_TPS:=600}"
: "${MBUNI_MAX_SEND_ATTEMPTS:=100}"
: "${SEND_ATTEMPT_BACK_OFF_SECONDS:=2}"
for setting in "$MBUNI_SEND_TPS" "$MBUNI_MAX_SEND_ATTEMPTS" "$SEND_ATTEMPT_BACK_OFF_SECONDS"; do
    case "$setting" in ''|*[!0-9]*) echo "Mbuni demo rate and retry settings must be positive integers" >&2; exit 1;; esac
    [ "$setting" -gt 0 ] || { echo "Mbuni demo rate and retry settings must be positive" >&2; exit 1; }
done
export MBUNI_SEND_TPS MBUNI_MAX_SEND_ATTEMPTS SEND_ATTEMPT_BACK_OFF_SECONDS
mkdir -p /var/log/mbuni /var/spool/mbuni
envsubst '${MBUNI_SEND_TPS} ${MBUNI_MAX_SEND_ATTEMPTS} ${SEND_ATTEMPT_BACK_OFF_SECONDS}' < /etc/mbuni/mmsbox.conf.template > /tmp/mmsbox.conf
exec /opt/mbuni/bin/mmsbox /tmp/mmsbox.conf

#!/bin/sh
set -eu
# No client credentials are needed to confirm that Garnet can complete a TLS handshake.
exec timeout 40 sh -c '
    until timeout 3 openssl s_client -brief -connect 127.0.0.1:6379 -servername localhost </dev/null >/dev/null 2>&1
    do
        sleep 0.25
    done'

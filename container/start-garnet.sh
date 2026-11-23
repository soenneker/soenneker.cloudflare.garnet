#!/bin/sh
set -eu
umask 077
: "${GARNET_PASSWORD:?A Garnet password is required}"
: "${TLS_CERT_PEM:?A TLS certificate is required}"
: "${TLS_KEY_PEM:?A TLS key is required}"
memory=${GARNET_MEMORY_MIB:-512}
case "$memory" in
    0*|*[!0-9]*) echo "GARNET_MEMORY_MIB must be a positive integer" >&2; exit 1 ;;
esac

cd /opt/garnet-runtime
printf '%s\n' "$TLS_CERT_PEM" > tls.pem
printf '%s\n' "$TLS_KEY_PEM" > tls.key
# Garnet reads the leaf from the PEM. Give SslStream the rest of the chain for the handshake.
issuers="$PWD/issuers"
mkdir -p "$issuers"
rm -f "$issuers"/*.pem
awk -v dir="$issuers" '/-----BEGIN CERTIFICATE-----/ { n++ } n > 1 { print > (dir "/" n ".pem") }' tls.pem
openssl rehash "$issuers"
export SSL_CERT_DIR="$issuers:/etc/ssl/certs"

# Garnet ACL files accept SHA-256 password digests with a leading '#'.
digest=$(printf '%s' "$GARNET_PASSWORD" | sha256sum)
printf 'user default on #%s +@read +@write +@connection +@transaction +@pubsub +@scripting -@admin -@dangerous +info +time +eval +evalsha +script|load\n' "${digest%% *}" > users.acl
unset GARNET_PASSWORD TLS_CERT_PEM TLS_KEY_PEM digest

exec /app/GarnetServer --bind 0.0.0.0 --port 6379 --tls \
    --cert-file-name "$PWD/tls.pem" --cert-password "$PWD/tls.key" --client-certificate-required false \
    --auth ACL --acl-file "$PWD/users.acl" --lua --memory "${memory}m" --index 16m

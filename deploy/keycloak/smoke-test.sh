#!/usr/bin/env bash
# Start the optimized image against a disposable PostgreSQL service and verify
# the HTTP listener and issuer URL behind forwarded TLS headers.
set -euo pipefail

image=${1:?usage: $0 IMAGE}
: "${KC_DB_URL:?set KC_DB_URL to the disposable PostgreSQL database}"
: "${KC_DB_USERNAME:?set KC_DB_USERNAME}"
: "${KC_DB_PASSWORD:?set KC_DB_PASSWORD}"
: "${KC_HOSTNAME:?set KC_HOSTNAME to the expected HTTPS issuer origin}"

container=$(docker run -d --rm -p 127.0.0.1::8080 \
  -e KC_DB_URL -e KC_DB_USERNAME -e KC_DB_PASSWORD -e KC_HOSTNAME \
  -e KC_BOOTSTRAP_ADMIN_USERNAME="smoke-admin" \
  -e KC_BOOTSTRAP_ADMIN_PASSWORD="smoke-password-change-me" \
  "$image")
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT

port=$(docker inspect -f '{{(index (index .NetworkSettings.Ports "8080/tcp") 0).HostPort}}' "$container")
url="http://127.0.0.1:${port}/realms/master/.well-known/openid-configuration"
for _ in {1..60}; do
  if response=$(curl --silent --fail --max-time 2 \
      -H "Host: ${KC_HOSTNAME#*://}" \
      -H 'X-Forwarded-Proto: https' \
      -H "X-Forwarded-Host: ${KC_HOSTNAME#*://}" "$url"); then
    grep -Fq "\"issuer\":\"${KC_HOSTNAME}/realms/master\"" <<<"$response"
    exit 0
  fi
  sleep 2
done

echo "Keycloak did not serve forwarded-host discovery on port 8080" >&2
exit 1

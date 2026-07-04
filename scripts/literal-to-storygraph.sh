#!/usr/bin/env bash
# Export Literal.club → StoryGraph import CSV using curl for all API calls.
# Use this if the Python script hits Cloudflare error 1010.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="storygraph-import.csv"

usage() {
  cat <<'EOF'
Usage: literal-to-storygraph.sh [options]

Fetch your Literal library and write a StoryGraph-compatible CSV.

Environment:
  LITERAL_TOKEN          Use an existing API token (skips login)
  LITERAL_EMAIL          Literal account email (if no token)
  LITERAL_PASSWORD       Literal account password (if no token)

Options:
  -o, --output FILE      Output CSV path (default: storygraph-import.csv)
  -h, --help             Show this help

Examples:
  LITERAL_EMAIL=you@example.com LITERAL_PASSWORD='secret' \
    ./literal-to-storygraph.sh

  LITERAL_TOKEN='eyJ...' ./literal-to-storygraph.sh -o books.csv
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output)
      OUTPUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
GRAPHQL='https://literal.club/graphql/'

literal_post() {
  local payload="$1"
  local token="${2:-}"
  local args=(
    -sS
    -X POST
    "$GRAPHQL"
    -H "Content-Type: application/json"
    -H "User-Agent: $UA"
    -H "Accept: application/json, text/plain, */*"
    -H "Origin: https://literal.club"
    -H "Referer: https://literal.club/"
    --data-binary "$payload"
  )
  if [[ -n "$token" ]]; then
    args+=(-H "Authorization: Bearer $token")
  fi
  curl "${args[@]}"
}

if [[ -z "${LITERAL_TOKEN:-}" ]]; then
  if [[ -z "${LITERAL_EMAIL:-}" || -z "${LITERAL_PASSWORD:-}" ]]; then
    echo "Set LITERAL_TOKEN, or LITERAL_EMAIL and LITERAL_PASSWORD." >&2
    exit 1
  fi

  login_payload=$(jq -n \
    --arg email "$LITERAL_EMAIL" \
    --arg password "$LITERAL_PASSWORD" \
    '{query:"mutation Login($email: String!, $password: String!) { login(email: $email, password: $password) { token profile { id handle } } }", variables:{email:$email, password:$password}}')

  login_response=$(literal_post "$login_payload")
  if echo "$login_response" | jq -e '.errors' >/dev/null 2>&1; then
    echo "Login failed: $(echo "$login_response" | jq -r '.errors[0].message')" >&2
    exit 1
  fi

  export LITERAL_TOKEN
  LITERAL_TOKEN=$(echo "$login_response" | jq -r '.data.login.token')
  export LITERAL_PROFILE_ID
  LITERAL_PROFILE_ID=$(echo "$login_response" | jq -r '.data.login.profile.id // empty')

  if [[ -z "$LITERAL_TOKEN" || "$LITERAL_TOKEN" == "null" ]]; then
    echo "Login failed: no token returned." >&2
    exit 1
  fi
  echo "Logged in to Literal." >&2
fi

exec python3 "$SCRIPT_DIR/literal-to-storygraph.py" \
  --fetch \
  --http-backend curl \
  --token "$LITERAL_TOKEN" \
  -o "$OUTPUT"

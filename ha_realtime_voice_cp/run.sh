#!/usr/bin/env bash
# Map Home Assistant add-on options → control plane env, then exec.
set -euo pipefail

OPTIONS_FILE="${OPTIONS_FILE:-/data/options.json}"

if [[ ! -f "${OPTIONS_FILE}" ]]; then
  echo "ERROR: ${OPTIONS_FILE} not found (is this running as an HA add-on?)" >&2
  exit 1
fi

json_str() {
  local key="$1"
  jq -r --arg k "$key" '.[$k] // empty | if . == null then empty else tostring end' "${OPTIONS_FILE}"
}

json_int() {
  local key="$1"
  local default="$2"
  local v
  v="$(jq -r --arg k "$key" --arg d "$default" '.[$k] // $d' "${OPTIONS_FILE}")"
  echo "${v}"
}

export XAI_API_KEY="$(json_str xai_api_key)"
export DEVICE_TOKENS="$(json_str device_tokens)"
export HA_BASE_URL="$(json_str ha_base_url)"
export PUBLIC_BASE_URL="$(json_str public_base_url)"
export DEFAULT_MODEL="$(json_str default_model)"
export DEFAULT_VOICE="$(json_str default_voice)"
export DEFAULT_SAMPLE_RATE="$(json_int default_sample_rate 16000)"
export XAI_EPHEMERAL_TTL_SECONDS="$(json_int xai_ephemeral_ttl_seconds 300)"
export LOG_LEVEL="$(json_str log_level)"
export DEFAULT_INSTRUCTIONS="$(json_str default_instructions)"
export UI_ACCESS="$(json_str ui_access)"
export DEVICE_AREAS="$(json_str device_areas)"
export SERVICE_ALLOW="$(json_str service_allow)"
export SERVICE_DENY="$(json_str service_deny)"
export CONFIRM_PIN="$(json_str confirm_pin)"
# Distinguish "not configured" (use the built-in confirm list) from "" (gate
# deliberately disabled). jq prints nothing for a missing key either way, so
# only export when the key is actually present.
if jq -e 'has("service_confirm")' "${OPTIONS_FILE}" >/dev/null; then
  export SERVICE_CONFIRM="$(json_str service_confirm)"
fi

# Always listen on all interfaces inside the container; Supervisor publishes 8787.
export LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"
export LISTEN_PORT="${LISTEN_PORT:-8787}"
export DATA_DIR="${DATA_DIR:-/data}"

if [[ -z "${XAI_API_KEY}" ]]; then
  echo "ERROR: xai_api_key is empty — set it in the add-on Configuration tab" >&2
  exit 2
fi
# Empty is fine now: devices are normally enrolled from the pairing UI, which
# mints a token and stores only its SHA-256. Starting with no credentials at all
# is a working state — the UI is how you leave it.
if [[ -z "${DEVICE_TOKENS}" ]]; then
  echo "NOTE: device_tokens is empty. Enrol devices from the add-on's pairing UI"
  echo "      (Devices -> Enrol device); the token is shown once."
fi
# A device token mints a live HA access token (pairing user's full privileges)
# plus a live xAI ephemeral token. Fail closed rather than boot on a value that
# is published in the repository or trivially guessable.
if [[ -n "${DEVICE_TOKENS}" && "${DEVICE_TOKENS}" == *"change-me"* ]]; then
  echo "ERROR: device_tokens still contains the example value 'change-me...'." >&2
  echo "  This credential is public. Generate a real one:" >&2
  echo "    python3 -c 'import secrets; print(\"voice-pe-1:\" + secrets.token_urlsafe(32))'" >&2
  exit 2
fi
if [[ -z "${HA_BASE_URL}" ]]; then
  echo "ERROR: ha_base_url is empty" >&2
  exit 2
fi
if [[ -z "${UI_ACCESS}" ]]; then
  export UI_ACCESS=ingress
fi
# Only the LAN-served UI needs a configured public URL. Through ingress the
# OAuth client_id and redirect are derived from the request, which is also the
# only way to get them right — Supervisor rewrites the base path per session.
if [[ -z "${PUBLIC_BASE_URL}" && "${UI_ACCESS}" != "ingress" ]]; then
  echo "ERROR: public_base_url is empty and ui_access=${UI_ACCESS}" >&2
  echo "  Either set public_base_url, or set ui_access: ingress to open the" >&2
  echo "  pairing UI from the Home Assistant sidebar instead." >&2
  exit 2
fi

if [[ -z "${DEFAULT_INSTRUCTIONS}" ]]; then
  unset DEFAULT_INSTRUCTIONS
fi
if [[ -z "${LOG_LEVEL}" ]]; then
  export LOG_LEVEL=info
fi

# OAuth client_id defaults to PUBLIC_BASE_URL inside the CP.
if [[ -n "${PUBLIC_BASE_URL}" ]]; then
  export HA_OAUTH_CLIENT_ID="${PUBLIC_BASE_URL}"
fi

mkdir -p "${DATA_DIR}"

echo "Starting HA Realtime Voice control plane on ${LISTEN_HOST}:${LISTEN_PORT}"
echo "  data_dir=${DATA_DIR}"
echo "  ha_base_url=${HA_BASE_URL}"
echo "  public_base_url=${PUBLIC_BASE_URL:-(derived from ingress)}"
echo "  ui_access=${UI_ACCESS}"
echo "  devices=$(echo "${DEVICE_TOKENS}" | awk -F',' '{print NF}')"
if [[ "${UI_ACCESS}" == "ingress" ]]; then
  echo "  HA credentials: open this add-on from the Home Assistant sidebar to pair"
  echo "  Port ${LISTEN_PORT} serves device-authenticated endpoints only"
else
  echo "  HA credentials: OAuth pairing UI at ${PUBLIC_BASE_URL}/ (no token paste)"
fi

exec ha-realtime-voice-cp

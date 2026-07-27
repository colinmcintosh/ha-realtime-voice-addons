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
export HA_LONG_LIVED_TOKEN="$(json_str ha_long_lived_token)"
export HA_REFRESH_TOKEN="$(json_str ha_refresh_token)"
export DEFAULT_MODEL="$(json_str default_model)"
export DEFAULT_VOICE="$(json_str default_voice)"
export DEFAULT_SAMPLE_RATE="$(json_int default_sample_rate 16000)"
export XAI_EPHEMERAL_TTL_SECONDS="$(json_int xai_ephemeral_ttl_seconds 300)"
export LOG_LEVEL="$(json_str log_level)"
export DEFAULT_INSTRUCTIONS="$(json_str default_instructions)"

# Always listen on all interfaces inside the container; Supervisor publishes 8787.
export LISTEN_HOST="${LISTEN_HOST:-0.0.0.0}"
export LISTEN_PORT="${LISTEN_PORT:-8787}"
export DATA_DIR="${DATA_DIR:-/data}"

if [[ -z "${XAI_API_KEY}" ]]; then
  echo "ERROR: xai_api_key is empty — set it in the add-on Configuration tab" >&2
  exit 2
fi
if [[ -z "${DEVICE_TOKENS}" ]]; then
  echo "ERROR: device_tokens is empty — need at least one device_id:token pair" >&2
  exit 2
fi

# Empty optional strings → unset so pydantic defaults apply where intended.
if [[ -z "${HA_BASE_URL}" ]]; then
  unset HA_BASE_URL
fi
if [[ -z "${HA_LONG_LIVED_TOKEN}" ]]; then
  unset HA_LONG_LIVED_TOKEN
fi
if [[ -z "${HA_REFRESH_TOKEN}" ]]; then
  unset HA_REFRESH_TOKEN
fi
if [[ -z "${DEFAULT_INSTRUCTIONS}" ]]; then
  unset DEFAULT_INSTRUCTIONS
fi
if [[ -z "${LOG_LEVEL}" ]]; then
  export LOG_LEVEL=info
fi

mkdir -p "${DATA_DIR}"

echo "Starting HA Realtime Voice control plane on ${LISTEN_HOST}:${LISTEN_PORT}"
echo "  data_dir=${DATA_DIR}"
echo "  ha_base_url=${HA_BASE_URL:-<unset>}"
echo "  devices=$(echo "${DEVICE_TOKENS}" | awk -F',' '{print NF}')"

exec ha-realtime-voice-cp

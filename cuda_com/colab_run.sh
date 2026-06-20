#!/usr/bin/env bash
set -euo pipefail

# Build/run controls. Set these as environment variables, or leave defaults:
#   LARGE_BIOMES=0 or 1
#   UNBOUND=0 or 1
#   PRINT_INTERVAL=256
#   CUDA_ARCH=sm_75, sm_80, sm_86, sm_89, sm_90, or leave empty for autodetect
#
# Optional Discord forwarding can be supplied as env vars:
#   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
#   DISCORD_OUTPUT_FILE=output.txt
#   DISCORD_MESSAGE_PREFIX="optional text"
#
# Or as script parameters:
#   ./colab_run.sh --webhook "https://discord.com/api/webhooks/..." --device 0
#   ./colab_run.sh --discord-webhook "https://discord.com/api/webhooks/..." --discord-prefix "Seed output" --device 0
#
# Parameters recognized by this wrapper are consumed. All other parameters are
# passed through to ./main.

cd "$(dirname "$0")"

LARGE_BIOMES="${LARGE_BIOMES:-0}"
UNBOUND="${UNBOUND:-1}"
PRINT_INTERVAL="${PRINT_INTERVAL:-256}"
DISCORD_OUTPUT_FILE="${DISCORD_OUTPUT_FILE:-output.txt}"

main_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --webhook|--discord-webhook)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "ERROR: $1 requires a Discord webhook URL." >&2
        exit 2
      fi
      DISCORD_WEBHOOK_URL="$2"
      export DISCORD_WEBHOOK_URL
      shift 2
      ;;
    --webhook=*|--discord-webhook=*)
      DISCORD_WEBHOOK_URL="${1#*=}"
      export DISCORD_WEBHOOK_URL
      shift
      ;;
    --discord-output|--discord-output-file)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "ERROR: $1 requires an output file path." >&2
        exit 2
      fi
      DISCORD_OUTPUT_FILE="$2"
      export DISCORD_OUTPUT_FILE
      shift 2
      ;;
    --discord-output=*|--discord-output-file=*)
      DISCORD_OUTPUT_FILE="${1#*=}"
      export DISCORD_OUTPUT_FILE
      shift
      ;;
    --discord-prefix|--discord-message-prefix)
      if [[ $# -lt 2 ]]; then
        echo "ERROR: $1 requires a message prefix." >&2
        exit 2
      fi
      DISCORD_MESSAGE_PREFIX="$2"
      export DISCORD_MESSAGE_PREFIX
      shift 2
      ;;
    --discord-prefix=*|--discord-message-prefix=*)
      DISCORD_MESSAGE_PREFIX="${1#*=}"
      export DISCORD_MESSAGE_PREFIX
      shift
      ;;
    --discord-username)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "ERROR: $1 requires a username." >&2
        exit 2
      fi
      DISCORD_USERNAME="$2"
      export DISCORD_USERNAME
      shift 2
      ;;
    --discord-username=*)
      DISCORD_USERNAME="${1#*=}"
      export DISCORD_USERNAME
      shift
      ;;
    --no-discord)
      unset DISCORD_WEBHOOK_URL
      shift
      ;;
    --)
      shift
      main_args+=("$@")
      break
      ;;
    *)
      main_args+=("$1")
      shift
      ;;
  esac
done

if ! command -v nvcc >/dev/null 2>&1; then
  echo "ERROR: nvcc was not found. In Colab, choose Runtime > Change runtime type > GPU, then reconnect and rerun." >&2
  exit 1
fi

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "WARNING: nvidia-smi was not found. CUDA may not be available in this runtime." >&2
else
  nvidia-smi -L || true
fi

if [[ -z "${CUDA_ARCH:-}" ]]; then
  CUDA_ARCH=""
  if command -v python3 >/dev/null 2>&1; then
    CUDA_ARCH="$(python3 - <<'PY_INNER' 2>/dev/null || true
try:
    import torch
    if torch.cuda.is_available():
        major, minor = torch.cuda.get_device_capability(0)
        print(f"sm_{major}{minor}")
except Exception:
    pass
PY_INNER
)"
  fi

  if [[ -z "$CUDA_ARCH" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    CUDA_ARCH="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 | tr -d '. ' | sed 's/^/sm_/' || true)"
  fi

  if [[ -z "$CUDA_ARCH" ]]; then
    CUDA_ARCH="sm_75"
    echo "WARNING: could not detect CUDA architecture; falling back to $CUDA_ARCH. Set CUDA_ARCH manually if this is wrong." >&2
  fi
fi

export DISCORD_OUTPUT_FILE

echo "Using CUDA_ARCH=$CUDA_ARCH"
echo "Using LARGE_BIOMES=$LARGE_BIOMES UNBOUND=$UNBOUND PRINT_INTERVAL=$PRINT_INTERVAL"

nvcc \
  src/main.cpp src/cpu.cpp src/gpu.cu src/cubiomes.c \
  src/client.cpp src/server.cpp \
  cubiomes/biomenoise.c cubiomes/biomes.c cubiomes/finders.c \
  cubiomes/generator.c cubiomes/layers.c cubiomes/noise.c \
  -o main \
  -O3 -std=c++20 -I asio/asio/include \
  --expt-relaxed-constexpr --default-stream per-thread \
  -DOMISSION_LARGE_BIOMES="$LARGE_BIOMES" \
  -DOMISSION_UNBOUND="$UNBOUND" \
  -DPRINT_INTERVAL="$PRINT_INTERVAL" \
  -arch="$CUDA_ARCH"

echo "Build complete. Launching ./main ${main_args[*]}"

args=("${main_args[@]}")
explicit_output=0
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[$i]}" == "--output" || "${args[$i]}" == --output=* ]]; then
    explicit_output=1
    if [[ "${args[$i]}" == "--output" && $((i + 1)) -lt ${#args[@]} ]]; then
      DISCORD_OUTPUT_FILE="${args[$((i + 1))]}"
      export DISCORD_OUTPUT_FILE
    elif [[ "${args[$i]}" == --output=* ]]; then
      DISCORD_OUTPUT_FILE="${args[$i]#--output=}"
      export DISCORD_OUTPUT_FILE
    fi
    break
  fi
done

if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
  echo "Discord forwarding enabled. Seed outputs will be tailed from $DISCORD_OUTPUT_FILE"
  if [[ "$explicit_output" == "0" ]]; then
    args+=("--output" "$DISCORD_OUTPUT_FILE")
  fi
  touch "$DISCORD_OUTPUT_FILE"
  python3 ./discord_output_bridge.py "$DISCORD_OUTPUT_FILE" &
  discord_bridge_pid=$!
  trap 'kill "$discord_bridge_pid" 2>/dev/null || true' EXIT INT TERM
  ./main "${args[@]}"
else
  exec ./main "${args[@]}"
fi

#!/bin/sh
# Translates the container environment into JANUS CLI arguments.
# Extra arguments passed to the container are appended and win over these.
set -eu

models_dir="${JANUS_MODELS_DIR:-/models}"

stt_model="${JANUS_STT_MODEL:-small}"
case "$stt_model" in
    /*) ;;
    *) stt_model="$models_dir/faster-whisper-$stt_model" ;;
esac

# Without an explicit URL each provider uses its own default (Ollama on
# 127.0.0.1:11434, Hugging Face on its router).
set -- ${JANUS_LLM_BASE_URL:+--llm-base-url "$JANUS_LLM_BASE_URL"} "$@"

exec python -m janus \
    --config-dir /app/configs \
    --mode "${JANUS_MODE:-stand}" \
    --llm-provider "${JANUS_LLM_PROVIDER:-ollama}" \
    --llm-model "${JANUS_LLM_MODEL:-qwen3:4b-instruct}" \
    --stt-provider "${JANUS_STT_PROVIDER:-faster_whisper}" \
    --stt-model "$stt_model" \
    --tts-provider "${JANUS_TTS_PROVIDER:-piper}" \
    --piper-model-it "$models_dir/piper/${JANUS_PIPER_VOICE_IT:-it_IT-paola-medium}.onnx" \
    --piper-model-en "$models_dir/piper/${JANUS_PIPER_VOICE_EN:-en_US-lessac-medium}.onnx" \
    --data-dir /data \
    --host 0.0.0.0 \
    --port 8000 \
    "$@"

#!/bin/sh
# One-shot provisioning of the offline speech models into the shared volume.
# Idempotent: models that are already complete are not downloaded again.
set -eu

models_dir="${JANUS_MODELS_DIR:-/models}"

if [ "${JANUS_STT_PROVIDER:-faster_whisper}" = "faster_whisper" ]; then
    size="${JANUS_STT_MODEL:-small}"
    destination="$models_dir/faster-whisper-$size"
    if [ -f "$destination/config.json" ] && [ -f "$destination/model.bin" ] \
        && [ -f "$destination/tokenizer.json" ]; then
        echo "faster-whisper $size already present in $destination"
    else
        echo "Downloading faster-whisper $size into $destination..."
        python /app/scripts/prepare_speech_model.py --model "$size" --destination "$destination"
    fi
fi

if [ "${JANUS_TTS_PROVIDER:-piper}" = "piper" ]; then
    piper_dir="$models_dir/piper"
    mkdir -p "$piper_dir"
    for voice in "${JANUS_PIPER_VOICE_IT:-it_IT-paola-medium}" \
        "${JANUS_PIPER_VOICE_EN:-en_US-lessac-medium}"; do
        if [ -f "$piper_dir/$voice.onnx" ] && [ -f "$piper_dir/$voice.onnx.json" ]; then
            echo "Piper voice $voice already present"
        else
            echo "Downloading Piper voice $voice into $piper_dir..."
            python -m piper.download_voices --data-dir "$piper_dir" "$voice"
        fi
        if [ ! -f "$piper_dir/$voice.onnx" ] || [ ! -f "$piper_dir/$voice.onnx.json" ]; then
            echo "Incomplete Piper voice: $voice" >&2
            exit 1
        fi
    done
fi

echo "Speech models ready."

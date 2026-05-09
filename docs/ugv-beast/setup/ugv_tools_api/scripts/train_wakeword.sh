#!/usr/bin/env bash
# Train a custom openWakeWord model for "black box flight recorder"
#
# Pipeline:
#   1. Create a dedicated Python venv with openwakeword + piper-tts + torch (CPU).
#   2. Download ~7 Piper TTS voices (English, varied accents & genders).
#   3. Generate ~560 positive clips (7 voices × 80 variations each:
#      multiple phrase variants × 4 length-scales × 3 noise × 3 noise-w).
#   4. Generate ~800 negative clips (generic English + adversarial phrases
#      that contain partial fragments like "black cat", "flight attendant",
#      "voice recorder", etc) + 50 silence/low-noise clips.
#   5. Extract (16, 96) embedding features using openwakeword's bundled
#      melspectrogram.onnx + embedding_model.onnx (Google speech_embedding).
#      Positive stride = 1 (dense windowing), negative stride = 4.
#   6. Train a small DNN (flatten → 128-unit FC → LayerNorm → ReLU → 1 FCN block
#      → linear → sigmoid) matching openwakeword.train.Net architecture,
#      ~175k params. 40 epochs, Adam lr=1e-3, cosine schedule, pos_weight=5.0.
#   7. Select best checkpoint by recall - 2*max_neg_score.
#   8. Export to ONNX opset 14 at the target path.
#
# Run from anywhere: ./scripts/train_wakeword.sh
#
# Training runs on CPU — ~5-15 minutes wall time on a modern laptop.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
TOOLS_DIR="${REPO_ROOT}/docs/ugv-beast/setup/ugv_tools_api"
TRAINING_DIR="${TOOLS_DIR}/training"
OUT_ONNX="${TOOLS_DIR}/voice_models/black_box_flight_recorder.onnx"

WORK_DIR="${WORK_DIR:-/tmp/oww_train}"
VENV_DIR="${VENV_DIR:-/tmp/wake_venv}"
VOICES_DIR="${WORK_DIR}/piper_voices"
POS_DIR="${WORK_DIR}/samples_pos"
NEG_DIR="${WORK_DIR}/samples_neg"
FEATURES_NPZ="${WORK_DIR}/features.npz"

N_PER_VOICE="${N_PER_VOICE:-80}"
N_NEG="${N_NEG:-800}"
EPOCHS="${EPOCHS:-40}"

# 1. venv + deps -----------------------------------------------------------
if [ ! -x "${VENV_DIR}/bin/python3" ]; then
    echo "[1/7] Creating venv at ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip >/dev/null
    "${VENV_DIR}/bin/pip" install \
        openwakeword==0.4.0 \
        piper-tts \
        onnxruntime \
        soundfile \
        scipy \
        torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -3
    "${VENV_DIR}/bin/pip" install torch 2>&1 | tail -2
fi
PY="${VENV_DIR}/bin/python3"
PIPER="${VENV_DIR}/bin/piper"
mkdir -p "${WORK_DIR}" "${VOICES_DIR}" "${POS_DIR}" "${NEG_DIR}" "$(dirname "${OUT_ONNX}")"

# 2. download voices --------------------------------------------------------
echo "[2/7] Downloading Piper voices to ${VOICES_DIR}"
VOICES=(
    en_US-amy-medium
    en_US-lessac-medium
    en_US-ryan-high
    en_US-kathleen-low
    en_US-joe-medium
    en_GB-alan-medium
    en_GB-jenny_dioco-medium
)
NEED_DL=()
for v in "${VOICES[@]}"; do
    [ -f "${VOICES_DIR}/${v}.onnx" ] || NEED_DL+=("$v")
done
if [ ${#NEED_DL[@]} -gt 0 ]; then
    "${PY}" -m piper.download_voices --download-dir "${VOICES_DIR}" "${NEED_DL[@]}"
fi
echo "    voices ready: $(ls "${VOICES_DIR}"/*.onnx 2>/dev/null | wc -l)"

# 3. positive samples -------------------------------------------------------
echo "[3/7] Generating positive samples (${N_PER_VOICE} per voice × 7 voices ≈ 560)"
"${PY}" "${TRAINING_DIR}/generate_positives.py" \
    --voices-dir "${VOICES_DIR}" \
    --out-dir "${POS_DIR}" \
    --piper-bin "${PIPER}" \
    --n-per-voice "${N_PER_VOICE}"
echo "    positive clips: $(ls "${POS_DIR}"/*.wav 2>/dev/null | wc -l)"

# 4. negative samples -------------------------------------------------------
echo "[4/7] Generating negative samples (${N_NEG} synthetic + 50 silence)"
"${PY}" "${TRAINING_DIR}/generate_negatives.py" \
    --voices-dir "${VOICES_DIR}" \
    --out-dir "${NEG_DIR}" \
    --piper-bin "${PIPER}" \
    --n-samples "${N_NEG}"
echo "    negative clips: $(ls "${NEG_DIR}"/*.wav 2>/dev/null | wc -l)"

# 5. feature extraction -----------------------------------------------------
echo "[5/7] Extracting (16, 96) embedding features via openwakeword"
"${PY}" "${TRAINING_DIR}/extract_features.py" \
    --pos-dir "${POS_DIR}" \
    --neg-dir "${NEG_DIR}" \
    --out "${FEATURES_NPZ}" \
    --pos-step 1 --neg-step 4

# 6. train + export ---------------------------------------------------------
echo "[6/7] Training DNN head (${EPOCHS} epochs)"
"${PY}" "${TRAINING_DIR}/train_model.py" \
    --features "${FEATURES_NPZ}" \
    --out-onnx "${OUT_ONNX}" \
    --epochs "${EPOCHS}" \
    --batch-size 256 \
    --lr 1e-3 \
    --layer-dim 128 \
    --n-blocks 1 \
    --pos-weight 5.0

# 7. done -------------------------------------------------------------------
echo "[7/7] Verifying output"
ls -la "${OUT_ONNX}"
"${PY}" -c "
from openwakeword.model import Model
try:
    m = Model(wakeword_models=['${OUT_ONNX}'], inference_framework='onnx')
except (TypeError, ValueError):
    m = Model(wakeword_model_paths=['${OUT_ONNX}'])
print('loaded:', list(m.models.keys()))
"
echo "==> model ready at: ${OUT_ONNX}"

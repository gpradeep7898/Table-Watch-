#!/usr/bin/env bash
# Explicit, separate download of the optional person-detection model.
# YOLOX-Nano (Apache-2.0, Megvii) in ONNX format, checksum-verified.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${1:-yolox_nano}"
case "$MODEL" in
  yolox_nano) SHA=c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d ;;
  yolox_tiny) SHA=427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7 ;;
  *) echo "Unknown model $MODEL (use yolox_nano or yolox_tiny)"; exit 1 ;;
esac
URL="https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/${MODEL}.onnx"
mkdir -p "$ROOT/models"
OUT="$ROOT/models/${MODEL}.onnx"
echo "Downloading $URL"
curl -fL --retry 3 -o "$OUT.part" "$URL"
GOT="$( (command -v sha256sum >/dev/null && sha256sum "$OUT.part" || shasum -a 256 "$OUT.part") | awk '{print $1}')"
if [[ "$GOT" != "$SHA" ]]; then rm -f "$OUT.part"; echo "Checksum mismatch. Not installing."; exit 1; fi
mv "$OUT.part" "$OUT"
echo "Installed $OUT (sha256 verified). License: Apache-2.0. See docs/licenses.md."

#!/usr/bin/env bash
# Build a Lambda deployment artifact at build/lambda/ (+ a zip).
#
# yfinance pulls in compiled dependencies (numpy, pandas, curl_cffi, cffi), so a
# plain "pip install" on macOS would bundle the wrong (Darwin) wheels. We do a
# two-pass install:
#   1. Normal install   -> resolves all pure-Python deps correctly.
#   2. Override binaries -> reinstall the compiled packages as manylinux/cp313
#                           wheels so they run on the Lambda (Amazon Linux x86_64)
#                           runtime.
# Then we prune test suites, caches, dist-info, and packages already present in
# the Lambda runtime (boto3/botocore) to stay comfortably under the 250 MB
# unzipped limit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$ROOT/build/lambda"
ZIP_PATH="$ROOT/build/stock-agent-lambda.zip"

LAMBDA_PYTHON="${LAMBDA_PYTHON:-3.13}"
PLATFORM="${LAMBDA_PLATFORM:-manylinux2014_x86_64}"
BINARY_PKGS="numpy pandas curl_cffi cffi"
PY="${PYTHON:-python3}"

echo "Cleaning $BUILD_DIR"
rm -rf "$BUILD_DIR" "$ZIP_PATH"
mkdir -p "$BUILD_DIR"

echo "[1/4] Installing dependencies (pure-Python resolution)"
"$PY" -m pip install -r "$ROOT/requirements.txt" --target "$BUILD_DIR" --quiet

echo "[2/4] Overriding compiled deps with $PLATFORM / cp$LAMBDA_PYTHON wheels"
"$PY" -m pip install --target "$BUILD_DIR" \
    --platform "$PLATFORM" --python-version "$LAMBDA_PYTHON" \
    --implementation cp --only-binary=:all: --upgrade \
    $BINARY_PKGS --quiet

echo "[3/4] Copying application package"
cp -r "$ROOT/src/stock_agent" "$BUILD_DIR/stock_agent"

echo "[3.5/4] Pruning"
# boto3/botocore are provided by the Lambda Python runtime.
rm -rf "$BUILD_DIR"/boto3 "$BUILD_DIR"/botocore "$BUILD_DIR"/s3transfer
# Stray transitive UI deps not needed at runtime.
rm -rf "$BUILD_DIR"/rich "$BUILD_DIR"/pygments "$BUILD_DIR"/markdown_it "$BUILD_DIR"/mdurl
# Test suites, caches, metadata, type stubs.
find "$BUILD_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type d \( -name "tests" -o -name "test" \) -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -type d -name "*.dist-info" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD_DIR" -name "*.pyc" -delete 2>/dev/null || true

echo "[4/4] Creating zip $ZIP_PATH"
(cd "$BUILD_DIR" && zip -r -q -X "$ZIP_PATH" .)

UNZIPPED=$(du -sh "$BUILD_DIR" | cut -f1)
ZIPPED=$(du -h "$ZIP_PATH" | cut -f1)
echo
echo "Done."
echo "  Unzipped: $UNZIPPED (Lambda limit 250 MB)"
echo "  Zip:      $ZIPPED  -> $ZIP_PATH (direct-upload limit 50 MB; else via S3)"

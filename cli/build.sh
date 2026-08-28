#!/usr/bin/env bash
#
# Build wiki-cli. Standard library only, no external dependencies.
#
# Usage:
#   ./build.sh              # build a binary for the host OS/arch -> ./wiki-cli
#   ./build.sh --all        # also cross-compile release binaries into ./dist
#   ./build.sh --skip-tests # skip `go test` before building
#
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

BINARY_NAME="wiki-cli"
DIST_DIR="dist"
BUILD_ALL=0
SKIP_TESTS=0

for arg in "$@"; do
  case "$arg" in
    --all) BUILD_ALL=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    -h|--help)
      echo "Usage: $0 [--all] [--skip-tests]"
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

if ! command -v go >/dev/null 2>&1; then
  echo "error: go is not installed or not on PATH" >&2
  exit 1
fi

echo "==> go vet"
go vet ./...

if [ "$SKIP_TESTS" -eq 0 ]; then
  echo "==> go test"
  go test ./...
else
  echo "==> skipping tests"
fi

if [ "$BUILD_ALL" -eq 1 ]; then
  rm -rf "$DIST_DIR"
  mkdir -p "$DIST_DIR"

  # GOOS/GOARCH pairs to cross-compile release binaries for.
  TARGETS=(
    "darwin amd64"
    "darwin arm64"
    "linux amd64"
    "linux arm64"
    "windows amd64"
  )

  for target in "${TARGETS[@]}"; do
    read -r goos goarch <<< "$target"
    out="$DIST_DIR/${BINARY_NAME}-${goos}-${goarch}"
    [ "$goos" = "windows" ] && out="${out}.exe"
    echo "==> building $out"
    GOOS="$goos" GOARCH="$goarch" go build -trimpath -o "$out" .
  done

  echo "==> done, binaries in $DIST_DIR/"
else
  echo "==> building ./$BINARY_NAME"
  go build -trimpath -o "$BINARY_NAME" .
  echo "==> done: ./$BINARY_NAME"
fi

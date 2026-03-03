#!/bin/bash
set -e

APP_NAME="opencontext-client"

# Platforms to build for
PLATFORMS=("darwin/amd64" "darwin/arm64" "linux/amd64" "linux/arm64" "windows/amd64")

echo "Building for platforms: ${PLATFORMS[*]}"

for PLATFORM in "${PLATFORMS[@]}"; do
    GOOS=${PLATFORM%/*}
    GOARCH=${PLATFORM#*/}
    OUTPUT_NAME="${APP_NAME}-${GOOS}-${GOARCH}"

    if [ "$GOOS" = "windows" ]; then
        OUTPUT_NAME+=".exe"
    fi

    echo "Building ${OUTPUT_NAME}..."
    env GOOS=$GOOS GOARCH=$GOARCH go build -ldflags="-s -w" -o "${OUTPUT_NAME}" main.go
done

echo "Build complete!"

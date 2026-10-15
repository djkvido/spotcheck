#!/usr/bin/env bash
set -euo pipefail

TARGET="$1"
OUT="$(mkdir -p "$2" && cd "$2" && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${WORK:-$PWD/ffmpeg-build}"
PREFIX="$WORK/prefix"
SOURCES="$OUT/sources"
JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
mkdir -p "$WORK" "$PREFIX" "$SOURCES" "$OUT/licenses"

field() {
  python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['ffmpeg_sources'][sys.argv[2]][sys.argv[3]])" \
    "$HERE/versions.json" "$1" "$2"
}

sha256() {
  if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

fetch() {
  local name="$1" url sum file
  url="$(field "$name" url)"
  sum="$(field "$name" sha256)"
  file="$SOURCES/$(basename "$url")"
  [ -f "$file" ] || curl -fsSL --retry 5 -o "$file" "$url"
  if [ "$(sha256 "$file")" != "$sum" ]; then
    echo "Checksum mismatch for $file" >&2
    exit 1
  fi
  rm -rf "$WORK/$name"
  mkdir -p "$WORK/$name"
  tar -xf "$file" -C "$WORK/$name" --strip-components 1
}

case "$TARGET" in
  windows)
    HOST=x86_64-w64-mingw32
    HOST_FLAGS=(--host="$HOST")
    export CC="$HOST-gcc" CXX="$HOST-g++" AR="$HOST-ar" RANLIB="$HOST-ranlib" STRIP="$HOST-strip"
    CMAKE_FLAGS=(-DCMAKE_SYSTEM_NAME=Windows -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX"
      -DCMAKE_RC_COMPILER="$HOST-windres" -DCMAKE_FIND_ROOT_PATH="$PREFIX")
    OPUS_FLAGS=(--disable-stack-protector)
    FFMPEG_FLAGS=(--target-os=mingw32 --arch=x86_64 --cross-prefix="$HOST-" --enable-cross-compile)
    EXTRA_CFLAGS="-DCHROMAPRINT_NODLL"
    EXTRA_LDFLAGS="-static -static-libgcc -static-libstdc++"
    EXTRA_LIBS="-lstdc++"
    EXE=.exe
    ;;
  macos)
    export MACOSX_DEPLOYMENT_TARGET=11.0 CC=clang CXX=clang++
    export CFLAGS="-O2 -mmacosx-version-min=11.0" CXXFLAGS="-O2 -mmacosx-version-min=11.0"
    HOST_FLAGS=()
    CMAKE_FLAGS=(-DCMAKE_OSX_DEPLOYMENT_TARGET=11.0 -DCMAKE_OSX_ARCHITECTURES=arm64)
    OPUS_FLAGS=()
    FFMPEG_FLAGS=(--arch=arm64)
    EXTRA_CFLAGS="-mmacosx-version-min=11.0"
    EXTRA_LDFLAGS="-mmacosx-version-min=11.0"
    EXTRA_LIBS="-lc++"
    EXE=
    ;;
  *)
    echo "Usage: build_ffmpeg.sh windows|macos OUTPUT_DIR" >&2
    exit 2
    ;;
esac

export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig"

fetch lame
(
  cd "$WORK/lame"
  sed -i.orig '/lame_init_old/d' include/libmp3lame.sym
  ./configure --prefix="$PREFIX" ${HOST_FLAGS[@]+"${HOST_FLAGS[@]}"} --disable-shared --enable-static --disable-frontend --disable-decoder
  make -j"$JOBS"
  make install
)

fetch opus
(
  cd "$WORK/opus"
  ./configure --prefix="$PREFIX" ${HOST_FLAGS[@]+"${HOST_FLAGS[@]}"} --disable-shared --enable-static --disable-doc \
    --disable-extra-programs ${OPUS_FLAGS[@]+"${OPUS_FLAGS[@]}"}
  make -j"$JOBS"
  make install
)

fetch chromaprint
cmake -S "$WORK/chromaprint" -B "$WORK/chromaprint-build" "${CMAKE_FLAGS[@]}" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF -DBUILD_TOOLS=OFF \
  -DBUILD_TESTS=OFF -DFFT_LIB=kissfft
cmake --build "$WORK/chromaprint-build" --config Release -j "$JOBS"
cmake --install "$WORK/chromaprint-build"

fetch ffmpeg
(
  cd "$WORK/ffmpeg"
  ./configure --prefix="$PREFIX" "${FFMPEG_FLAGS[@]}" --pkg-config=pkg-config --pkg-config-flags=--static \
    --disable-shared --enable-static --disable-debug --disable-doc --disable-ffplay --disable-autodetect \
    --enable-zlib --enable-libmp3lame --enable-libopus --enable-chromaprint \
    --extra-cflags="-I$PREFIX/include $EXTRA_CFLAGS" --extra-ldflags="-L$PREFIX/lib $EXTRA_LDFLAGS" \
    --extra-libs="$EXTRA_LIBS" || { tail -n 60 ffbuild/config.log; exit 1; }
  make -j"$JOBS"
  cp "ffmpeg$EXE" "ffprobe$EXE" "$OUT/"
  cp COPYING.LGPLv2.1 "$OUT/licenses/ffmpeg-LGPL-2.1.txt"
)
cp "$WORK/lame/COPYING" "$OUT/licenses/lame-LGPL.txt"
cp "$WORK/opus/COPYING" "$OUT/licenses/opus-BSD.txt"
cp "$WORK/chromaprint/LICENSE.md" "$OUT/licenses/chromaprint-LICENSE.md"
cp "$HERE/build_ffmpeg.sh" "$HERE/versions.json" "$SOURCES/"

if [ "$TARGET" = macos ]; then
  "$OUT/ffmpeg" -hide_banner -muxers | grep -q chromaprint
  "$OUT/ffmpeg" -hide_banner -version | head -1
fi

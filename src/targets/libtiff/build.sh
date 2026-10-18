#!/usr/bin/env bash
# Build libtiff (+ the libtiffxx C++ wrapper) as static libraries for one variant.
# Called by scripts/build_target.sh with SRC, OUT, CC, CXX, CFLAGS, CXXFLAGS, LDFLAGS, JOBS set.
set -euo pipefail

# Codecs needing extra libraries (jbig, lerc, libdeflate, webp) are off; jpeg, zlib,
# lzma and zstd stay on (all available from Ubuntu packages).
cmake -S "$SRC" -B "$OUT/cmake" -G "Unix Makefiles" \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_C_FLAGS="$CFLAGS" -DCMAKE_CXX_FLAGS="$CXXFLAGS" \
  -DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS" -DCMAKE_SHARED_LINKER_FLAGS="$LDFLAGS" \
  -DBUILD_SHARED_LIBS=OFF \
  -Dtiff-tools=OFF -Dtiff-tests=OFF -Dtiff-contrib=OFF -Dtiff-docs=OFF -Dtiff-cxx=ON \
  -Dtiff-opengl=OFF -Dsphinx=OFF \
  -Djbig=OFF -Dlerc=OFF -Dlibdeflate=OFF -Dwebp=OFF \
  > "$OUT/cmake.log" 2>&1 || { tail -30 "$OUT/cmake.log"; exit 1; }

cmake --build "$OUT/cmake" -j "$JOBS" > "$OUT/build.log" 2>&1 || { tail -30 "$OUT/build.log"; exit 1; }

mkdir -p "$OUT/lib" "$OUT/include"
cp "$OUT/cmake/libtiff/libtiff.a" "$OUT/cmake/libtiff/libtiffxx.a" "$OUT/lib/"
cp "$SRC/libtiff/tiff.h" "$SRC/libtiff/tiffio.h" "$SRC/libtiff/tiffio.hxx" "$OUT/include/"
cp "$OUT/cmake/libtiff/tiffconf.h" "$OUT/cmake/libtiff/tiffvers.h" "$OUT/include/"

PC=$(find "$OUT/cmake" -name 'libtiff-4.pc' | head -1)
if [ -n "$PC" ]; then
  sed -n 's/^Libs.private: *//p' "$PC" > "$OUT/LINK_FLAGS"
fi

#!/usr/bin/env bash
# Build libxml2 as a static library for one variant.
# Called by scripts/build_target.sh with SRC, OUT, CC, CXX, CFLAGS, CXXFLAGS, LDFLAGS, JOBS set.
# Options mirror fuzz/oss-fuzz-build.sh: no shared lib, no debug module, no HTTP, no Python.
set -euo pipefail

cmake -S "$SRC" -B "$OUT/cmake" -G "Unix Makefiles" \
  -DCMAKE_C_COMPILER="$CC" -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS" -DCMAKE_SHARED_LINKER_FLAGS="$LDFLAGS" \
  -DBUILD_SHARED_LIBS=OFF \
  -DLIBXML2_WITH_DEBUG=OFF -DLIBXML2_WITH_HTTP=OFF -DLIBXML2_WITH_PYTHON=OFF \
  -DLIBXML2_WITH_PROGRAMS=OFF -DLIBXML2_WITH_TESTS=OFF -DLIBXML2_WITH_DOCS=OFF \
  -DLIBXML2_WITH_ICU=OFF -DLIBXML2_WITH_READLINE=OFF \
  > "$OUT/cmake.log" 2>&1 || { tail -30 "$OUT/cmake.log"; exit 1; }

cmake --build "$OUT/cmake" -j "$JOBS" > "$OUT/build.log" 2>&1 || { tail -30 "$OUT/build.log"; exit 1; }

mkdir -p "$OUT/lib" "$OUT/include/libxml"
find "$OUT/cmake" -maxdepth 1 -name 'libxml2.a' -exec cp {} "$OUT/lib/" \;
cp "$SRC"/include/libxml/*.h "$OUT/include/libxml/"
find "$OUT/cmake" -name 'xmlversion.h' -exec cp {} "$OUT/include/libxml/" \;

PC=$(find "$OUT/cmake" -name 'libxml-2.0.pc' | head -1)
if [ -n "$PC" ]; then
  sed -n 's/^Libs.private: *//p' "$PC" > "$OUT/LINK_FLAGS"
fi

test -f "$OUT/lib/libxml2.a" && test -f "$OUT/include/libxml/xmlversion.h"

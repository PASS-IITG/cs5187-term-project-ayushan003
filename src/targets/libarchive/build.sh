#!/usr/bin/env bash
# Build libarchive as a static library for one variant.
# Called by scripts/build_target.sh with SRC, OUT, CC, CXX, CFLAGS, LDFLAGS, JOBS set.
# Must produce: $OUT/lib/libarchive.a, $OUT/include/*.h and (if possible) $OUT/LINK_FLAGS.
set -euo pipefail

# Optional features that pull in extra dependencies are switched off so every
# variant links the same way; the in-memory APIs (ACL, xattr entries, ...) stay.
cmake -S "$SRC" -B "$OUT/cmake" -G "Unix Makefiles" \
  -DCMAKE_C_COMPILER="$CC" \
  -DCMAKE_C_FLAGS="$CFLAGS" \
  -DCMAKE_EXE_LINKER_FLAGS="$LDFLAGS" \
  -DCMAKE_SHARED_LINKER_FLAGS="$LDFLAGS" \
  -DENABLE_WERROR=OFF -DENABLE_TEST=OFF -DENABLE_INSTALL=OFF \
  -DENABLE_TAR=OFF -DENABLE_CPIO=OFF -DENABLE_CAT=OFF -DENABLE_UNZIP=OFF \
  -DENABLE_OPENSSL=OFF -DENABLE_MBEDTLS=OFF -DENABLE_NETTLE=OFF -DENABLE_CNG=OFF \
  -DENABLE_ACL=OFF -DENABLE_XATTR=OFF -DENABLE_LIBB2=OFF -DENABLE_EXPAT=OFF \
  > "$OUT/cmake.log" 2>&1 || { tail -30 "$OUT/cmake.log"; exit 1; }

# Prefer the static target only; fall back to building everything.
if ! cmake --build "$OUT/cmake" --target archive_static -j "$JOBS" > "$OUT/build.log" 2>&1; then
  cmake --build "$OUT/cmake" -j "$JOBS" >> "$OUT/build.log" 2>&1 || { tail -30 "$OUT/build.log"; exit 1; }
fi

mkdir -p "$OUT/lib" "$OUT/include"
find "$OUT/cmake" -name 'libarchive.a' -exec cp {} "$OUT/lib/" \;
cp "$SRC/libarchive/archive.h" "$SRC/libarchive/archive_entry.h" "$OUT/include/"

PC=$(find "$OUT/cmake" -name 'libarchive.pc' | head -1)
if [ -n "$PC" ]; then
  sed -n 's/^Libs.private: *//p' "$PC" > "$OUT/LINK_FLAGS"
fi

test -f "$OUT/lib/libarchive.a"

#!/bin/sh
# ARMv7: compile the bridge against the same OpenCV used by palm_service.
set -eu
PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CV_PREFIX=${EYE_OPENCV_PREFIX:-/opt/opencv-neon}
OUT=${EYE_NATIVE_OUTPUT:-"$PACKAGE_ROOT/build/work/libpalm_layer.so"}
case "$(uname -m)" in armv7l|armv8l) ;; *) echo 'Run on ARMv7 Linux' >&2; exit 1;; esac
[ ! -e "$OUT" ] || { echo 'Output exists; choose EYE_NATIVE_OUTPUT' >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
g++ -O3 -std=c++11 -shared -fPIC -I"$CV_PREFIX/include/opencv4" \
  "$PACKAGE_ROOT/src/native/palm_native_layer.cpp" -L"$CV_PREFIX/lib" \
  -Wl,-rpath,"$CV_PREFIX/lib" -lopencv_dnn -lopencv_core -o "$OUT"

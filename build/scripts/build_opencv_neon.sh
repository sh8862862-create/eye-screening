#!/bin/sh
# Match the captured ARMv7 OpenCV 4.5.4 CMake settings. Stage, do not install live.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
WORK=${EYE_CV_BUILD_DIR:-"$ROOT/build/work/opencv_neon"}
PYTHON=${EYE_BOARD_PYTHON:-/usr/local/share/pynq-venv/bin/python3}
case "$(uname -m)" in armv7l|armv8l) ;; *) echo 'Run on ARMv7 Linux' >&2; exit 1;; esac
[ ! -e "$WORK" ] || { echo 'Build directory exists' >&2; exit 1; }
mkdir -p "$WORK/source"
tar -xzf "$ROOT/data/third_party/opencv-4.5.4.tar.gz" -C "$WORK/source"
NUMPY_INCLUDE=$("$PYTHON" -c 'import numpy; print(numpy.get_include())')
cmake -S "$WORK/source/opencv-4.5.4" -B "$WORK/build" \
 -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/opencv-neon \
 -DBUILD_LIST=core,imgproc,imgcodecs,videoio,dnn,python3 \
 -DBUILD_SHARED_LIBS=ON -DBUILD_TESTS=OFF -DBUILD_PERF_TESTS=OFF -DBUILD_EXAMPLES=OFF \
 -DCPU_BASELINE=NEON -DCPU_DISPATCH= -DWITH_IPP=OFF -DWITH_OPENCL=OFF \
 -DWITH_GTK=OFF -DWITH_FFMPEG=OFF -DWITH_GSTREAMER=ON -DWITH_OPENMP=OFF \
 -DPYTHON3_EXECUTABLE="$PYTHON" -DPYTHON3_INCLUDE_DIR=/usr/include/python3.10 \
 -DPYTHON3_NUMPY_INCLUDE_DIRS="$NUMPY_INCLUDE" \
 -DPYTHON3_PACKAGES_PATH=/opt/opencv-neon/lib/python3.10/site-packages
cmake --build "$WORK/build" -j1
DESTDIR="$WORK/rootfs" cmake --install "$WORK/build"
tar -C "$WORK/rootfs" -czf "$WORK/opencv_neon_rebuilt.tar.gz" opt/opencv-neon
echo "Staged runtime: $WORK/opencv_neon_rebuilt.tar.gz"

#!/usr/bin/env bash
# Get an updated config.sub and config.guess
cp $BUILD_PREFIX/share/gnuconfig/config.* .

set -ex
if [[ -n "$mpi" && "$mpi" != "nompi" ]]; then
  export CC=mpicc
  export CXX=mpicxx
  export OPAL_PREFIX=${PREFIX}
fi
# This is supposed to have been done in the autoreconf, but i'm terrible at that
export LDFLAGS="${LDFLAGS} -lhdf5"

if [[ "$(uname)" == "Darwin" ]]; then
  # 2021/12/06 hmaarrfk:
  # I'm really not sure why CMake has such a hard time finding
  # hdf5 on osx.
  # I compiled things on linux with debug mode, and adjusted these flags to match
  CMAKE_HDF5_FLAGS=
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_ROOT=${PREFIX}"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_INCLUDE_DIRS=${PREFIX}/include"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_C_INCLUDE_DIR=${PREFIX}/include"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_C_INCLUDE_DIRS=${PREFIX}/include"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_LIBRARIES=${PREFIX}/lib/libhdf5${SHLIB_EXT}"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_C_LIBRARIES=${PREFIX}/lib/libhdf5${SHLIB_EXT}"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_HL_LIBRARIES=${PREFIX}/lib/libhdf5_hl${SHLIB_EXT};${PREFIX}/lib/libhdf5${SHLIB_EXT}"
  CMAKE_HDF5_FLAGS="${CMAKE_HDF5_FLAGS} -DHDF5_C_HL_LIBRARIES=${PREFIX}/lib/libhdf5_hl${SHLIB_EXT};${PREFIX}/lib/libhdf5${SHLIB_EXT}"
fi

# Build shared library using cmake
# works for both linux and osx, but missing ncxx4-config
# see https://github.com/Unidata/netcdf-cxx4/issues/36

# Build static.
mkdir build_static && cd build_static
cmake \
    ${CMAKE_ARGS} \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_C_COMPILER=${CC} \
    -DCMAKE_CXX_COMPILER="${CXX}" \
    -DCMAKE_INSTALL_PREFIX=$PREFIX \
    -DCMAKE_INSTALL_LIBDIR:PATH=$PREFIX/lib \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF \
    -DNCXX_ENABLE_TESTS=ON \
    -DENABLE_DOXYGEN=OFF \
    ${CMAKE_HDF5_FLAGS} \
    ${SRC_DIR}

make -j${CPU_COUNT}
# ctest  # Run only for the shared lib build to save time.
make install
# Remove the static library
rm ${PREFIX}/lib/libnetcdf-cxx4.a


make clean

cd ..

# Build shared.
mkdir build_shared && cd build_shared
cmake \
    ${CMAKE_ARGS} \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_C_COMPILER=${CC} \
    -DCMAKE_CXX_COMPILER=${CXX} \
    -DCMAKE_INSTALL_PREFIX=$PREFIX \
    -DCMAKE_INSTALL_LIBDIR:PATH=$PREFIX/lib \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON \
    -DNCXX_ENABLE_TESTS=ON \
    -DENABLE_DOXYGEN=OFF \
    ${CMAKE_HDF5_FLAGS} \
    ${SRC_DIR}
make -j${CPU_COUNT}
if [[ "${CONDA_BUILD_CROSS_COMPILATION:-}" != "1" || "${CROSSCOMPILING_EMULATOR}" != "" ]]; then
  HDF5_PLUGIN_PATH="${PWD}/plugins${HDF5_PLUGIN_PATH:+:${HDF5_PLUGIN_PATH}}" \
    ctest --output-on-failure
fi
make install

# Workaround cmake's libnetcdf-cxx4 and configure's libnetcdf_c++4.
for fname in $PREFIX/lib/libnetcdf-cxx4.*; do
  ln -s $fname $PREFIX/lib/libnetcdf_c++4.${fname#*.}
done

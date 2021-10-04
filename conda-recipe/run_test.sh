#!/bin/bash

set -euxo pipefail

PYTEST_ARGS="-q -ra --disable-warnings"
PYARGS="numba_dppy -vv"

if [ -n "$NUMBA_DPPY_TESTING_GDB_ENABLE" ]; then
    PYARGS="$PYARGS -k test_debug_dppy_numba"

    # Activate debugger
    if [[ -v ONEAPI_ROOT ]]; then
        set +ux
        source "${ONEAPI_ROOT}/debugger/latest/env/vars.sh"
        set -ux
    fi
fi

pytest $PYTEST_ARGS --pyargs $PYARGS

if [[ -v ONEAPI_ROOT ]]; then
    set +u
    # shellcheck disable=SC1091
    source "${ONEAPI_ROOT}/compiler/latest/env/vars.sh"
    set -u

    export NUMBA_DPPY_LLVM_SPIRV_ROOT="${ONEAPI_ROOT}/compiler/latest/linux/bin"
    echo "Using llvm-spirv from oneAPI"
else
    export NUMBA_DPPY_LLVM_SPIRV_ROOT="${CONDA_PREFIX}/bin"
    echo "Using llvm-spirv from conda environment"
fi

pytest -q -ra --disable-warnings -vv \
    --pyargs numba_dppy.tests.kernel_tests.test_atomic_op::test_atomic_fp_native

exit 0

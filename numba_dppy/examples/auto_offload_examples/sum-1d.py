# Copyright 2021 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import dpctl
import numpy as np
from numba import njit

import numba_dppy as dppy


@njit
def f1(a, b):
    c = a + b
    return c


def main():
    global_size = 64
    local_size = 32
    N = global_size * local_size
    print("N", N)

    a = np.ones(N, dtype=np.float32)
    b = np.ones(N, dtype=np.float32)

    print("a:", a, hex(a.ctypes.data))
    print("b:", b, hex(b.ctypes.data))

    # Use the environment variable SYCL_DEVICE_FILTER to change
    # the default device. See
    # https://github.com/intel/llvm/blob/sycl/sycl/doc/EnvironmentVariables.md#sycl_device_filter.
    device = dpctl.select_default_device()
    print("Using device ...")
    device.print_device_info()

    with dpctl.device_context(device):
        c = f1(a, b)

    print("RESULT c:", c, hex(c.ctypes.data))
    for i in range(N):
        if c[i] != 2.0:
            print("First index not equal to 2.0 was", i)
            break

    print("Done...")


if __name__ == "__main__":
    main()

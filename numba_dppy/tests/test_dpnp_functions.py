#! /usr/bin/env python
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
import pytest
from numba import njit

from numba_dppy.tests._helper import (
    assert_auto_offloading,
    dpnp_debug,
    skip_no_dpnp,
    skip_no_opencl_gpu,
)


@skip_no_opencl_gpu
@skip_no_dpnp
class Testdpnp_functions:
    N = 10

    a = np.array(np.random.random(N), dtype=np.float32)
    b = np.array(np.random.random(N), dtype=np.float32)
    tys = [np.int32, np.uint32, np.int64, np.uint64, np.float32, np.double]

    def test_dpnp_interacting_with_parfor(self):
        def f(a, b):
            c = np.sum(a)
            e = np.add(b, a)
            d = c + e
            return d

        device = dpctl.SyclDevice("opencl:gpu")
        with dpctl.device_context(
            device
        ), assert_auto_offloading(), dpnp_debug():
            njit_f = njit(f)
            got = njit_f(self.a, self.b)
        expected = f(self.a, self.b)

        max_abs_err = got.sum() - expected.sum()
        assert max_abs_err < 1e-4

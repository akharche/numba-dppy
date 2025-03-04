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

import dpctl.tensor as dpt
import numpy as np
import pytest
from numba.misc.special import typeof

from numba_dppy.dpctl_iface import USMNdArrayType
from numba_dppy.tests._helper import filter_strings

list_of_dtypes = [
    np.int32,
    np.float32,
    np.int64,
    np.float64,
]


@pytest.fixture(params=list_of_dtypes)
def dtype(request):
    return request.param


list_of_usm_type = [
    "shared",
    "device",
    "host",
]


@pytest.fixture(params=list_of_usm_type)
def usm_type(request):
    return request.param


@pytest.mark.parametrize("filter_str", filter_strings)
def test_usm_ndarray_type(filter_str, dtype, usm_type):
    a = np.array(np.random.random(10), dtype)
    da = dpt.usm_ndarray(a.shape, dtype=a.dtype, buffer=usm_type)

    assert isinstance(typeof(da), USMNdArrayType)
    assert da.usm_type == usm_type

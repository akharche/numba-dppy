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
"""Tests for Information About a Frame

https://www.sourceware.org/gdb/onlinedocs/gdb/Frame-Info.html
"""

import pytest

from numba_dppy.tests._helper import skip_no_gdb, skip_no_numba055

from .common import setup_breakpoint
from .test_breakpoints import side_by_side_breakpoint

pytestmark = skip_no_gdb


def side_by_side_case(api):
    return (
        side_by_side_breakpoint,
        f"side-by-side.py --api={api}",
        None,
        (
            r"param_a = 0",
            r"param_b = 0",
        ),
        (
            "param_a",
            r"\$1 = 0",
            r"type = float32",
            r"type = float32",
        ),
    )


@skip_no_numba055
@pytest.mark.parametrize(
    "breakpoint, script, expected_line, expected_args, expected_info",
    [
        (
            "simple_dppy_func.py:29",
            "simple_dppy_func.py",
            r"29\s+i = dppy.get_global_id\(0\)",
            (
                r"a_in_kernel = {meminfo = ",
                r"b_in_kernel = {meminfo = ",
                r"c_in_kernel = {meminfo = ",
            ),
            (
                "a_in_kernel",
                r"\$1 = {meminfo = ",
                r"type = struct array\(float32, 1d, C\).*}\)",
                r"type = array\(float32, 1d, C\) \({.*}\)",
            ),
        ),
        side_by_side_case("numba"),
        side_by_side_case("numba-dppy-kernel"),
    ],
)
def test_info_args(
    app, breakpoint, script, expected_line, expected_args, expected_info
):
    """Test for info args command.

    SAT-4462
    Issue: https://github.com/numba/numba/issues/7414
    Fix: https://github.com/numba/numba/pull/7177
    """

    setup_breakpoint(app, breakpoint, script, expected_line=expected_line)

    app.info_args()

    for arg in expected_args:
        app.child.expect(arg)

    variable, expected_print, expected_ptype, expected_whatis = expected_info

    app.print(variable)
    app.child.expect(expected_print)

    app.ptype(variable)
    app.child.expect(expected_ptype)

    app.whatis(variable)
    app.child.expect(expected_whatis)


@skip_no_numba055
def test_info_functions(app):
    expected_line = r"23\s+i = dppy.get_global_id\(0\)"
    setup_breakpoint(app, "simple_sum.py:23", expected_line=expected_line)

    app.info_functions("data_parallel_sum")

    app.child.expect(r"22:\s+.*__main__::data_parallel_sum\(.*\)")


def side_by_side_info_locals_case(api):
    return (
        {"NUMBA_OPT": 0},
        "side-by-side.py:27 if param_a == 5",
        f"side-by-side.py --api={api}",
        None,
        (
            r"param_c = 15",
            r"param_d = 2.5",
            r"result = 0",
        ),
        (),
    )


def side_by_side_2_info_locals_case(api):
    return (
        {"NUMBA_OPT": 0},
        "side-by-side-2.py:29 if param_a == 5",
        f"side-by-side-2.py --api={api}",
        None,
        (
            r"param_a = 5",
            r"param_b = 5",
            r"param_c = 15",
            r"param_d = 2.5",
            r"result = 0",
        ),
        (
            (
                r"a",
                r"\$1 = {meminfo = ",
                r"type = struct array\(float32, 1d, C\)",
                r"type = array\(float32, 1d, C\)",
            ),
        ),
    )


@skip_no_numba055
@pytest.mark.parametrize(
    "env, breakpoint, script, expected_line, expected_info_locals, expected_info",
    [
        (
            {"NUMBA_OPT": 0},
            "sum_local_vars.py:26",
            "sum_local_vars.py",
            r"26\s+c\[i\] = l1 \+ l2",
            (
                r"i = 0",
                r"l1 = [0-9]\.[0-9]{3}",
                r"l2 = [0-9]\.[0-9]{3}",
            ),
            (
                (
                    "a",
                    r"\$1 = {meminfo = ",
                    r"type = struct array\(float32, 1d, C\).*}\)",
                    r"type = array\(float32, 1d, C\) \({.*}\)",
                ),
                (
                    "l1",
                    r"\$2 = [0-9]\.[0-9]{3}",
                    r"type = float64",
                    r"type = float64",
                ),
                (
                    "l2",
                    r"\$3 = [0-9]\.[0-9]{3}",
                    r"type = float64",
                    r"type = float64",
                ),
            ),
        ),
        (
            {"NUMBA_OPT": 1},
            "sum_local_vars.py:26",
            "sum_local_vars.py",
            r"26\s+c\[i\] = l1 \+ l2",
            ("No locals.",),
            (),
        ),
        (
            {"NUMBA_EXTEND_VARIABLE_LIFETIMES": 1},
            "side-by-side.py:28",
            "side-by-side.py --api=numba-dppy-kernel",
            None,
            (r"param_c = 10", r"param_d = 0", r"result = 10"),
            (),
        ),
        (
            {"NUMBA_EXTEND_VARIABLE_LIFETIMES": 0},
            "side-by-side.py:28",
            "side-by-side.py --api=numba-dppy-kernel",
            None,
            (r"param_c = 0", r"param_d = 0", r"result = 10"),
            (),
        ),
        side_by_side_info_locals_case("numba"),
        side_by_side_info_locals_case("numba-dppy-kernel"),
        side_by_side_2_info_locals_case("numba"),
        side_by_side_2_info_locals_case("numba-dppy-kernel"),
    ],
)
def test_info_locals(
    app,
    env,
    breakpoint,
    script,
    expected_line,
    expected_info_locals,
    expected_info,
):
    """Test info locals with different environment variables.

    commands/local_variables_0
    commands/local_variables_1

    SAT-4454
    Provide information about variables (arrays).
    Issue: https://github.com/numba/numba/issues/7414
    Fix: https://github.com/numba/numba/pull/7177
         https://github.com/numba/numba/pull/7421
    """

    for varname, value in env.items():
        app.set_environment(varname, value)

    setup_breakpoint(app, breakpoint, script, expected_line=expected_line)

    app.info_locals()

    for variable in expected_info_locals:
        app.child.expect(variable)

    for info in expected_info:
        variable, expected_print, expected_ptype, expected_whatis = info

        app.print(variable)
        app.child.expect(expected_print)

        app.ptype(variable)
        app.child.expect(expected_ptype)

        app.whatis(variable)
        app.child.expect(expected_whatis)


def side_by_side_2_print_array_element_case(api):
    return (
        "side-by-side-2.py:29 if param_a == 5",
        f"side-by-side-2.py --api={api}",
        [(r"a.data[5]", r"\$1 = 5")],
    )


@pytest.mark.parametrize(
    "breakpoint, script, expected_info",
    [
        side_by_side_2_print_array_element_case("numba"),
        side_by_side_2_print_array_element_case("numba-dppy-kernel"),
    ],
)
def test_print_array_element(app, breakpoint, script, expected_info):
    """Test access to array elements"""

    setup_breakpoint(app, breakpoint, script)

    for info in expected_info:
        variable, expected_print = info

        app.print(variable)
        app.child.expect(expected_print)


def side_by_side_2_assignment_to_variable_case(api):
    return (
        "side-by-side-2.py:29 if param_a == 5",
        f"side-by-side-2.py --api={api}",
        [
            (r"param_c", r"\$1 = 15"),
            (r"param_c=150", r"\$2 = 150"),
            (r"param_c", r"\$3 = 150"),
            (r"i", r"\$4 = 5"),
            (r"i=50", r"\$5 = 50"),
            (r"i", r"\$6 = 50"),
        ],
    )


@pytest.mark.parametrize(
    "breakpoint, script, expected_info",
    [
        side_by_side_2_assignment_to_variable_case("numba"),
        side_by_side_2_assignment_to_variable_case("numba-dppy-kernel"),
    ],
)
def test_assignment_to_variable(app, breakpoint, script, expected_info):
    setup_breakpoint(app, breakpoint, script)

    for info in expected_info:
        variable, expected_print = info

        app.print(variable)
        app.child.expect(expected_print)

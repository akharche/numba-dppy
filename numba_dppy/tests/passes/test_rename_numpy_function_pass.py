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


import numba
import numpy as np
import pytest
from numba import typeof
from numba.core import compiler, cpu, typing
from numba.core.typed_passes import AnnotateTypes, NopythonTypeInference

import numba_dppy
from numba_dppy.core.passes.rename_numpy_functions_pass import (
    DPPYRewriteNdarrayFunctions,
    DPPYRewriteOverloadedNumPyFunctions,
)
from numba_dppy.tests._helper import skip_no_dpnp


class MyPipeline(object):
    def __init__(self, test_ir, args):
        self.state = compiler.StateDict()
        self.state.typingctx = typing.Context()
        self.state.targetctx = cpu.CPUContext(self.state.typingctx)
        self.state.func_ir = test_ir
        self.state.func_id = test_ir.func_id
        self.state.args = args
        self.state.return_type = None
        self.state.locals = dict()
        self.state.status = None
        self.state.lifted = dict()
        self.state.lifted_from = None
        self.state.typingctx.refresh()
        self.state.targetctx.refresh()


def check_equivalent(expected_ir, got_ir):
    expected_block_body = expected_ir.blocks[0].body
    got_block_body = got_ir.blocks[0].body

    if len(expected_block_body) != len(got_block_body):
        return False

    for i in range(len(expected_block_body)):
        expected_stmt = expected_block_body[i]
        got_stmt = got_block_body[i]
        if type(expected_stmt) != type(got_stmt):
            return False
        else:
            if isinstance(expected_stmt, numba.core.ir.Assign):
                if isinstance(expected_stmt.value, numba.core.ir.Global):
                    if (
                        expected_stmt.value.name != got_stmt.value.name
                        and expected_stmt.value.name != "numba_dppy"
                    ):
                        return False
                elif isinstance(expected_stmt.value, numba.core.ir.Expr):
                    # should get "dpnp" and "sum" as attr
                    if expected_stmt.value.op == "getattr":
                        if expected_stmt.value.attr != got_stmt.value.attr:
                            return False
    return True


class TestRenameNumpyFunctionsPass:
    def test_rename_numpy(self):
        def expected(a):
            return numba_dppy.dpnp.sum(a)

        def got(a):
            return np.sum(a)

        expected_ir = compiler.run_frontend(expected)
        got_ir = compiler.run_frontend(got)

        pipeline = MyPipeline(got_ir, None)

        rewrite_numpy_functions_pass = DPPYRewriteOverloadedNumPyFunctions()
        rewrite_numpy_functions_pass.run_pass(pipeline.state)

        assert check_equivalent(expected_ir, pipeline.state.func_ir)


@skip_no_dpnp
class TestRenameNdarrayFunctionsPass:
    def test_rename_ndarray(self):
        def expected(a):
            return numba_dppy.dpnp.sum(a)

        def got(a):
            return a.sum()

        expected_ir = compiler.run_frontend(expected)
        got_ir = compiler.run_frontend(got)

        a = np.arange(10)
        args = [a]
        argtypes = [typeof(x) for x in args]

        pipeline = MyPipeline(got_ir, argtypes)

        tyinfer_pass = NopythonTypeInference()
        tyinfer_pass.run_pass(pipeline.state)

        annotate_ty_pass = AnnotateTypes()
        annotate_ty_pass.run_pass(pipeline.state)

        rewrite_ndarray_functions_pass = DPPYRewriteNdarrayFunctions()
        rewrite_ndarray_functions_pass.run_pass(pipeline.state)

        assert check_equivalent(expected_ir, pipeline.state.func_ir)

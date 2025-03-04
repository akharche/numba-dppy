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

import operator
import warnings
import weakref
from collections import deque, namedtuple
from contextlib import contextmanager

import numba
import numpy as np
from numba.core import errors, funcdesc, ir, lowering, types, typing, utils
from numba.core.compiler_machinery import (
    AnalysisPass,
    FunctionPass,
    LoweringPass,
    register_pass,
)
from numba.core.errors import (
    LiteralTypingError,
    LoweringError,
    TypingError,
    new_error_context,
)
from numba.core.ir_utils import remove_dels
from numba.parfors.parfor import Parfor
from numba.parfors.parfor import ParforPass as _parfor_ParforPass
from numba.parfors.parfor import PreParforPass as _parfor_PreParforPass
from numba.parfors.parfor import swap_functions_map

from numba_dppy import config

from .dppy_lowerer import DPPYLower


@register_pass(mutates_CFG=True, analysis_only=False)
class DPPYConstantSizeStaticLocalMemoryPass(FunctionPass):

    _name = "dppy_constant_size_static_local_memory_pass"

    def __init__(self):
        FunctionPass.__init__(self)

    def run_pass(self, state):
        """
        Preprocessing for data-parallel computations.
        """
        # Ensure we have an IR and type information.
        assert state.func_ir
        func_ir = state.func_ir

        _DEBUG = False

        if _DEBUG:
            print(
                "Checks if size of OpenCL local address space alloca is a compile-time constant.".center(
                    80, "-"
                )
            )
            print(func_ir.dump())

        work_list = list(func_ir.blocks.items())
        while work_list:
            label, block = work_list.pop()
            for i, instr in enumerate(block.body):
                if isinstance(instr, ir.Assign):
                    expr = instr.value
                    if isinstance(expr, ir.Expr):
                        if expr.op == "call":
                            find_var = block.find_variable_assignment(
                                expr.func.name
                            )
                            if find_var is not None:
                                call_node = find_var.value
                                if (
                                    isinstance(call_node, ir.Expr)
                                    and call_node.op == "getattr"
                                    and call_node.attr == "array"
                                ):
                                    # let's check if it is from numba_dppy.local
                                    attr_node = block.find_variable_assignment(
                                        call_node.value.name
                                    ).value
                                    if (
                                        isinstance(attr_node, ir.Expr)
                                        and attr_node.op == "getattr"
                                        and attr_node.attr == "local"
                                    ):

                                        arg = None
                                        # at first look in keyword arguments to get the shape, which has to be
                                        # constant
                                        if expr.kws:
                                            for _arg in expr.kws:
                                                if _arg[0] == "shape":
                                                    arg = _arg[1]

                                        if not arg:
                                            arg = expr.args[0]

                                        error = False
                                        # arg can be one constant or a tuple of constant items
                                        arg_type = func_ir.get_definition(
                                            arg.name
                                        )
                                        if isinstance(arg_type, ir.Expr):
                                            # we have a tuple
                                            for item in arg_type.items:
                                                if not isinstance(
                                                    func_ir.get_definition(
                                                        item.name
                                                    ),
                                                    ir.Const,
                                                ):
                                                    error = True
                                                    break

                                        else:
                                            if not isinstance(
                                                func_ir.get_definition(
                                                    arg.name
                                                ),
                                                ir.Const,
                                            ):
                                                error = True
                                                break

                                        if error:
                                            warnings.warn_explicit(
                                                "The size of the Local memory has to be constant",
                                                errors.NumbaError,
                                                state.func_id.filename,
                                                state.func_id.firstlineno,
                                            )
                                            raise

        if config.DEBUG or config.DUMP_IR:
            name = state.func_ir.func_id.func_qualname
            print(("IR DUMP: %s" % name).center(80, "-"))
            state.func_ir.dump()

        return True


@register_pass(mutates_CFG=True, analysis_only=False)
class DPPYPreParforPass(FunctionPass):

    _name = "dppy_pre_parfor_pass"

    def __init__(self):
        FunctionPass.__init__(self)

    def run_pass(self, state):
        """
        Preprocessing for data-parallel computations.
        """

        # Ensure we have an IR and type information.
        assert state.func_ir
        functions_map = swap_functions_map.copy()
        functions_map.pop(("dot", "numpy"), None)
        functions_map.pop(("sum", "numpy"), None)
        functions_map.pop(("prod", "numpy"), None)
        functions_map.pop(("argmax", "numpy"), None)
        functions_map.pop(("max", "numpy"), None)
        functions_map.pop(("argmin", "numpy"), None)
        functions_map.pop(("min", "numpy"), None)
        functions_map.pop(("mean", "numpy"), None)

        preparfor_pass = _parfor_PreParforPass(
            state.func_ir,
            state.type_annotation.typemap,
            state.type_annotation.calltypes,
            state.typingctx,
            state.targetctx,
            state.flags.auto_parallel,
            state.parfor_diagnostics.replaced_fns,
            replace_functions_map=functions_map,
        )

        preparfor_pass.run()

        if config.DEBUG or config.DUMP_IR:
            name = state.func_ir.func_id.func_qualname
            print(("IR DUMP: %s" % name).center(80, "-"))
            state.func_ir.dump()

        return True


@register_pass(mutates_CFG=True, analysis_only=False)
class DPPYParforPass(FunctionPass):

    _name = "dppy_parfor_pass"

    def __init__(self):
        FunctionPass.__init__(self)

    def run_pass(self, state):
        """
        Convert data-parallel computations into Parfor nodes
        """
        # Ensure we have an IR and type information.
        assert state.func_ir
        parfor_pass = _parfor_ParforPass(
            state.func_ir,
            state.type_annotation.typemap,
            state.type_annotation.calltypes,
            state.return_type,
            state.typingctx,
            state.targetctx,
            state.flags.auto_parallel,
            state.flags,
            state.metadata,
            state.parfor_diagnostics,
        )

        parfor_pass.run()

        if config.DEBUG or config.DUMP_IR:
            name = state.func_ir.func_id.func_qualname
            print(("IR DUMP: %s" % name).center(80, "-"))
            state.func_ir.dump()

        return True


@contextmanager
def fallback_context(state, msg):
    """
    Wraps code that would signal a fallback to object mode
    """
    try:
        yield
    except Exception as e:
        if not state.status.can_fallback:
            raise
        else:
            if utils.PYVERSION >= (3,):
                # Clear all references attached to the traceback
                e = e.with_traceback(None)
            # this emits a warning containing the error message body in the
            # case of fallback from npm to objmode
            loop_lift = "" if state.flags.enable_looplift else "OUT"
            msg_rewrite = (
                "\nCompilation is falling back to object mode "
                "WITH%s looplifting enabled because %s" % (loop_lift, msg)
            )
            warnings.warn_explicit(
                "%s due to: %s" % (msg_rewrite, e),
                errors.NumbaWarning,
                state.func_id.filename,
                state.func_id.firstlineno,
            )
            raise


@register_pass(mutates_CFG=True, analysis_only=False)
class SpirvFriendlyLowering(LoweringPass):

    _name = "spirv_friendly_lowering"

    def __init__(self):
        LoweringPass.__init__(self)

    def run_pass(self, state):
        if state.library is None:
            codegen = state.targetctx.codegen()
            state.library = codegen.create_library(state.func_id.func_qualname)
            # Enable object caching upfront, so that the library can
            # be later serialized.
            state.library.enable_object_caching()

        targetctx = state.targetctx

        library = state.library
        interp = state.func_ir
        typemap = state.typemap
        restype = state.return_type
        calltypes = state.calltypes
        flags = state.flags
        metadata = state.metadata

        msg = "Function %s failed at nopython " "mode lowering" % (
            state.func_id.func_name,
        )
        with fallback_context(state, msg):
            kwargs = {}

            # for support numba 0.54 and <=0.55.0dev0=*_469
            if hasattr(flags, "get_mangle_string"):
                kwargs["abi_tags"] = flags.get_mangle_string()

            # Lowering
            fndesc = (
                funcdesc.PythonFunctionDescriptor.from_specialized_function(
                    interp,
                    typemap,
                    restype,
                    calltypes,
                    mangler=targetctx.mangler,
                    inline=flags.forceinline,
                    noalias=flags.noalias,
                    **kwargs,
                )
            )

            with targetctx.push_code_library(library):
                lower = DPPYLower(
                    targetctx, library, fndesc, interp, metadata=metadata
                )
                lower.lower()
                if not flags.no_cpython_wrapper:
                    lower.create_cpython_wrapper(flags.release_gil)

                env = lower.env
                call_helper = lower.call_helper
                del lower

            from numba.core.compiler import _LowerResult  # TODO: move this

            if flags.no_compile:
                state["cr"] = _LowerResult(
                    fndesc, call_helper, cfunc=None, env=env
                )
            else:
                # Prepare for execution
                cfunc = targetctx.get_executable(library, fndesc, env)
                # Insert native function for use by other jitted-functions.
                # We also register its library to allow for inlining.
                targetctx.insert_user_function(cfunc, fndesc, [library])
                state["cr"] = _LowerResult(
                    fndesc, call_helper, cfunc=cfunc, env=env
                )

        return True


@register_pass(mutates_CFG=True, analysis_only=False)
class DPPYNoPythonBackend(FunctionPass):

    _name = "nopython_backend"

    def __init__(self):
        FunctionPass.__init__(self)

    def run_pass(self, state):
        """
        Back-end: Generate LLVM IR from Numba IR, compile to machine code
        """

        lowered = state["cr"]
        signature = typing.signature(state.return_type, *state.args)

        from numba.core.compiler import compile_result

        state.cr = compile_result(
            typing_context=state.typingctx,
            target_context=state.targetctx,
            entry_point=lowered.cfunc,
            typing_error=state.status.fail_reason,
            type_annotation=state.type_annotation,
            library=state.library,
            call_helper=lowered.call_helper,
            signature=signature,
            objectmode=False,
            lifted=state.lifted,
            fndesc=lowered.fndesc,
            environment=lowered.env,
            metadata=state.metadata,
            reload_init=state.reload_init,
        )

        remove_dels(state.func_ir.blocks)

        return True


@register_pass(mutates_CFG=False, analysis_only=True)
class DPPYDumpParforDiagnostics(AnalysisPass):

    _name = "dump_parfor_diagnostics"

    def __init__(self):
        AnalysisPass.__init__(self)

    def run_pass(self, state):
        # if state.flags.auto_parallel.enabled: //add in condition flag for kernels
        if config.OFFLOAD_DIAGNOSTICS:
            if state.parfor_diagnostics is not None:
                state.parfor_diagnostics.dump(config.PARALLEL_DIAGNOSTICS)
            else:
                raise RuntimeError("Diagnostics failed.")
        return True

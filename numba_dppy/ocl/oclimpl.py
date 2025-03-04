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
from functools import reduce

import dpctl
import llvmlite.binding as ll
import llvmlite.llvmpy.core as lc
from llvmlite import ir
from llvmlite.llvmpy.core import Type
from numba.core import cgutils, types
from numba.core.imputils import Registry
from numba.core.itanium_mangler import mangle, mangle_c, mangle_type
from numba.core.typing.npydecl import parse_dtype

from numba_dppy import config, target
from numba_dppy.codegen import SPIR_DATA_LAYOUT
from numba_dppy.dppy_array_type import DPPYArray
from numba_dppy.ocl.atomics import atomic_helper
from numba_dppy.utils import address_space

from . import stubs

registry = Registry()
lower = registry.lower

_void_value = lc.Constant.null(lc.Type.pointer(lc.Type.int(8)))

# -----------------------------------------------------------------------------


def _declare_function(context, builder, name, sig, cargs, mangler=mangle_c):
    """Insert declaration for a opencl builtin function.
    Uses the Itanium mangler.

    Args
    ----
    context: target context

    builder: llvm builder

    name: str
        symbol name

    sig: signature
        function signature of the symbol being declared

    cargs: sequence of str
        C type names for the arguments

    mangler: a mangler function
        function to use to mangle the symbol

    """
    mod = builder.module
    if sig.return_type == types.void:
        llretty = lc.Type.void()
    else:
        llretty = context.get_value_type(sig.return_type)
    llargs = [context.get_value_type(t) for t in sig.args]
    fnty = Type.function(llretty, llargs)
    mangled = mangler(name, cargs)
    fn = cgutils.get_or_insert_function(mod, fnty, mangled)
    fn.calling_convention = target.CC_SPIR_FUNC
    return fn


@lower(stubs.get_global_id, types.uint32)
def get_global_id_impl(context, builder, sig, args):
    [dim] = args
    get_global_id = _declare_function(
        context, builder, "get_global_id", sig, ["unsigned int"]
    )
    res = builder.call(get_global_id, [dim])
    return context.cast(builder, res, types.uintp, types.intp)


@lower(stubs.get_local_id, types.uint32)
def get_local_id_impl(context, builder, sig, args):
    [dim] = args
    get_local_id = _declare_function(
        context, builder, "get_local_id", sig, ["unsigned int"]
    )
    res = builder.call(get_local_id, [dim])
    return context.cast(builder, res, types.uintp, types.intp)


@lower(stubs.get_group_id, types.uint32)
def get_group_id_impl(context, builder, sig, args):
    [dim] = args
    get_group_id = _declare_function(
        context, builder, "get_group_id", sig, ["unsigned int"]
    )
    res = builder.call(get_group_id, [dim])
    return context.cast(builder, res, types.uintp, types.intp)


@lower(stubs.get_num_groups, types.uint32)
def get_num_groups_impl(context, builder, sig, args):
    [dim] = args
    get_num_groups = _declare_function(
        context, builder, "get_num_groups", sig, ["unsigned int"]
    )
    res = builder.call(get_num_groups, [dim])
    return context.cast(builder, res, types.uintp, types.intp)


@lower(stubs.get_work_dim)
def get_work_dim_impl(context, builder, sig, args):
    get_work_dim = _declare_function(
        context, builder, "get_work_dim", sig, ["void"]
    )
    res = builder.call(get_work_dim, [])
    return res


@lower(stubs.get_global_size, types.uint32)
def get_global_size_impl(context, builder, sig, args):
    [dim] = args
    get_global_size = _declare_function(
        context, builder, "get_global_size", sig, ["unsigned int"]
    )
    res = builder.call(get_global_size, [dim])
    return context.cast(builder, res, types.uintp, types.intp)


@lower(stubs.get_local_size, types.uint32)
def get_local_size_impl(context, builder, sig, args):
    [dim] = args
    get_local_size = _declare_function(
        context, builder, "get_local_size", sig, ["unsigned int"]
    )
    res = builder.call(get_local_size, [dim])
    return context.cast(builder, res, types.uintp, types.intp)


@lower(stubs.barrier, types.uint32)
def barrier_one_arg_impl(context, builder, sig, args):
    [flags] = args
    barrier = _declare_function(
        context, builder, "barrier", sig, ["unsigned int"]
    )
    builder.call(barrier, [flags])
    return _void_value


@lower(stubs.barrier)
def barrier_no_arg_impl(context, builder, sig, args):
    assert not args
    sig = types.void(types.uint32)
    barrier = _declare_function(
        context, builder, "barrier", sig, ["unsigned int"]
    )
    flags = context.get_constant(types.uint32, stubs.CLK_GLOBAL_MEM_FENCE)
    builder.call(barrier, [flags])
    return _void_value


@lower(stubs.mem_fence, types.uint32)
def mem_fence_impl(context, builder, sig, args):
    [flags] = args
    mem_fence = _declare_function(
        context, builder, "mem_fence", sig, ["unsigned int"]
    )
    builder.call(mem_fence, [flags])
    return _void_value


@lower(stubs.sub_group_barrier)
def sub_group_barrier_impl(context, builder, sig, args):
    assert not args
    sig = types.void(types.uint32)
    barrier = _declare_function(
        context, builder, "barrier", sig, ["unsigned int"]
    )
    flags = context.get_constant(types.uint32, stubs.CLK_LOCAL_MEM_FENCE)
    builder.call(barrier, [flags])
    return _void_value


def insert_and_call_atomic_fn(
    context, builder, sig, fn_type, dtype, ptr, val, addrspace
):
    ll_p = None
    name = ""
    if dtype.name == "float32":
        ll_val = ir.FloatType()
        ll_p = ll_val.as_pointer()
        if fn_type == "add":
            name = "numba_dppy_atomic_add_f32"
        elif fn_type == "sub":
            name = "numba_dppy_atomic_sub_f32"
        else:
            raise TypeError("Operation type is not supported %s" % (fn_type))
    elif dtype.name == "float64":
        if True:
            ll_val = ir.DoubleType()
            ll_p = ll_val.as_pointer()
            if fn_type == "add":
                name = "numba_dppy_atomic_add_f64"
            elif fn_type == "sub":
                name = "numba_dppy_atomic_sub_f64"
            else:
                raise TypeError(
                    "Operation type is not supported %s" % (fn_type)
                )
    else:
        raise TypeError(
            "Atomic operation is not supported for type %s" % (dtype.name)
        )

    if addrspace == address_space.LOCAL:
        name = name + "_local"
    else:
        name = name + "_global"

    assert ll_p is not None
    assert name != ""
    ll_p.addrspace = address_space.GENERIC

    mod = builder.module
    if sig.return_type == types.void:
        llretty = lc.Type.void()
    else:
        llretty = context.get_value_type(sig.return_type)

    llargs = [ll_p, context.get_value_type(sig.args[2])]
    fnty = ir.FunctionType(llretty, llargs)

    fn = cgutils.get_or_insert_function(mod, fnty, name)
    fn.calling_convention = target.CC_SPIR_FUNC

    generic_ptr = context.addrspacecast(builder, ptr, address_space.GENERIC)

    return builder.call(fn, [generic_ptr, val])


def native_atomic_add(context, builder, sig, args):
    aryty, indty, valty = sig.args
    ary, inds, val = args
    dtype = aryty.dtype

    if indty == types.intp:
        indices = [inds]  # just a single integer
        indty = [indty]
    else:
        indices = cgutils.unpack_tuple(builder, inds, count=len(indty))
        indices = [
            context.cast(builder, i, t, types.intp)
            for t, i in zip(indty, indices)
        ]

    if dtype != valty:
        raise TypeError("expecting %s but got %s" % (dtype, valty))

    if aryty.ndim != len(indty):
        raise TypeError(
            "indexing %d-D array with %d-D index" % (aryty.ndim, len(indty))
        )

    lary = context.make_array(aryty)(context, builder, ary)
    ptr = cgutils.get_item_pointer(context, builder, aryty, lary, indices)

    if dtype == types.float32 or dtype == types.float64:
        context.extra_compile_options[target.LLVM_SPIRV_ARGS] = [
            "--spirv-ext=+SPV_EXT_shader_atomic_float_add"
        ]
        name = "__spirv_AtomicFAddEXT"
    elif dtype == types.int32 or dtype == types.int64:
        name = "__spirv_AtomicIAdd"
    else:
        raise TypeError("Unsupported type")

    assert name != ""

    ptr_type = context.get_value_type(dtype).as_pointer()
    ptr_type.addrspace = aryty.addrspace

    retty = context.get_value_type(sig.return_type)
    spirv_fn_arg_types = [
        ptr_type,
        ir.IntType(32),
        ir.IntType(32),
        context.get_value_type(sig.args[2]),
    ]

    from numba_dppy import extended_numba_itanium_mangler as ext_itanium_mangler

    numba_ptr_ty = types.CPointer(dtype, addrspace=ptr_type.addrspace)
    mangled_fn_name = ext_itanium_mangler.mangle(
        name,
        [
            numba_ptr_ty,
            "__spv.Scope.Flag",
            "__spv.MemorySemanticsMask.Flag",
            valty,
        ],
    )

    fnty = ir.FunctionType(retty, spirv_fn_arg_types)
    fn = cgutils.get_or_insert_function(builder.module, fnty, mangled_fn_name)
    fn.calling_convention = target.CC_SPIR_FUNC

    sycl_memory_order = atomic_helper.sycl_memory_order.relaxed
    sycl_memory_scope = atomic_helper.sycl_memory_scope.device
    spirv_scope = atomic_helper.get_scope(sycl_memory_scope)
    spirv_memory_semantics_mask = atomic_helper.get_memory_semantics_mask(
        sycl_memory_order
    )
    fn_args = [
        ptr,
        context.get_constant(types.int32, spirv_scope),
        context.get_constant(types.int32, spirv_memory_semantics_mask),
        val,
    ]

    return builder.call(fn, fn_args)


@lower(stubs.atomic.add, types.Array, types.intp, types.Any)
@lower(stubs.atomic.add, types.Array, types.UniTuple, types.Any)
@lower(stubs.atomic.add, types.Array, types.Tuple, types.Any)
def atomic_add_tuple(context, builder, sig, args):
    device_type = dpctl.get_current_queue().sycl_device.device_type
    dtype = sig.args[0].dtype

    if dtype == types.float32 or dtype == types.float64:
        if (
            device_type == dpctl.device_type.gpu
            and config.NATIVE_FP_ATOMICS == 1
        ):
            return native_atomic_add(context, builder, sig, args)
        else:
            # Currently, DPCPP only supports native floating point
            # atomics for GPUs.
            return atomic_add(context, builder, sig, args, "add")
    elif dtype == types.int32 or dtype == types.int64:
        return native_atomic_add(context, builder, sig, args)
    else:
        raise TypeError("Atomic operation on unsupported type %s" % dtype)


def atomic_sub_wrapper(context, builder, sig, args):
    # dpcpp yet does not support ``__spirv_AtomicFSubEXT``. To support atomic.sub we
    # reuse atomic.add and negate the value. For example, atomic.add(A, index, -val) is
    # equivalent to atomic.sub(A, index, val).
    val = args[2]
    new_val = cgutils.alloca_once(
        builder,
        context.get_value_type(sig.args[2]),
        size=context.get_constant(types.uintp, 1),
        name="new_val_0",
    )
    val_dtype = sig.args[2]
    if val_dtype == types.float32 or val_dtype == types.float64:
        builder.store(
            builder.fmul(val, context.get_constant(sig.args[2], -1)), new_val
        )
    elif val_dtype == types.int32 or val_dtype == types.int64:
        builder.store(
            builder.mul(val, context.get_constant(sig.args[2], -1)), new_val
        )
    else:
        raise TypeError("Unsupported type %s" % val_dtype)

    args[2] = builder.load(new_val)

    return native_atomic_add(context, builder, sig, args)


@lower(stubs.atomic.sub, types.Array, types.intp, types.Any)
@lower(stubs.atomic.sub, types.Array, types.UniTuple, types.Any)
@lower(stubs.atomic.sub, types.Array, types.Tuple, types.Any)
def atomic_sub_tuple(context, builder, sig, args):
    device_type = dpctl.get_current_queue().sycl_device.device_type
    dtype = sig.args[0].dtype

    if dtype == types.float32 or dtype == types.float64:
        if (
            device_type == dpctl.device_type.gpu
            and config.NATIVE_FP_ATOMICS == 1
        ):
            return atomic_sub_wrapper(context, builder, sig, args)
        else:
            # Currently, DPCPP only supports native floating point
            # atomics for GPUs.
            return atomic_add(context, builder, sig, args, "sub")
    elif dtype == types.int32 or dtype == types.int64:
        return atomic_sub_wrapper(context, builder, sig, args)
    else:
        raise TypeError("Atomic operation on unsupported type %s" % dtype)


def atomic_add(context, builder, sig, args, name):
    from .atomics import atomic_support_present

    if atomic_support_present():
        context.extra_compile_options[target.LINK_ATOMIC] = True
        aryty, indty, valty = sig.args
        ary, inds, val = args
        dtype = aryty.dtype

        if indty == types.intp:
            indices = [inds]  # just a single integer
            indty = [indty]
        else:
            indices = cgutils.unpack_tuple(builder, inds, count=len(indty))
            indices = [
                context.cast(builder, i, t, types.intp)
                for t, i in zip(indty, indices)
            ]

        if dtype != valty:
            raise TypeError("expecting %s but got %s" % (dtype, valty))

        if aryty.ndim != len(indty):
            raise TypeError(
                "indexing %d-D array with %d-D index" % (aryty.ndim, len(indty))
            )

        lary = context.make_array(aryty)(context, builder, ary)
        ptr = cgutils.get_item_pointer(context, builder, aryty, lary, indices)

        if (
            isinstance(aryty, DPPYArray)
            and aryty.addrspace == address_space.LOCAL
        ):
            return insert_and_call_atomic_fn(
                context,
                builder,
                sig,
                name,
                dtype,
                ptr,
                val,
                address_space.LOCAL,
            )
        else:
            return insert_and_call_atomic_fn(
                context,
                builder,
                sig,
                name,
                dtype,
                ptr,
                val,
                address_space.GLOBAL,
            )
    else:
        raise ImportError(
            "Atomic support is not present, can not perform atomic_add"
        )


@lower(stubs.private.array, types.IntegerLiteral, types.Any)
def dppy_private_array_integer(context, builder, sig, args):
    length = sig.args[0].literal_value
    dtype = parse_dtype(sig.args[1])
    return _generic_array(
        context,
        builder,
        shape=(length,),
        dtype=dtype,
        symbol_name="_dppy_pmem",
        addrspace=address_space.PRIVATE,
    )


@lower(stubs.private.array, types.Tuple, types.Any)
@lower(stubs.private.array, types.UniTuple, types.Any)
def dppy_private_array_tuple(context, builder, sig, args):
    shape = [s.literal_value for s in sig.args[0]]
    dtype = parse_dtype(sig.args[1])
    return _generic_array(
        context,
        builder,
        shape=shape,
        dtype=dtype,
        symbol_name="_dppy_pmem",
        addrspace=address_space.PRIVATE,
    )


@lower(stubs.local.array, types.IntegerLiteral, types.Any)
def dppy_local_array_integer(context, builder, sig, args):
    length = sig.args[0].literal_value
    dtype = parse_dtype(sig.args[1])
    return _generic_array(
        context,
        builder,
        shape=(length,),
        dtype=dtype,
        symbol_name="_dppy_lmem",
        addrspace=address_space.LOCAL,
    )


@lower(stubs.local.array, types.Tuple, types.Any)
@lower(stubs.local.array, types.UniTuple, types.Any)
def dppy_local_array_tuple(context, builder, sig, args):
    shape = [s.literal_value for s in sig.args[0]]
    dtype = parse_dtype(sig.args[1])
    return _generic_array(
        context,
        builder,
        shape=shape,
        dtype=dtype,
        symbol_name="_dppy_lmem",
        addrspace=address_space.LOCAL,
    )


def _generic_array(context, builder, shape, dtype, symbol_name, addrspace):
    """
    This function allows us to create generic arrays in different
    address spaces.
    """
    elemcount = reduce(operator.mul, shape)
    lldtype = context.get_data_type(dtype)
    laryty = Type.array(lldtype, elemcount)

    if addrspace == address_space.LOCAL:
        lmod = builder.module

        # Create global variable in the requested address-space
        gvmem = lmod.add_global_variable(laryty, symbol_name, addrspace)

        if elemcount <= 0:
            raise ValueError("array length <= 0")
        else:
            gvmem.linkage = lc.LINKAGE_INTERNAL

        if dtype not in types.number_domain:
            raise TypeError("unsupported type: %s" % dtype)

    elif addrspace == address_space.PRIVATE:
        gvmem = cgutils.alloca_once(builder, laryty, name=symbol_name)
    else:
        raise NotImplementedError("addrspace {addrspace}".format(**locals()))

    # We need to add the addrspace to _make_array() function call as we want
    # the variable containing the reference of the memory to retain the
    # original address space of that memory. Before, we were casting the
    # memories allocated in local address space to global address space. This
    # approach does not let us identify the original address space of a memory
    # down the line.
    return _make_array(
        context, builder, gvmem, dtype, shape, addrspace=addrspace
    )


def _make_array(
    context,
    builder,
    dataptr,
    dtype,
    shape,
    layout="C",
    addrspace=address_space.GENERIC,
):
    ndim = len(shape)
    # Create array object
    aryty = DPPYArray(dtype=dtype, ndim=ndim, layout="C", addrspace=addrspace)
    ary = context.make_array(aryty)(context, builder)

    targetdata = _get_target_data(context)
    lldtype = context.get_data_type(dtype)
    itemsize = lldtype.get_abi_size(targetdata)
    # Compute strides
    rstrides = [itemsize]
    for i, lastsize in enumerate(reversed(shape[1:])):
        rstrides.append(lastsize * rstrides[-1])
    strides = [s for s in reversed(rstrides)]

    kshape = [context.get_constant(types.intp, s) for s in shape]
    kstrides = [context.get_constant(types.intp, s) for s in strides]

    context.populate_array(
        ary,
        data=builder.bitcast(dataptr, ary.data.type),
        shape=cgutils.pack_array(builder, kshape),
        strides=cgutils.pack_array(builder, kstrides),
        itemsize=context.get_constant(types.intp, itemsize),
        meminfo=None,
    )

    return ary._getvalue()


def _get_target_data(context):
    return ll.create_target_data(SPIR_DATA_LAYOUT[context.address_size])

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

"""This module provides utilities to interact with USM memory."""

import dpctl
import dpctl.memory as dpctl_mem
import numpy as np

from numba_dppy import config

supported_numpy_dtype = [
    np.int32,
    np.int64,
    np.uint32,
    np.int64,
    np.float32,
    np.float64,
]


def get_info_from_suai(obj):
    """
    Convenience function to gather information from __sycl_usm_array_interface__.

    Args:
        obj: Array with SUAI attribute.

    Returns:
        usm_mem: USM memory object.
        total_size: Total number of items in the array.
        shape: Shape of the array.
        ndim: Total number of dimensions.
        itemsize: Size of each item.
        strides: Stride of the array.
        dtype: Dtype of the array.
    """
    usm_mem = dpctl_mem.as_usm_memory(obj)

    assert usm_mem is not None

    shape = obj.__sycl_usm_array_interface__["shape"]
    total_size = np.prod(obj.__sycl_usm_array_interface__["shape"])
    ndim = len(obj.__sycl_usm_array_interface__["shape"])
    itemsize = np.dtype(obj.__sycl_usm_array_interface__["typestr"]).itemsize
    dtype = np.dtype(obj.__sycl_usm_array_interface__["typestr"])
    strides = obj.__sycl_usm_array_interface__["strides"]
    if strides is None:
        strides = [1] * ndim
        for i in reversed(range(1, ndim)):
            strides[i - 1] = strides[i] * shape[i]
        strides = tuple(strides)
    return usm_mem, total_size, shape, ndim, itemsize, strides, dtype


def has_usm_memory(obj):
    """
    Determine and return a SYCL device accesible object.

    as_usm_memory() converts Python object with `__sycl_usm_array_interface__`
    property to one of :class:`.MemoryUSMShared`, :class:`.MemoryUSMDevice`, or
    :class:`.MemoryUSMHost` instances. For more information please refer:
    https://github.com/IntelPython/dpctl/blob/0.8.0/dpctl/memory/_memory.pyx#L673

    Args:
        obj: Object to be tested and data copied from.

    Returns:
        A Python object allocated using USM memory if argument is already
        allocated using USM (zero-copy), None otherwise.
    """
    usm_mem = None
    try:
        usm_mem = dpctl_mem.as_usm_memory(obj)
    except Exception as e:
        if hasattr(obj, "base"):
            try:
                usm_mem = dpctl_mem.as_usm_memory(obj.base)
            except Exception as e:
                if config.DEBUG:
                    print(e)
        else:
            if config.DEBUG:
                print(e)

    return usm_mem


def copy_from_numpy_to_usm_obj(usm_allocated, obj):
    """
    Copy from supported objects to USM allocated data.

    This function copies the data of a supported Python type (only
    numpy.ndarray is supported at this point) into object that
    defines a __sycl_usm_array_interface__ attribute. For more information
    please refer to the specification of __sycl_usm_array_interface__:
    https://github.com/IntelPython/dpctl/wiki/Zero-copy-data-exchange-using-SYCL-USM#sycl-usm-array-interface

    Args:
        usm_allocated: An object that should define a
            __sycl_usm_array_interface__ dictionary. A TypeError is thrown
            if the object does not have such an attribute.
        obj (numpy.ndarray): Numpy ndarray, the data will be copied into.

    Raises:
        TypeError: If any argument is not of permitted type.
        ValueError:
            1. If size of data does not match.
            2. If obj is not C-contiguous.
    """
    usm_mem = has_usm_memory(usm_allocated)
    if usm_mem is None:
        raise TypeError("Source is not USM allocated.")

    if not isinstance(obj, np.ndarray):
        raise TypeError(
            "Obj is not USM allocated and is not of type "
            "numpy.ndarray. Obj type: %s" % (type(obj))
        )

    if obj.dtype not in [np.dtype(typ) for typ in supported_numpy_dtype]:
        raise ValueError(
            "dtype is not supprted. Supported dtypes "
            "are: %s" % (supported_numpy_dtype)
        )

    if not obj.flags.c_contiguous:
        raise ValueError(
            "Only C-contiguous numpy.ndarray is currently supported!"
        )

    size = np.prod(obj.shape)
    if usm_mem.size != (obj.dtype.itemsize * size):
        raise ValueError(
            "Size (Bytes) of data does not match. USM allocated "
            "memory size %d, supported object size: %d"
            % (usm_mem.size, (obj.dtype.itemsize * size))
        )

    obj_memview = memoryview(obj)
    obj_memview = obj_memview.cast("B")
    usm_mem.copy_from_host(obj_memview)


def copy_to_numpy_from_usm_obj(usm_allocated, obj):
    """
    Copy from USM allocated data to supported objects.

    Args:
        usm_allocated: An object that should define a
            __sycl_usm_array_interface__ dictionary. A TypeError is thrown
            if the object does not have such an attribute.
        obj (numpy.ndarray): Numpy ndarray, the data will be copied into.


    Raises:
        TypeError: If any argument is not of permitted type.
        ValueError: If size of data does not match.
    """
    usm_mem = has_usm_memory(usm_allocated)
    if usm_mem is None:
        raise TypeError("Source is not USM allocated.")

    if not isinstance(obj, np.ndarray):
        raise TypeError(
            "Obj is not USM allocated and is not of type "
            "numpy.ndarray. Obj type: %s" % (type(obj))
        )

    if obj.dtype not in [np.dtype(typ) for typ in supported_numpy_dtype]:
        raise ValueError(
            "dtype is not supprted. Supported dtypes "
            "are: %s" % (supported_numpy_dtype)
        )

    size = np.prod(obj.shape)
    if usm_mem.size != (obj.dtype.itemsize * size):
        raise ValueError(
            "Size (Bytes) of data does not match. USM allocated "
            "memory size %d, supported object size: %d"
            % (usm_mem.size, (obj.dtype.itemsize * size))
        )

    obj_memview = memoryview(obj)
    obj_memview = obj_memview.cast("B")
    usm_mem.copy_to_host(obj_memview)


def as_usm_obj(obj, queue=None, usm_type="shared", copy=True):
    """
    Determine and return a SYCL device accesible object.

    We try to determine if the provided object defines a valid
    __sycl_usm_array_interface__ dictionary.
    If not, we create a USM memory of `usm_type` and try to copy the data
    `obj` holds. Only numpy.ndarray is supported currently as `obj` if
    the object is not already allocated using USM.

    Args:
        obj: Object to be tested and data copied from.
        usm_type: USM type used in case obj is not already allocated using USM.
        queue (dpctl.SyclQueue): SYCL queue to be used to allocate USM
            memory in case obj is not already USM allocated.
        copy (bool): Flag to determine if we copy data from obj.

    Returns:
        A Python object allocated using USM memory.

    Raises:
        TypeError:
            1. If obj is not allocated on USM memory or is not of type
               numpy.ndarray, TypeError is raised.
            2. If queue is not of type dpctl.SyclQueue.
        ValueError:
            1. In case obj is not USM allocated, users need to pass
               the SYCL queue to be used for creating new memory. ValuieError
               is raised if queue argument is not provided.
            2. If usm_type is not valid.
            3. If dtype of the passed ndarray(obj) is not supported.
    """
    usm_mem = has_usm_memory(obj)

    if queue is None:
        raise ValueError(
            "Queue can not be None. Please provide the SYCL queue to be used."
        )
    if not isinstance(queue, dpctl.SyclQueue):
        raise TypeError(
            "queue has to be of dpctl.SyclQueue type. Got %s" % (type(queue))
        )

    if usm_mem is None:
        if not isinstance(obj, np.ndarray):
            raise TypeError(
                "Obj is not USM allocated and is not of type "
                "numpy.ndarray. Obj type: %s" % (type(obj))
            )

        if obj.dtype not in [np.dtype(typ) for typ in supported_numpy_dtype]:
            raise ValueError(
                "dtype is not supprted. Supported dtypes "
                "are: %s" % (supported_numpy_dtype)
            )

        size = np.prod(obj.shape)
        if usm_type == "shared":
            usm_mem = dpctl_mem.MemoryUSMShared(
                size * obj.dtype.itemsize, queue=queue
            )
        elif usm_type == "device":
            usm_mem = dpctl_mem.MemoryUSMDevice(
                size * obj.dtype.itemsize, queue=queue
            )
        elif usm_type == "host":
            usm_mem = dpctl_mem.MemoryUSMHost(
                size * obj.dtype.itemsize, queue=queue
            )
        else:
            raise ValueError(
                "Supported usm_type are: 'shared', "
                "'device' and 'host'. Provided: %s" % (usm_type)
            )

        if copy:
            # Copy data from numpy.ndarray
            copy_from_numpy_to_usm_obj(usm_mem, obj)

    return usm_mem

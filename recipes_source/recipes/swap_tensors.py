"""
在 ``nn.Module`` 中为 ``load_state_dict`` 和张量子类提供扩展点
===============================================================================
**作者:** `Mikayla Gawarecki <https://github.com/mikaylagawarecki>`_

本教程介绍了一个新的实用函数 ``torch.utils.swap_tensors``，
以及在 ``nn.Module`` 中集成它的两个新扩展点:

* ``nn.Module.to()`` 和相关方法
* ``nn.Module.load_state_dict()``

.. note::
    本教程需要 PyTorch 2.3.0 或更高版本。
"""

###############################################################################
# ``torch.utils.swap_tensors``
# ----------------------------
# ``torch.utils.swap_tensors``(以下简称为 ``swap_tensors``) 是一个
# 实用函数,它接受两个 Python 张量并交换它们。

import torch
import torch.nn as nn

t1 = torch.arange(2)
t2 = torch.arange(3)
print(f"交换前, t1: {t1}, t2: {t2}")
torch.utils.swap_tensors(t1, t2)
print(f"交换后, t1: {t1}, t2: {t2}")

################################################################################
# 更具体地说, ``swap_tensors`` 交换了两个张量的 Python ``__class__``、``__dict__``
# 和 ``__slots__``,以及它们相关的 ``at::Tensor``。
#
#
# 应用于 ``nn.Module``
# ----------------------------
# 当 ``nn.Module`` 之外的 Python 对象持有该模块参数的引用时,此实用函数就很有用。
# 如果 ``nn.Module`` 就地修改了任何参数,持有这些参数引用的对象将无法看到更改。
# 一个典型的例子是优化器,它持有 ``nn.Module`` 参数的引用。
# 这会导致一个潜在的正确性问题,即 ``optimizer.step()`` 会无错误运行,
# 但 ``nn.Module`` 的权重不会被更新。

mod = torch.nn.Linear(1, 2, bias=False)
optimizer = torch.optim.SGD(mod.parameters())
print(f"mod 中的权重: {mod.weight}")
print(f"优化器中的权重: {optimizer.param_groups[0]['params']}")
mod.weight = torch.nn.Parameter(2 * mod.weight)
print(f"mod 中的权重: {mod.weight}")
print(f"优化器中的权重: {optimizer.param_groups[0]['params']}")

################################################################################
# ``nn.Module.to()`` 和相关方法
# --------------------------------------
# 这包括改变模块设备的方法(如 ``nn.Module.cpu()``)、
# 改变模块 ``dtype`` 的方法(如 ``nn.Module.float()``)、
# 以及允许模块实例化的方法(如 ``nn.Module.to_empty()``)。
#
# 乍一看,这些方法能够就地修改模块的参数可能看起来不太直观。
# 现有的方法是使用一种追溯到 PyTorch 最初几天的丑陋黑客手段。
#
# 值得注意的是,现有方法在以下情况下无法工作:
#
# * 使用 ``__torch_dispatch__`` 子类
# * ``param`` 和 ``new_param`` 的 Python ``type()`` 不同
# * 对于具有特殊 C++ 表示的张量(如稀疏张量和 ``XLA`` 张量)
#
# 在本教程的下一部分,我们将定义一个玩具 ``__torch_dispatch__`` 子类 ``MyQuantizedLinearWeight``
# 来表示量化的线性权重。在本教程的剩余部分,我们将使用这个子类进行说明。
# 为简洁起见,我们省略了大部分 ``__torch_dispatch__`` 实现。

aten = torch.ops.aten


class MyQuantizedLinearWeight(torch.Tensor):
    @staticmethod
    def __new__(cls, elem, scale):
        return torch.Tensor._make_wrapper_subclass(
            cls,
            elem.shape,
            dtype=elem.dtype,
            layout=elem.layout,
            device=elem.device,
            strides=elem.stride(),
            storage_offset=elem.storage_offset(),
        )

    def __init__(self, elem: torch.Tensor, scale: float):
        self.elem = elem
        self.scale = scale

    def __repr__(self):
        return f"MyQuantizedLinearWeight({self.elem}, scale={self.scale})"

    @classmethod
    def __torch_dispatch__(cls, func, types, args, kwargs):
        if func in (aten.detach.default, aten._to_copy.default):
            new_elem = func(args[0].elem, *args[1:], **kwargs)
            return cls(new_elem, args[0].scale)
        # 某些操作的实现将添加到 ``OP_TABLE``。
        # 为简洁起见,我们在此省略。
        OP_TABLE = dict()
        if func in OP_TABLE:
            return OP_TABLE[func](func, args, kwargs)
        raise NotImplementedError(f"不支持的函数 {func}")


#################################################################################
# 让我们创建一个 ``dtype`` 为 ``torch.float32`` 的 ``nn.Linear`` 层,
# 其权重是 ``MyQuantizedLinearWeight``。然后尝试将其转换为 ``torch.bfloat16``。
# 观察到权重的 ``dtype`` 如预期般改变了。但是子类的有效载荷(``elem``)的 ``dtype`` 没有改变。

m = nn.Linear(3, 5, dtype=torch.float32)
m.weight = torch.nn.Parameter(MyQuantizedLinearWeight(m.weight, 0.5))
print(f"之前: id(m.weight)={id(m.weight)}, id(m.bias)={id(m.bias)}")
m.bfloat16()
print(f"之后: id(m.weight)={id(m.weight)}, id(m.bias)={id(m.bias)}")
print(f"m.weight.dtype: {m.weight.dtype}")
print(f"m.weight.elem.dtype: {m.weight.elem.dtype}")
print(f"m.bias.dtype: {m.bias.dtype}")

################################################################################
# 为此,我们引入了一个全局配置 ``torch.__future__.set_swap_module_params_on_conversion``
# 它将使用 ``swap_tensors`` 交换模块的参数,同时保留 ``.data`` 设置中的引用。
# 设置此配置后,在转换期间将使用 ``swap_tensors``,从而确保有效载荷的 ``dtype`` 正确转换。

torch.__future__.set_swap_module_params_on_conversion(True)
m = nn.Linear(3, 5, dtype=torch.float32)
m.weight = torch.nn.Parameter(MyQuantizedLinearWeight(m.weight, 0.5))
print(f"之前: id(m.weight)={id(m.weight)}, id(m.bias)={id(m.bias)}")
m.bfloat16()
print(f"之后: id(m.weight)={id(m.weight)}, id(m.bias)={id(m.bias)}")
print(f"m.weight.dtype: {m.weight.dtype}")
print(f"m.weight.elem.dtype: {m.weight.elem.dtype}")
print(f"m.bias.dtype: {m.bias.dtype}")
torch.__future__.set_swap_module_params_on_conversion(False)

################################################################################
# ``nn.Module.load_state_dict()``
# --------------------------------
# 根据传递给 ``load_state_dict()`` 的 ``assign`` 关键字参数的值,
# 有两种方式加载 ``state_dict``：
#
# * ``assign=False``: 保留 ``module.param`` 的属性,只从 ``state_dict['param_name']`` 中获取值
# * ``assign=True``: 保留 ``state_dict['param_name']`` 的属性和值。
#
#
# 之前,这些分别是通过就地 ``copy_`` 和 ``__setattr__`` 实现的。
# 在现有实现中,每种方法都有自己的限制 - ``assign=False`` 要求 ``state_dict`` 中的参数类型
# 必须与模块中的参数类型相同,而 ``assign=True`` 要求在 ``nn.Module.load_state_dict()`` 之后
# 初始化任何持有模块参数引用的对象。
#
# 现在,我们通过在 ``load_state_dict()`` 中添加 ``swap_tensors`` 路径并引入新的扩展点
# ``torch.Tensor.module_load(self, other, assign=False)`` 来解决这两个限制。
# 当启用上述 ``__future__`` 时,我们可以使用 ``module_load`` 的 ``__torch_function__`` 处理程序
# 对 ``state_dict`` 中的值应用自定义转换。转换的结果将与模块中的参数交换。
#
# 在下面的示例中,我们将使用上面定义的 ``MyQuantizedLinearWeight`` 子类
# 来说明如何使用这些功能在加载 ``state_dict`` 时对线性层的权重应用自定义量化方案。
#
# 回顾一下,如果 ``self`` 或 ``other``(在本例中是 ``param`` 或 ``state_dict[param_key]``)
# 是 ``MyQuantizedLinearWeight`` 子类,则会调用 ``module_load`` 的 ``__torch_function__`` 处理程序。
#
# 假设我们期望 ``state_dict`` 包含普通张量,而模块包含 ``MyQuantizedLinearWeight`` 参数,
# 我们希望将 ``state_dict`` 中的张量转换为子类。那么我们可以为 ``torch.Tensor.module_load`` 定义
# 一个 ``__torch_function__`` 处理程序,如下所示:


@classmethod
def custom_torch_function(cls, func, types, args=(), kwargs=None):
    kwargs = {} if kwargs is None else kwargs

    if func is torch.Tensor.module_load:
        dest, src = args[0], args[1]
        assert type(dest) == cls and type(src) == torch.Tensor
        return MyQuantizedLinearWeight(src, dest.scale)
    else:
        with torch._C.DisableTorchFunctionSubclass():
            return func(*args, **kwargs)


MyQuantizedLinearWeight.__torch_function__ = custom_torch_function

#################################################################################
# 首先,让我们在 meta 设备上创建一个模型框架,以避免实例化存储。
# 我们将模块中的所有权重转换为 ``MyQuantizedLinearWeight`` 子类,同时保留偏置不变。


def fn(m):
    if isinstance(m, nn.Linear):
        requires_grad = m.weight.requires_grad
        m.weight = torch.nn.Parameter(
            MyQuantizedLinearWeight(m.weight, 0.5), requires_grad=requires_grad
        )


with torch.device("meta"):
    m = nn.Linear(3, 5)
    m.apply(fn)

#################################################################################
# 然后我们可以加载 ``state_dict``。注意我们使用 ``assign=True``，因为对于偏置,
# 我们希望保留 ``state_dict`` 中张量的属性(例如,我们不希望偏置在加载后位于 ``meta`` 设备上)。

torch.__future__.set_swap_module_params_on_conversion(True)
print(f"之前: id(weight)={id(m.weight)}, id(bias)={id(m.bias)}")
print(f"load_state_dict() 之前的 m.state_dict():\n {m.state_dict()}")
state_dict = nn.Linear(3, 5).state_dict()
print(f"state_dict:\n {state_dict}")
m.load_state_dict(state_dict, assign=True)
print(f"之后: id(weight)={id(m.weight)}, id(bias)={id(m.bias)}")
print(f"load_state_dict() 之后的 m.state_dict():\n {m.state_dict()}")

#################################################################################
# 上面是一个如何使用 ``nn.Module.load_state_dict()`` 中的新扩展点的玩具示例。
# 我们还可以想象其他场景,例如当 ``state_dict`` 中有张量子类而模块中有普通 ``nn.Parameters``/张量时,
# 或者两者都是张量子类时。根据使用场景,我们可以定义 ``module_load`` 的 ``__torch_function__`` 处理程序
# 来应用所需的转换。
#
# 结论
# ----------
# 在本教程中,我们学习了 ``swap_tensors``、在 ``nn.Module`` 中保留参数引用的重要性,
# 以及如何使用由 ``torch.__future__.set_swap_module_params_on_conversion`` 控制的两个新扩展点。

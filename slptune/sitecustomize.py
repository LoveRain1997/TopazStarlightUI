# -*- coding: utf-8 -*-
"""SLP 引擎调优注入器(放在 PYTHONPATH 上,由 neuroserver 的 Python 自动导入)。

思路源自 skv89/Topaz-SLP-Launcher:neuroserver 的 SLP 模型是编译 .pyd,
其行为由模块级全局变量控制(PIX_CHUNK_SIZE / VAE tile / VAE_CONV_MAX_MEM ...)。
本钩子在 .pyd 导入完成后、被业务代码使用前,把环境变量 SLPTUNE_* 写进模块。

失败安全:任何异常只打日志,绝不影响原生运行。
仅当模块已定义对应属性时才覆盖(契约校验,防止打错模块)。
"""
import importlib.machinery as _mach
import os as _os
import sys as _sys

_TAG = '[slp-tune] '
_KEYS = {
    'SLPTUNE_PIX_CHUNK': 'PIX_CHUNK_SIZE',
    'SLPTUNE_PIX_OVERLAP': 'PIX_OVERLAP',
    'SLPTUNE_VAE_CONV_MAX_MEM': 'VAE_CONV_MAX_MEM',
    'SLPTUNE_ENC_TILE': 'VAE_ENCODE_TILE_SIZE',
    'SLPTUNE_ENC_OVERLAP': 'VAE_ENCODE_TILE_OVERLAP',
    'SLPTUNE_DEC_TILE': 'VAE_DECODE_TILE_SIZE',
    'SLPTUNE_DEC_OVERLAP': 'VAE_DECODE_TILE_OVERLAP',
    'SLPTUNE_ENC_TILED': 'VAE_ENCODE_TILED',
    'SLPTUNE_DEC_TILED': 'VAE_DECODE_TILED',
}

_any_env = any(k in _os.environ for k in _KEYS)


def _log(msg):
    try:
        print(_TAG + msg, file=_sys.stderr, flush=True)
    except Exception:
        pass


def _maybe_patch(module):
    if not hasattr(module, 'PIX_CHUNK_SIZE') or not hasattr(module, 'VAE_DECODE_TILE_SIZE'):
        return                       # 不是 SLP 模块(契约校验)
    applied = []
    for env, attr in _KEYS.items():
        raw = _os.environ.get(env)
        if raw is None or raw == '':
            continue
        cur = getattr(module, attr, None)
        if isinstance(cur, bool) or attr.endswith('_TILED'):
            val = raw.strip().lower() in ('1', 'true', 'yes')
        elif cur is None or isinstance(cur, (int, float)):
            try:
                val = float(raw) if '.' in raw else int(raw)
            except ValueError:
                _log(f'忽略非法值 {env}={raw}')
                continue
        else:
            val = raw
        setattr(module, attr, val)
        applied.append(f'{attr}={val}')
    if applied:
        _log('applied ' + ', '.join(applied) + f' (module={getattr(module, "__name__", "?")})')


def _install():
    if not _any_env:
        return
    orig = _mach.ExtensionFileLoader.exec_module

    def exec_module(self, module):
        orig(self, module)
        try:
            _maybe_patch(module)
        except Exception as e:
            _log(f'patch failed (ignored): {e}')

    _mach.ExtensionFileLoader.exec_module = exec_module
    _log('hook installed (SLPTUNE_* env detected)')


try:
    _install()
except Exception as e:
    _log(f'install failed (ignored): {e}')

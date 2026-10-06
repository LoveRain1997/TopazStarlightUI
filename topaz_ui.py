# -*- coding: utf-8 -*-
"""
Topaz 星光独立UI —— 不装 ComfyUI、不依赖浏览器,单文件桌面程序
================================================================

直接调用本机已装的 Topaz Video (1.7.1) 引擎:
  超分:  neuroserver.exe --once --filters '[{"model":"slp-26",...}]'
         env TOPAZ_MODEL_STORE=<含 slp26/ 的模型库>
  插帧:  ffmpeg.exe -hwaccel cuvid -filter_complex '...tvai_fi=model=aion-1:...'
         env TVAI_MODEL_DIR=<模型元数据目录>
  收尾:  ffmpeg -c:v copy 合并原视频音轨(aac 192k, +faststart)

UI 为 tkinter(Python 自带),内存占用 ~40MB,无线程阻塞:
任务跑在后台线程,tk 主线程用 root.after 轮询刷新进度/日志。

用法
  python topaz_ui.py            # 源码运行
  python topaz_ui.py --check    # 引擎环境检测(弹窗显示)
  Topaz星光UI.exe               # PyInstaller --onefile --windowed 打包后的成品

本机要点(实测沉淀,勿删):
  - Topaz ffmpeg/ffprobe 是 --disable-decoder=h264/hevc 的定制构建:
    它的 ffprobe -show_streams 对 h264 会段错误 → 探测优先用系统 ffprobe;
    凡用 Topaz ffmpeg 解码的命令(试跑裁剪/插帧)必须带 -hwaccel cuvid。
  - CPU 回退编码用 h264_mf(Topaz ffmpeg 是 LGPL 构建,无 libx264)。
  - 模型库可能在 E:\\ProgramData\\...\\models\\models(GUI 迁移过),按盘符扫。
"""
import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

# PyInstaller --windowed 模式下 stdout/stderr 为 None,必须判空
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# 高DPI感知:必须在任何窗口创建前、且越早越好(建窗后再设会坐标错位)
def _enable_dpi_awareness():
    try:
        from ctypes import windll
        try:
            windll.shcore.SetProcessDpiAwareness(1)      # Win8.1+
        except OSError:
            pass                                          # 已由 manifest 设置过
        except Exception:
            try:
                windll.user32.SetProcessDPIAware()        # Vista+ 回退
            except Exception:
                pass
    except Exception:
        pass

_enable_dpi_awareness()

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if getattr(sys, 'frozen', False):                      # PyInstaller 单文件 exe
    APP_DIR = Path(sys.executable).resolve().parent
    _RUNTIME_DIR = Path(getattr(sys, '_MEIPASS', APP_DIR))   # 单文件解包目录
else:
    APP_DIR = Path(__file__).resolve().parent
    _RUNTIME_DIR = APP_DIR
CONFIG_FILE = APP_DIR / 'config.json'
# sitecustomize 注入包:源码运行在脚本旁,exe 运行在解包目录内(--add-data 内嵌)
SLPTUNE_DIR = _RUNTIME_DIR / 'slptune' if (_RUNTIME_DIR / 'slptune' / 'sitecustomize.py').is_file() \
    else APP_DIR / 'slptune'

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# ===========================================================================
# 引擎定位
# ===========================================================================

def _registry_install_dir():
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, r'Software\Topaz Labs LLC\Topaz Video') as k:
                    val, _ = winreg.QueryValueEx(k, 'InstallDir')
                    if val and Path(val).is_dir():
                        return Path(val)
            except OSError:
                continue
    except Exception:
        pass
    return None


def _cfg_engine():
    try:
        return _load_config().get('engine') or {}
    except Exception:
        return {}


def _valid_install(p):
    return bool(p) and (Path(p) / 'neuroserver' / 'neuroserver.exe').is_file() \
        and (Path(p) / 'ffmpeg.exe').is_file()


def _valid_model_store(p):
    return bool(p) and ((Path(p) / 'slp26').is_dir() or (Path(p) / 'slp25').is_dir())


def find_install():
    """定位 Topaz Video 安装目录:手动配置优先 → 注册表 → 常见路径。"""
    manual = _cfg_engine().get('install_dir')
    if manual and _valid_install(manual):
        return Path(manual).resolve()
    cands = [_registry_install_dir()]
    pf = os.environ.get('ProgramFiles', r'C:\Program Files')
    cands += [
        Path(pf) / 'Topaz Labs LLC' / 'Topaz Video',
        Path(r'E:\Program Files\Topaz Labs LLC\Topaz Video'),
        Path(r'D:\Program Files\Topaz Labs LLC\Topaz Video'),
    ]
    for c in cands:
        if c and _valid_install(c):
            return c.resolve()
    return None


def find_model_store():
    """定位 neuroserver 模型库(含 slp26/ 或 slp25/ 子目录):手动配置优先。"""
    manual = _cfg_engine().get('model_store')
    if manual and _valid_model_store(manual):
        return Path(manual).resolve()
    dirs = []
    if INSTALL:
        dirs.append(INSTALL / 'models' / 'models')
    for drive in ('C:', 'D:', 'E:', 'F:'):
        dirs += [
            Path(drive) / r'ProgramData\Topaz Labs LLC\Topaz Video\models\models',
            Path(drive) / r'ProgramData\Topaz Labs LLC\Topaz Video\models',
        ]
    for d in dirs:
        if (d / 'slp26').is_dir() or (d / 'slp25').is_dir():
            return d.resolve()
    return None


def find_model_meta():
    """tvai 滤镜的模型元数据目录(含 apo-8.json 等)。"""
    for drive in ('C:', 'D:', 'E:', 'F:'):
        d = Path(drive) / r'ProgramData\Topaz Labs LLC\Topaz Video\models'
        if (d / 'apo-8.json').is_file() or (d / 'aion-1.json').is_file():
            return d.resolve()
    return None


def _nvidia_smi_rows():
    try:
        r = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,memory.total,driver_version',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=15,
            creationflags=CREATE_NO_WINDOW)
    except Exception:
        return []
    if r.returncode != 0:
        return []
    rows = []
    for line in r.stdout.splitlines():
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 3 and parts[0].lstrip('-').isdigit():
            try:
                vram_mb = int(re.sub(r'[^\d]', '', parts[2]) or 0)
            except ValueError:
                vram_mb = 0
            rows.append({'driver_idx': int(parts[0]), 'name': parts[1],
                         'vram_mb': vram_mb,
                         'driver_ver': parts[3] if len(parts) > 3 else ''})
    return rows


def refresh_engine():
    """重算引擎全局状态(手动选目录/重置后调用)。"""
    global INSTALL, MODEL_STORE, MODEL_META, GPUS
    INSTALL = find_install()
    MODEL_STORE = find_model_store()
    MODEL_META = find_model_meta()
    GPUS = _nvidia_smi_rows()


INSTALL = None
MODEL_STORE = None
MODEL_META = None
GPUS = []
refresh_engine()

# 编码参数(GPU 与节点包 NS_ENC 一致;Topaz ffmpeg 无 libx264,CPU 用 h264_mf)
NS_ENC_GPU = ('-c:v h264_nvenc -profile:v high -pix_fmt yuv420p -g 30 '
              '-preset p7 -tune hq -rc constqp -qp 18 -rc-lookahead 20 '
              '-spatial_aq 1 -aq-strength 15 -b:v 0 -bf 0')
NS_ENC_CPU = ('-c:v h264_mf -rate_control quality -quality 65 '
              '-pix_fmt yuv420p -g 30 -bf 0')

SLP_MODELS = [('星光 2.6 (推荐)', 'slp-26'), ('Astra', 'astra'),
              ('Astra HQ', 'astrahq'), ('Astra Sharp', 'astrasharp'),
              ('Astra Fast (需Intel核显)', 'astrafast')]
ASTRA_MODELS = ('astra', 'astrahq', 'astrasharp', 'astrafast')
FI_MODELS = [('Aion 补帧 (AI视频推荐)', 'aion-1'), ('Apollo 插帧', 'apo-8'),
             ('Chronos (老版备选)', 'chr-2'), ('Apollo Fast', 'apf-2'),
             ('Chronos Fast', 'chf-3')]

# ---- 引擎调优(思路源自 skv89/Topaz-SLP-Launcher:PYTHONPATH+sitecustomize
#      在 neuroserver 进程内改 SLP 模块全局变量)。本机实测(RTX 5080 16G,144帧):
#      原生(块121/tile640-480) 46.2s;块33+封顶2G 94.6s(慢2x,省显存救急);
#      tile 1024 → 显存打满爬行(16G 卡勿用)。≥14G 显存保持原生即最快。 ----

TUNE_PRESETS = ['自动 (按显存,推荐)', '原生默认 (不注入)', '保守省显存', '自定义']


def tune_settings(preset, custom=None, gpu_sel='auto', vram_setting=96):
    """返回注入 env dict;None=不注入(原生)。
    自动策略: >=14G 原生 / 10~14G 块49+封VAE / 8~10G 块33+缩tile / <8G 最保守。"""
    vram = float(vram_setting) if vram_setting and float(vram_setting) < 90 \
        else gpu_vram_gb(gpu_sel)
    if preset == '自动 (按显存,推荐)':
        if vram >= 14:
            return None
        if vram >= 10:
            return {'PIX_CHUNK': 49, 'VAE_CONV_MAX_MEM': max(2, int(vram - 4))}
        if vram >= 8:
            return {'PIX_CHUNK': 33, 'VAE_CONV_MAX_MEM': max(2, int(vram - 4)),
                    'ENC_TILE': 512, 'ENC_OVERLAP': 64,
                    'DEC_TILE': 384, 'DEC_OVERLAP': 48}
        return {'PIX_CHUNK': 33, 'VAE_CONV_MAX_MEM': 3,
                'ENC_TILE': 384, 'ENC_OVERLAP': 48,
                'DEC_TILE': 288, 'DEC_OVERLAP': 36}
    if preset == '原生默认 (不注入)':
        return None
    if preset == '保守省显存':
        return {'PIX_CHUNK': 33, 'VAE_CONV_MAX_MEM': max(2, int(vram * 0.4)),
                'ENC_TILE': 512, 'ENC_OVERLAP': 64,
                'DEC_TILE': 384, 'DEC_OVERLAP': 48}
    # 自定义
    c = custom or {}
    out = {}
    if c.get('chunk'):
        out['PIX_CHUNK'] = int(c['chunk'])
    if c.get('conv'):
        out['VAE_CONV_MAX_MEM'] = int(c['conv'])
    if c.get('enc_tile'):
        out['ENC_TILE'] = int(c['enc_tile'])
        if c.get('enc_overlap'):
            out['ENC_OVERLAP'] = int(c['enc_overlap'])
    if c.get('dec_tile'):
        out['DEC_TILE'] = int(c['dec_tile'])
        if c.get('dec_overlap'):
            out['DEC_OVERLAP'] = int(c['dec_overlap'])
    return out or None


def _apply_tune_env(env, settings):
    """把 tune_settings 结果写进子进程 env(PYTHONPATH + SLPTUNE_*)。"""
    if not settings or not (SLPTUNE_DIR / 'sitecustomize.py').is_file():
        return False
    env['PYTHONPATH'] = str(SLPTUNE_DIR) + (
        os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    m = {'PIX_CHUNK': 'SLPTUNE_PIX_CHUNK', 'PIX_OVERLAP': 'SLPTUNE_PIX_OVERLAP',
         'VAE_CONV_MAX_MEM': 'SLPTUNE_VAE_CONV_MAX_MEM',
         'ENC_TILE': 'SLPTUNE_ENC_TILE', 'ENC_OVERLAP': 'SLPTUNE_ENC_OVERLAP',
         'DEC_TILE': 'SLPTUNE_DEC_TILE', 'DEC_OVERLAP': 'SLPTUNE_DEC_OVERLAP',
         'ENC_TILED': 'SLPTUNE_ENC_TILED', 'DEC_TILED': 'SLPTUNE_DEC_TILED'}
    for k, v in settings.items():
        env[m[k]] = str(v)
    return True


# ---- 调优可注入性验证(任意机器一键测试 + 首跑自检 + 签名缓存) -----------------

def _engine_sig():
    """引擎指纹:neuroserver.exe + slp25 模块 .pyd 的大小/修改时间哈希。
    Topaz 升级(换引擎/换模型模块)后签名变化 → 缓存失效需重验。"""
    if not INSTALL:
        return ''
    h = hashlib.sha256()
    paths = [INSTALL / 'neuroserver' / 'neuroserver.exe']
    slp = INSTALL / 'neuroserver' / 'models' / 'slp25'
    if slp.is_dir():
        paths += sorted(slp.glob('*.pyd'))
    for p in paths:
        try:
            st = p.stat()
            h.update(f'{p.name.lower()}:{st.st_size}:{int(st.st_mtime)};'.encode())
        except OSError:
            h.update(p.name.lower().encode())
    return h.hexdigest()[:16]


def get_tune_cache():
    """返回 (ok, module, checked_at) 或 None(未验证/签名不符)。"""
    c = _load_config().get('tune_check') or {}
    if c.get('sig') and c['sig'] == _engine_sig():
        return c
    return None


def set_tune_cache(ok, module=''):
    cfg = _load_config()
    cfg['tune_check'] = {'ok': bool(ok), 'module': module or '',
                         'checked_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
                         'sig': _engine_sig()}
    _save_config(cfg)


def verify_tune(log=print):
    """注入实测:生成 6 帧小片→带注入跑一次真 neuroserver→看 [slp-tune] applied。
    返回 (ok, detail)。任意机器可跑(引擎须已被检测到);约 1 分钟(主要是模型加载)。"""
    if not (INSTALL and MODEL_STORE):
        return False, '引擎不完整,无法测试'
    if not (SLPTUNE_DIR / 'sitecustomize.py').is_file():
        return False, '缺少 slptune 注入组件'
    ff, _ = _ff()
    tmp = Path(os.environ.get('TEMP') or '.') / f'_tunechk_{uuid.uuid4().hex[:6]}'
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        clip = tmp / 'in.mp4'
        r = subprocess.run(
            [ff, '-y', '-v', 'error', '-f', 'lavfi',
             '-i', 'testsrc2=size=320x240:rate=12:duration=0.5',
             '-c:v', 'h264_nvenc', '-qp', '18', '-pix_fmt', 'yuv420p', str(clip)],
            capture_output=True, text=True, timeout=120,
            creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0 or not clip.is_file():
            r = subprocess.run(
                [ff, '-y', '-v', 'error', '-f', 'lavfi',
                 '-i', 'testsrc2=size=320x240:rate=12:duration=0.5',
                 '-c:v', 'h264_mf', '-rate_control', 'quality', '-quality', '65',
                 '-pix_fmt', 'yuv420p', str(clip)],
                capture_output=True, text=True, timeout=120,
                creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0 or not clip.is_file():
                return False, '测试片生成失败: ' + (r.stderr or '')[-200:]

        env = _env_common()
        ns_dir = INSTALL / 'neuroserver'
        env['PATH'] = str(INSTALL) + os.pathsep + str(ns_dir) + os.pathsep + env.get('PATH', '')
        env['TOPAZ_MODEL_STORE'] = str(MODEL_STORE)
        env['PYTHONPATH'] = str(SLPTUNE_DIR)
        env['SLPTUNE_PIX_CHUNK'] = '33'          # 探针值,与原生 121 区分
        out = tmp / 'out.mp4'
        cmd = [str(ns_dir / 'neuroserver.exe'), '--once',
               '--input-path', str(clip), '--output-path', str(out),
               '--start-frame-idx', '0', '--end-frame-idx', '6',
               '--max-gpu-mem', '96',
               '--filters', '[{"model": "slp-26", "enhancement_strength": 1.0, "softness": 1}]',
               '--output-width', '320', '--output-height', '240',
               '--upscale-factor', '1.0',
               '--ffmpeg-encoding', NS_ENC_GPU]
        proc = subprocess.Popen(cmd, env=env, cwd=str(ns_dir),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace',
                                creationflags=CREATE_NO_WINDOW)
        applied_line, module = '', ''
        for line in proc.stdout:
            line = line.rstrip()
            if '[slp-tune]' in line:
                log('[测试] ' + line)
                if 'applied' in line:
                    applied_line = line
                    m = re.search(r'module=([^\)]+)', line)
                    module = m.group(1) if m else ''
        proc.wait()
        if applied_line:
            set_tune_cache(True, module)
            return True, f'注入生效 ({module or "SLP模块"})'
        if proc.returncode != 0:
            return False, f'neuroserver 运行失败 (exit {proc.returncode}),见日志'
        set_tune_cache(False)
        return False, '引擎跑通但注入未生效(模块契约不匹配,已按原生跑)'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def engine_status():
    return {
        'install': str(INSTALL) if INSTALL else None,
        'neuroserver': str(INSTALL / 'neuroserver' / 'neuroserver.exe') if INSTALL else None,
        'ffmpeg': str(INSTALL / 'ffmpeg.exe') if INSTALL else None,
        'model_store': str(MODEL_STORE) if MODEL_STORE else None,
        'slp26_ready': bool(MODEL_STORE and (MODEL_STORE / 'slp26').is_dir()),
        'model_meta': str(MODEL_META) if MODEL_META else None,
        'gpus': GPUS,
        'errors': _engine_errors(),
        'ok': bool(INSTALL and MODEL_STORE and MODEL_META),
    }


def _engine_errors():
    errs = []
    if not INSTALL:
        errs.append('未找到 Topaz Video 安装目录(需要 neuroserver\\neuroserver.exe 与 ffmpeg.exe)')
    else:
        if not MODEL_STORE:
            errs.append('未找到模型库(需含 slp26/ 的 models\\models 目录,先在 Topaz GUI 下载过星光模型)')
        if not MODEL_META:
            errs.append('未找到 tvai 模型元数据目录(C:/ProgramData/Topaz Labs LLC/Topaz Video/models)')
    return errs


# ===========================================================================
# 探测 / ffmpeg 基础
# ===========================================================================

def _ff():
    return str(INSTALL / 'ffmpeg.exe'), str(INSTALL / 'ffprobe.exe')


def _probe_candidates():
    """ffprobe 候选 (来源标签, 路径): 系统 ffprobe(完整构建)优先, Topaz ffprobe 兜底。
    Topaz ffprobe 是 --disable-decoder=h264/hevc 的定制构建,实测
    -show_streams 对 h264 会段错误,所以只做最后回退。"""
    cands = []
    sysp = shutil.which('ffprobe')
    if sysp:
        sysp = Path(sysp).resolve()
        if not (INSTALL and str(sysp).lower().startswith(str(INSTALL).lower())):
            cands.append(('系统ffprobe(PATH)', str(sysp)))
    if INSTALL and (INSTALL / 'ffprobe.exe').is_file():
        cands.append(('Topaz ffprobe', str(INSTALL / 'ffprobe.exe')))
    return cands


def _ffmpeg_parse_candidates():
    """可用于 `ffmpeg -i` 解析的候选 (来源标签, 路径): Topaz ffmpeg 优先, 系统兜底。"""
    cands = []
    if INSTALL and (INSTALL / 'ffmpeg.exe').is_file():
        cands.append(('Topaz ffmpeg', str(INSTALL / 'ffmpeg.exe')))
    sysf = shutil.which('ffmpeg')
    if sysf:
        sysf = str(Path(sysf).resolve())
        if not (INSTALL and sysf.lower().startswith(str(INSTALL).lower())):
            cands.append(('系统ffmpeg(PATH)', sysf))
    return cands


_ERR_HINT_RE = re.compile(
    r'Error|error|Invalid|No such|moov|Permission|not found|Corrupt|Unknown format|exists')


def _ffmpeg_err_hint(err):
    """从 ffmpeg/ffprobe stderr 里挑最有信息量的一行(错误关键词优先)。"""
    lines = [l.strip() for l in err.splitlines() if l.strip()]
    hint = next((l for l in lines if _ERR_HINT_RE.search(l)), '')
    if not hint and lines:
        hint = lines[-1]
    return hint[:160]


def _run_capture(cmd, timeout=60, log=None, tag=''):
    """运行子进程并捕获输出,永不返回 None。

    要点: stdin=DEVNULL —— 无控制台的 --windowed exe 里 stdin 句柄无效,
    子进程继承后可能静默失败(stdout/stderr 全空);显式给 DEVNULL 断开继承。
    若捕获结果两个流全空,再用二进制模式重试一次(排除 text 管道层怪问题)。
    返回 (rc, out, err) —— 全是 str。"""
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, errors='replace', timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        rc, out, err = r.returncode, r.stdout or '', r.stderr or ''
    except subprocess.TimeoutExpired:
        raise
    except Exception as e:
        if log:
            log(f'[探测]{tag} 启动失败: {type(e).__name__}: {str(e)[:120]}')
        raise
    if not out and not err:
        try:
            r2 = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, creationflags=CREATE_NO_WINDOW)
            out = (r2.stdout or b'').decode('utf-8', 'replace')
            err = (r2.stderr or b'').decode('utf-8', 'replace')
            rc = r2.returncode
            if log:
                log(f'[探测]{tag} 捕获全空,二进制重试: rc={rc} '
                    f'out={len(out)}B err={len(err)}B')
        except Exception as e:
            if log:
                log(f'[探测]{tag} 二进制重试失败: {type(e).__name__}: {str(e)[:120]}')
    return rc, out, err


def _log_head(log, tag, err, n=3):
    """调试日志: 打印 stderr 前几行(截断)。"""
    if not log:
        return
    lines = [l.strip() for l in err.splitlines() if l.strip()]
    if not lines:
        log(f'[探测]{tag} stderr 为空')
        return
    for l in lines[:n]:
        log(f'[探测]{tag} | {l[:120]}')
    if len(lines) > n:
        log(f'[探测]{tag} …(共{len(lines)}行)')


def _probe_ffmpeg_fallback(path, log=None):
    """ffprobe 全部不可用时,解析 `ffmpeg -i` 的 stderr 拿基本信息。
    尝试所有候选,返回 (info, 使用的候选名);全部失败抛带明细的报错。"""
    attempts = []
    for name, ff in _ffmpeg_parse_candidates():
        if log:
            log(f'[探测]{name} 尝试 ffmpeg -i 解析: {ff}')
        try:
            rc, out, err = _run_capture([ff, '-hide_banner', '-i', str(path)],
                                        timeout=60, log=log, tag=f'[{name}]')
        except Exception as e:
            attempts.append(f'{name}:无法启动({type(e).__name__})')
            continue
        if log:
            log(f'[探测][{name}] rc={rc} out={len(out)}B err={len(err)}B')
            _log_head(log, f'[{name}]', err)
        err = err or ''
        # 注意: Topaz ffmpeg 会打警告行 "...(Video: h264...)", 不能选它 —— 优先取 Stream 行
        vline = next((l for l in err.splitlines()
                      if l.strip().startswith('Stream') and 'Video:' in l), '')
        if not vline:
            vline = next((l for l in err.splitlines() if 'Video:' in l), '')
        m = re.search(r'(\d{2,5})x(\d{2,5})', vline) if vline else None
        if not vline or not m:
            hint = _ffmpeg_err_hint(err)
            attempts.append(f'{name}: 无视频流' + (f' —— {hint}' if hint else ''))
            continue
        dur = 0.0
        md = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', err)
        if md:
            dur = int(md.group(1)) * 3600 + int(md.group(2)) * 60 + float(md.group(3))
        fps = 0.0
        mf = re.search(r'([\d.]+)\s*fps', vline) or re.search(r'([\d.]+)\s*tbr', vline)
        if mf:
            fps = float(mf.group(1))
        frames = int(round(dur * fps)) if fps > 0 and dur > 0 else 0
        return ({'width': int(m.group(1)), 'height': int(m.group(2)),
                 'fps': fps, 'frames': frames, 'duration': round(dur, 2),
                 'has_audio': any(l.strip().startswith('Stream') and 'Audio:' in l
                                  for l in err.splitlines()),
                 'audio_codec': '', 'vcodec': ''},
                name)
    if attempts:
        raise RuntimeError('全部候选失败 → ' + '; '.join(attempts))
    raise RuntimeError('机器上没有任何 ffmpeg(引擎目录未定位到,PATH 里也没有)')


def probe_video(path, log=None):
    """返回 dict: width,height,fps,frames,duration,has_audio,audio_codec,vcodec。
    探测链: 系统 ffprobe → Topaz ffprobe → ffmpeg -i 解析(Topaz/系统)。
    log 可传回调,逐级打印调试日志(命令/返回码/输出大小/stderr 原文)。"""
    info, tried = None, []
    for tag, ffprobe in _probe_candidates():
        tried.append(tag)
        try:
            if log:
                log(f'[探测]{tag} 尝试: {ffprobe}')
            rc, out, err = _run_capture(
                [ffprobe, '-v', 'error', '-show_streams', '-show_format',
                 '-of', 'json', str(path)],
                timeout=60, log=log, tag=f'[{tag}]')
            if log:
                log(f'[探测][{tag}] rc={rc} stdout={len(out)}B stderr={len(err)}B')
                if rc != 0 or not out.strip():
                    _log_head(log, f'[{tag}]', err)
            if rc != 0 or not (out or '').strip():
                hint = _ffmpeg_err_hint(err or '')
                tried[-1] += '(不可用/崩溃' + (f'({hint[:80]})' if hint else '') + ')'
                continue
            info = json.loads(out or '{}')
            if log:
                log(f'[探测]{tag} ✓ 解析成功')
            break
        except Exception as e:
            tried[-1] += f'(异常{type(e).__name__}: {str(e)[:120]})'
            if log:
                log(f'[探测]{tag} 异常: {type(e).__name__}: {str(e)[:160]}')
            continue
    if info is None:
        try:
            info, _used = _probe_ffmpeg_fallback(path, log=log)
            if log:
                log(f'[探测] ffmpeg -i 解析成功(候选: {_used})')
            return info            # -i 解析结果已是最终格式,直接返回
        except RuntimeError as e:
            msg = ('探测失败 —— 已依次尝试: ' + ('、'.join(tried) if tried else '(无 ffprobe 候选)')
                   + '、ffmpeg -i 解析(' + str(e) + ')。'
                   '修复: ① 点「引擎设置…」确认 Topaz 安装目录正确; '
                   '② 或安装 ffmpeg 到 PATH(winget install ffmpeg)后重启本程序')
            if re.search(r'Invalid data|moov|Corrupt|Unknown format', str(e)):
                msg += ('。另: 该文件可能不是视频文件、尚未下载/生成完成(缺 mp4 头),'
                        '或已损坏 —— 换个完整视频试试')
            raise RuntimeError(msg) from None

    vs = [s for s in info.get('streams', []) if s.get('codec_type') == 'video']
    as_ = [s for s in info.get('streams', []) if s.get('codec_type') == 'audio']
    if not vs:
        raise RuntimeError('文件里没有视频流')
    v = vs[0]
    fps = v.get('avg_frame_rate') or v.get('r_frame_rate') or '0/1'
    try:
        num, den = fps.split('/')
        fps_v = float(num) / float(den) if float(den) else 0.0
    except Exception:
        fps_v = 0.0
    dur = 0.0
    try:
        dur = float(v.get('duration') or info.get('format', {}).get('duration') or 0)
    except Exception:
        pass
    frames = 0
    try:
        frames = int(v.get('nb_frames') or 0)
    except Exception:
        pass
    if frames <= 0 and fps_v > 0 and dur > 0:
        frames = int(round(dur * fps_v))
    if frames <= 0:
        for _tag, ffprobe in _probe_candidates():
            try:
                rc2, out2, _e2 = _run_capture(
                    [ffprobe, '-v', 'error', '-count_packets', '-select_streams', 'v:0',
                     '-show_entries', 'stream=nb_read_packets', '-of', 'csv=p=0', str(path)],
                    timeout=600, log=log, tag=f'[{_tag}/count]')
                if rc2 == 0 and (out2 or '').strip():
                    frames = int(out2.strip().split(',')[0])
                    break
            except Exception:
                continue
    return {'width': int(v['width']), 'height': int(v['height']),
            'fps': round(fps_v, 3), 'frames': frames, 'duration': round(dur, 2),
            'has_audio': bool(as_),
            'audio_codec': as_[0].get('codec_name') if as_ else '',
            'vcodec': v.get('codec_name', '')}


def _env_common():
    env = os.environ.copy()
    for k in ('CUDA_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES',
              'ASCEND_RT_VISIBLE_DEVICES'):
        env.pop(k, None)
    return env


# ===========================================================================
# 任务引擎(后台线程跑,tk 主线程轮询)
# ===========================================================================

class Job:
    active = None
    _lock = threading.Lock()

    def __init__(self, params):
        self.id = uuid.uuid4().hex[:8]
        self.params = params
        self.status = 'running'          # running / done / error / cancelled
        self.steps = []                  # {name,status,pct,note}
        self.log = deque(maxlen=3000)
        self.output = None
        self.error = None
        self.info = None
        self.t0 = time.time()
        self.t_end = None
        self.procs = []
        self.cancelled = False

    def logf(self, msg):
        self.log.append(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}')

    def step(self, name):
        self.steps.append({'name': name, 'status': 'running', 'pct': 0, 'note': '',
                           't0': time.time()})
        self.logf(f'▶ {name}')

    def step_eta(self):
        """当前步骤预计剩余秒数(进度≥3%才可估);不可估返回 None。"""
        s = self.cur()
        if not s or s['status'] != 'running' or s['pct'] < 3:
            return None
        return (100 - s['pct']) / s['pct'] * (time.time() - s['t0'])

    def overall_eta(self):
        """整体预计剩余秒数(整体进度≥8%才可估)。"""
        ov = self.overall()
        if ov < 8:
            return None
        e = (self.t_end or time.time()) - self.t0
        return (100 - ov) / ov * e

    def cur(self):
        for s in self.steps:
            if s['status'] == 'running':
                return s
        return self.steps[-1] if self.steps else None

    def set_pct(self, pct, note=''):
        s = self.cur()
        if s:
            s['pct'] = max(s['pct'], min(100, int(pct)))
            if note:
                s['note'] = note

    def finish_step(self, ok=True, note=''):
        s = self.cur()
        if s:
            s['status'] = 'done' if ok else 'failed'
            s['pct'] = 100 if ok else s['pct']
            if note:
                s['note'] = note

    def elapsed(self):
        return round((self.t_end or time.time()) - self.t0, 1)

    def overall(self):
        """整体进度 0-100(按步骤数平均)。"""
        if not self.steps:
            return 0
        tot = sum(s['pct'] if s['status'] == 'running' else 100 for s in self.steps)
        return int(tot / len(self.steps))

    def kill_tree(self):
        self.cancelled = True
        for p in list(self.procs):
            if p.poll() is None:
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)


_FRAME_RE = re.compile(r'frame=\s*(\d+)')
_PROG_RE = re.compile(r'"progress"\s*:\s*(\d+)')
_FFMPEG_LOG_KEYS = ('tvai_', 'License', 'Model', 'Error', 'error', 'failed',
                    'Output #', 'Stream #', 'frame=', 'No such', 'Invalid')


def _consume(proc, job, est_total, tag, keyset='ffmpeg', pbase=0.0, pspan=100.0):
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        if keyset == 'ns':
            job.logf(f'[{tag}] {line}')
            m = _PROG_RE.search(line)
            if m:
                job.set_pct(pbase + int(m.group(1)) * pspan / 100.0)
        else:
            m = _FRAME_RE.search(line)
            if m and est_total > 0:
                job.set_pct(pbase + min(99, int(int(m.group(1)) * 100 / est_total)) * pspan / 100.0)
            if any(k in line for k in _FFMPEG_LOG_KEYS):
                job.logf(f'[{tag}] {line}')
    proc.wait()


def _popen(cmd, env, cwd=None, job=None):
    p = subprocess.Popen(cmd, env=env, cwd=cwd,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding='utf-8', errors='replace',
                         creationflags=CREATE_NO_WINDOW)
    if job:
        job.procs.append(p)
        if job.cancelled:
            job.kill_tree()
    return p


# ---- 星光超分 (neuroserver) ------------------------------------------------

def run_upscale(job, in_path, out_path, p, pbase=0.0, pspan=100.0):
    if not (INSTALL and MODEL_STORE):
        raise RuntimeError('引擎不完整: 未找到 neuroserver 或模型库')
    model = p['model']
    info = job.info
    frames, w, h = info['frames'], info['width'], info['height']
    if model in ASTRA_MODELS and frames < 9:
        raise RuntimeError(f'Astra 系列模型需要至少 9 帧输入(当前 {frames} 帧)')

    scale = float(p['scale'])
    ow = int(round(w * scale)); ow += ow % 2
    oh = int(round(h * scale)); oh += oh % 2

    env = _env_common()
    ns_dir = INSTALL / 'neuroserver'
    env['PATH'] = str(INSTALL) + os.pathsep + str(ns_dir) + os.pathsep + env.get('PATH', '')
    env['TOPAZ_MODEL_STORE'] = str(MODEL_STORE)
    env.pop('TVAI_MODEL_DIR', None)     # 安装版授权走本机默认环境
    env.pop('TOPAZLABS_LICENSE', None)
    device_arg = []
    if p.get('gpu') not in (None, '', 'auto'):
        # PCI_BUS_ID + 只暴露所选卡,不受 CUDA FASTEST_FIRST 枚举顺序影响
        env['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
        env['CUDA_VISIBLE_DEVICES'] = str(int(p['gpu']))
        device_arg = ['--device', '0']

    if model == 'slp-26':
        filters = '[{"model": "%s", "enhancement_strength": %s, "softness": 1}]' % (model, p['strength'])
    else:
        filters = '[{"model": "%s", "enhancement_strength": %s}]' % (model, p['strength'])

    enc = NS_ENC_GPU if p.get('enc', 'gpu') == 'gpu' else NS_ENC_CPU
    cmd = [str(ns_dir / 'neuroserver.exe'), '--once',
           '--input-path', str(in_path),
           '--output-path', str(out_path),
           '--start-frame-idx', '0',
           '--end-frame-idx', str(frames),
           '--max-gpu-mem', str(p['vram']),
           '--filters', filters,
           '--output-width', str(ow),
           '--output-height', str(oh),
           '--upscale-factor', str(scale),
           '--ffmpeg-encoding', enc] + device_arg

    gpu_name = 'Auto' if p.get('gpu') in (None, '', 'auto') else f'驱动号{p["gpu"]}'
    job.logf(f'[超分] 模型 {model} | {w}x{h} → {ow}x{oh} (x{scale}) | '
             f'强度 {p["strength"]} | 显存上限 {p["vram"]}GiB | GPU {gpu_name}')

    tune = p.get('tune')
    if tune:
        applied = _apply_tune_env(env, tune)
        if applied:
            job.logf('[调优] ' + ', '.join(f'{k}={v}' for k, v in tune.items())
                     + ' (sitecustomize 注入)')

    proc = _popen(cmd, env, cwd=str(ns_dir), job=job)
    _consume(proc, job, 0, '超分', keyset='ns', pbase=pbase, pspan=pspan)
    if job.cancelled:
        raise RuntimeError('已取消')
    if proc.returncode != 0:
        raise RuntimeError(f'neuroserver 失败 (exit {proc.returncode}),详见日志')
    if not os.path.isfile(out_path):
        raise RuntimeError('neuroserver 未产生输出文件')
    return frames


# ---- 插帧 (tvai_fi) --------------------------------------------------------

def run_fi(job, in_path, out_path, p, frames_in=None, pbase=0.0, pspan=100.0):
    ff, _ = _ff()
    model = p['model']
    fps_in, fps_out = float(p['fps_in']), float(p['fps_out'])
    slowmo = float(p['slowmo'])
    if fps_out < fps_in:
        raise RuntimeError(f'输出帧率({fps_out})不能小于输入帧率({fps_in})')
    if fps_out == fps_in and slowmo <= 1.0:
        raise RuntimeError('输出帧率需大于输入帧率,或把慢动作倍数设为 >1 做纯慢动作')
    if frames_in is None:
        frames_in = probe_video(in_path)['frames']

    env = _env_common()
    env['PATH'] = str(INSTALL) + os.pathsep + env.get('PATH', '')
    if MODEL_META:
        env['TVAI_MODEL_DIR'] = str(MODEL_META)

    rdt = float(p['rdt_sens']) * 0.2 / 100 if p.get('rdt_on') else 0.0
    device = -2 if p.get('gpu') in (None, '', 'auto') else int(p['gpu'])
    fi = (f'tvai_fi=model={model}:slowmo={slowmo}:rdt={rdt}:fps={int(fps_out)}:'
          f'device={device}:vram={p["vram"]}:instances={p["instances"]}')
    if p.get('scene'):
        fi += ':parameters=scene_change_threshold=1'

    enc_args = (NS_ENC_GPU if p.get('enc', 'gpu') == 'gpu' else NS_ENC_CPU).split()
    cmd = [ff, '-y', '-stats_period', '5', '-loglevel', 'info', '-nostdin',
           '-hwaccel', 'cuvid',
           '-sws_flags', 'spline+accurate_rnd+full_chroma_int',
           '-i', str(in_path),
           '-filter_complex', f'[0:v]hwdownload,format=nv12,format=rgb48le,{fi}[vout]',
           '-map', '[vout]'] + enc_args + [str(out_path)]

    est = int(round(frames_in / fps_in * slowmo * fps_out)) if fps_in else 0
    est = max(est, 1)
    job.logf(f'[插帧] 模型 {model} | {fps_in:g} → {fps_out:g} fps (slowmo={slowmo}) | '
             f'实例 {p["instances"]} | vram {p["vram"]} | rdt={rdt} | '
             f'GPU {"Auto(-2)" if device == -2 else device} | 预计输出 ~{est} 帧')

    def _once():
        proc = _popen(cmd, env, job=job)
        _consume(proc, job, est, '插帧', pbase=pbase, pspan=pspan)
        return proc

    proc = _once()
    # Apollo tile 首次现场编译偶发 0xC0000005 段错误,缓存好后重跑即稳
    if proc.returncode == 3221225477 and not job.cancelled:
        job.logf('[插帧] 首次编译崩溃 (0xC0000005),自动重试一次 ...')
        job.set_pct(0)
        proc = _once()
    if job.cancelled:
        raise RuntimeError('已取消')
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg 失败 (exit {proc.returncode}),详见日志')
    if not os.path.isfile(out_path):
        raise RuntimeError('ffmpeg 未产生输出文件')
    return est


# ---- 收尾:合并音轨 ---------------------------------------------------------

def mux_audio(job, video_only, src_with_audio, out_path, keep_audio=True):
    ff, _ = _ff()
    cmd = [ff, '-y', '-v', 'error', '-i', str(video_only)]
    if keep_audio:
        cmd += ['-i', str(src_with_audio), '-map', '0:v:0', '-map', '1:a:0?',
                '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k']
    else:
        cmd += ['-map', '0:v:0', '-c:v', 'copy']
    cmd += ['-movflags', '+faststart', str(out_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                       creationflags=CREATE_NO_WINDOW)
    if job.cancelled:
        raise RuntimeError('已取消')
    if r.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError('合成失败: ' + (r.stderr or '')[-400:])
    job.logf('[合成] 已合并音轨 → ' + os.path.basename(out_path))


# ---- 试跑裁剪 ---------------------------------------------------------------

def _has_encoder(ff, enc):
    try:
        r = subprocess.run([ff, '-hide_banner', '-encoders'],
                           capture_output=True, text=True, timeout=20,
                           creationflags=CREATE_NO_WINDOW)
        return enc in (r.stdout + r.stderr)
    except Exception:
        return False


def trim_clip(job, in_path, out_path, n_frames):
    """试跑裁剪: Topaz ffmpeg 禁了 h264/hevc 软解,凡用它的方案必须 -hwaccel cuvid。
    依次回退: cuvid+nvenc → 系统 ffmpeg libx264(纯软解) → cuvid+h264_mf。"""
    ff, _ = _ff()
    base = ['-y', '-v', 'error', '-i', str(in_path), '-frames:v', str(n_frames)]
    plans = [(ff, ['-hwaccel', 'cuvid',
                   '-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'constqp', '-qp', '18'])]
    sysff = shutil.which('ffmpeg')
    if sysff:
        sp = str(Path(sysff).resolve()).lower()
        if not (INSTALL and sp.startswith(str(INSTALL).lower())):
            plans.append((sysff, ['-c:v', 'libx264', '-crf', '16', '-preset', 'fast']))
    plans.append((ff, ['-hwaccel', 'cuvid', '-c:v', 'h264_mf',
                       '-rate_control', 'quality', '-quality', '65']))
    plans = [(f, a + ['-pix_fmt', 'yuv420p', '-c:a', 'copy']) for f, a in plans]

    r = None
    for f, enc in plans:
        r = subprocess.run([f] + base + enc + [str(out_path)],
                           capture_output=True, text=True, timeout=1800,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode == 0 and os.path.isfile(out_path):
            job.logf(f'[试跑] 已裁剪前 {n_frames} 帧 → 临时片段')
            return
    raise RuntimeError('试跑裁剪失败: ' + ((r.stderr or '')[-300:] if r else '无输出'))


# ---- 长视频分段 -------------------------------------------------------------

def gpu_vram_gb(gpu_sel='auto'):
    """所选卡的显存 GiB;查询失败按 16 兜底。"""
    idx = None
    if gpu_sel not in (None, '', 'auto'):
        try:
            idx = int(gpu_sel)
        except (TypeError, ValueError):
            idx = None
    for g in GPUS:
        if idx is not None and g['driver_idx'] == idx:
            return max(4.0, g['vram_mb'] / 1024.0)
    if GPUS:
        return max(4.0, GPUS[0]['vram_mb'] / 1024.0)
    return 16.0


def auto_seg_seconds(vram_setting, gpu_sel, ow, oh):
    """按显存智能分段:显存越大、输出越小 → 单段越长。
    显存上限≥90 视为"不设限",用显卡实际显存。
    公式: 秒 = clamp(显存GiB × 6 × (1080p面积/输出面积), 20, 300),5秒取整。"""
    vram = float(vram_setting) if vram_setting and float(vram_setting) < 90 \
        else gpu_vram_gb(gpu_sel)
    factor = min(2.0, max(0.25, (1920 * 1088) / max(1, int(ow) * int(oh))))
    sec = vram * 6 * factor
    return int(min(300, max(20, round(sec / 5) * 5)))


def split_video(job, in_path, out_dir, tag, seg_sec, info):
    """按时长均分并重编码分割(-ss 精确到帧, 保证各段无缝拼接)。
    优先 nvenc, 回退系统 ffmpeg libx264 → h264_mf。返回 [段文件路径]。"""
    ff, _ = _ff()
    dur = float(info.get('duration') or 0)
    if dur <= 0:
        fps = info.get('fps') or 24
        dur = (info.get('frames') or 0) / fps
    if dur <= 0:
        raise RuntimeError('无法得知视频时长,不能分段')
    n = max(1, int(-(-dur // seg_sec)))          # ceil
    actual = dur / n
    base = ['-y', '-v', 'error']
    plans = [(ff, ['-c:v', 'h264_nvenc', '-preset', 'p4', '-rc', 'constqp', '-qp', '18'])]
    sysff = shutil.which('ffmpeg')
    if sysff:
        sp = str(Path(sysff).resolve()).lower()
        if not (INSTALL and sp.startswith(str(INSTALL).lower())):
            plans.append((sysff, ['-c:v', 'libx264', '-crf', '16', '-preset', 'fast']))
    plans.append((ff, ['-c:v', 'h264_mf', '-rate_control', 'quality', '-quality', '65']))
    plans = [(f, a + ['-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k'])
             for f, a in plans]

    files = []
    for i in range(n):
        out = out_dir / f'_seg{i:03d}_{tag}.mp4'
        ss, t = i * actual, min(actual, dur - i * actual)
        ok = False
        for f, a in plans:
            cmd = [f] + base + ['-ss', f'{ss:.3f}', '-t', f'{t:.3f}', '-i', str(in_path)] \
                + a + [str(out)]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600,
                               creationflags=CREATE_NO_WINDOW)
            if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
                ok = True
                break
        if not ok:
            for f2 in files:
                try:
                    f2.unlink()
                except OSError:
                    pass
            raise RuntimeError(f'分割第 {i + 1}/{n} 段失败: ' + (r.stderr or '')[-300:])
        files.append(out)
        job.set_pct(min(99, int((i + 1) * 100 / n)), note=f'{i + 1}/{n} 段')
    job.logf(f'[分割] {dur:.1f}s → {n} 段 × {actual:.1f}s')
    return files, n


def concat_segments(job, files, out_path):
    """同参数段无损拼接(concat demuxer, -c copy)。"""
    ff, _ = _ff()
    list_file = Path(out_path).with_suffix('.txt')
    list_file.write_text(''.join(f"file '{f}'\n" for f in files), encoding='utf-8')
    try:
        r = subprocess.run(
            [ff, '-y', '-v', 'error', '-f', 'concat', '-safe', '0',
             '-i', str(list_file), '-c', 'copy', str(out_path)],
            capture_output=True, text=True, timeout=3600,
            creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError('拼接失败: ' + (r.stderr or '')[-400:])
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass
    job.logf(f'[合并] {len(files)} 段 → ' + os.path.basename(out_path))


# ---- 任务编排 ---------------------------------------------------------------

def unique_path(directory, prefix):
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    p = d / f'{prefix}_{stamp}.mp4'
    n = 2
    while p.exists():
        p = d / f'{prefix}_{stamp}_{n}.mp4'
        n += 1
    return p


def _fmt_dur(sec):
    if sec >= 5400:
        return f'~{sec / 3600:.1f}小时'
    if sec >= 90:
        return f'~{sec / 60:.0f}分钟'
    return f'~{sec:.0f}秒'


def _save_perf(key, val):
    if val <= 0:
        return
    try:
        cfg = _load_config()
        cfg.setdefault('perf', {})[key] = {
            'k': round(val, 6),
            'date': datetime.now().strftime('%m-%d %H:%M'),
            'gpu': GPUS[0]['name'] if GPUS else ''}
        _save_config(cfg)
    except Exception:
        pass


def job_worker(job):
    tmp_files = []
    try:
        p = job.params
        up, fi, out_cfg = p.get('upscale', {}), p.get('fi', {}), p.get('out', {})
        mode = p.get('mode', 'upscale')

        src = Path(p['input'])
        out_dir = Path(out_cfg.get('dir') or src.parent)
        prefix = (out_cfg.get('prefix') or 'Topaz').strip() or 'Topaz'
        if out_cfg.get('date_sub'):
            out_dir = out_dir / datetime.now().strftime('%Y-%m-%d')
        final = unique_path(out_dir, prefix)
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = uuid.uuid4().hex[:8]

        # 试跑裁剪
        work_in = src
        trim_n = int(p.get('trim_frames') or 0)
        if trim_n > 0:
            job.step(f'试跑裁剪 (前 {trim_n} 帧)')
            t = out_dir / f'_trim_{tag}.mp4'
            tmp_files.append(t)
            trim_clip(job, src, t, trim_n)
            job.finish_step()
            work_in = t
        job.info = probe_video(work_in, log=job.logf)
        info = job.info
        job.logf(f'[输入] {info["width"]}x{info["height"]} · {info["fps"]:g}fps · '
                 f'{info["frames"]} 帧 · {info["duration"]}s')

        # 分段规划:仅作用于星光超分;插帧永远整片单趟(流式处理+避免接缝丢帧)
        seg_sec = float(p.get('seg_seconds') or 0)
        if mode == 'fi':
            seg_sec = 0
        if seg_sec == 0 and mode in ('upscale', 'chain'):
            scale = float(up.get('scale') or 2.0)
            ow = int(round(info['width'] * scale)); ow += ow % 2
            oh = int(round(info['height'] * scale)); oh += oh % 2
            seg_sec = auto_seg_seconds(up.get('vram'), up.get('gpu'), ow, oh)
            job.logf(f'[分段] 自动: 显存 {gpu_vram_gb(up.get("gpu")):.0f}GiB, '
                     f'输出 {ow}x{oh} → 每段 ≈{seg_sec}s')
        dur = float(info.get('duration') or 0)
        if dur <= 0 and info.get('fps'):
            dur = info['frames'] / info['fps']
        n_seg = max(1, int(-(-dur // seg_sec))) if (seg_sec > 0 and dur > 0) else 1

        # 跑前预估:按历史实测速度(秒/帧·百万像素)估算各阶段耗时
        perf = _load_config().get('perf') or {}
        est_parts = []
        fi_in_mpx = info['width'] * info['height'] / 1e6
        if mode in ('upscale', 'chain'):
            sc = float(up.get('scale') or 2.0)
            ow_ = int(round(info['width'] * sc)); ow_ += ow_ % 2
            oh_ = int(round(info['height'] * sc)); oh_ += oh_ % 2
            fi_in_mpx = ow_ * oh_ / 1e6          # 链式时插帧的输入=超分输出尺寸
            if perf.get('up_k'):
                est = info['frames'] * fi_in_mpx * perf['up_k']['k']
                est_parts.append(f'超分 {_fmt_dur(est)}')
        if mode in ('fi', 'chain') and perf.get('fi_k'):
            fi_ = fi or {}
            fps_in_ = float(fi_.get('fps_in') or info['fps'] or 24)
            fps_out_ = float(fi_.get('fps_out') or 60)
            slow_ = float(fi_.get('slowmo') or 1.0)
            out_fr = info['frames'] / fps_in_ * slow_ * fps_out_
            est = out_fr * fi_in_mpx * perf['fi_k']['k']
            est_parts.append(f'插帧 {_fmt_dur(est)}')
        if est_parts:
            job.logf('[预估] ' + ' + '.join(est_parts)
                     + '(按历史实测速度;分段会因重复加载模型略增)')

        # 物理分割
        if n_seg > 1:
            job.step(f'分割 ({n_seg} 段 × ~{seg_sec:.0f}s)')
            seg_files, n_seg = split_video(job, work_in, out_dir, tag, seg_sec, info)
            tmp_files += seg_files
            job.finish_step(note=f'{n_seg} 段')
        else:
            seg_files = [work_in]

        # 逐段星光超分
        frames_src = info['frames']
        if mode in ('upscale', 'chain'):
            t_up0 = time.time()
            job.step(f'星光超分 ({up.get("model", "slp-26")})' +
                     (f' · {n_seg}段' if n_seg > 1 else ''))
            outs = []
            span = 100.0 / n_seg
            for i, seg in enumerate(seg_files):
                t = out_dir / f'_ns{i:03d}_{tag}.mp4'
                tmp_files.append(t)
                job.info = probe_video(seg)   # 每段帧数不同,run_upscale 依赖 job.info
                run_upscale(job, seg, t, up, pbase=i * span, pspan=span)
                outs.append(t)
                job.logf(f'[超分] 段 {i + 1}/{n_seg} 完成 '
                         f'({job.info["frames"]} 帧 → {job.info["width"]}x{job.info["height"]})')
            job.finish_step()
            # 记录实测速度(秒/输入帧·输出百万像素),供下次跑前预估
            sc_ = float(up.get('scale') or 2.0)
            ow2 = int(round(info['width'] * sc_)); ow2 += ow2 % 2
            oh2 = int(round(info['height'] * sc_)); oh2 += oh2 % 2
            _save_perf('up_k', (time.time() - t_up0)
                       / max(1, frames_src) / (ow2 * oh2 / 1e6))
            if up.get('tune'):
                # 首跑即验证:本次运行自带 [slp-tune] 日志,无需额外测试
                mod = ''
                ok = False
                for l in job.log:
                    if '[slp-tune] applied' in l:
                        ok = True
                        m = re.search(r'module=([^\)]+)', l)
                        mod = m.group(1) if m else ''
                if ok:
                    set_tune_cache(True, mod)
                    job.logf('[调优] 注入已生效(本次运行自动验证并记忆,引擎升级前不再重测)')
                else:
                    set_tune_cache(False)
                    job.logf('[调优] ⚠ 注入未生效,已按原生参数运行(可点「测试注入」排查)')
            if n_seg == 1:
                cur = probe_video(outs[0])
                job.logf(f'[超分] 完成: {cur["width"]}x{cur["height"]}, '
                         f'{cur["frames"]} 帧 @ {cur["fps"]:g}fps')
                work_in = outs[0]
            else:
                # 超分后立即无损合并——链式时插帧吃整片,消除接缝丢帧;
                # 纯超分模式合并即收尾前的整片
                job.step('合并分段 (超分后无损拼接)')
                merged = out_dir / f'_merged_{tag}.mp4'
                tmp_files.append(merged)
                concat_segments(job, outs, merged)
                job.finish_step()
                work_in = merged
        else:
            work_in = seg_files[0]

        # 插帧:整片单趟(不做分段——流式处理显存恒定,分段反而丢接缝帧)
        if mode in ('fi', 'chain'):
            fi = dict(fi)
            if mode == 'chain' and not fi.get('fps_in'):
                fi['fps_in'] = info['fps']
            t_fi0 = time.time()
            job.step(f'插帧 ({fi.get("model", "aion-1")}) · 整片')
            t = out_dir / f'_fi_{tag}.mp4'
            tmp_files.append(t)
            run_fi(job, work_in, t, fi, frames_in=frames_src)
            job.finish_step()
            # 记录实测速度(秒/输出帧·输入百万像素)
            fps_in_ = float(fi.get('fps_in') or info['fps'] or 24)
            fps_out_ = float(fi.get('fps_out') or 60)
            slow_ = float(fi.get('slowmo') or 1.0)
            out_fr_total = frames_src / fps_in_ * slow_ * fps_out_
            _save_perf('fi_k', (time.time() - t_fi0)
                       / max(1.0, out_fr_total) / max(0.01, fi_in_mpx))
            work_in = t

        # 合成输出 (音轨+faststart)
        job.step('合成输出 (音轨+faststart)')
        mux_audio(job, work_in, src, final, keep_audio=out_cfg.get('keep_audio', True))
        job.finish_step()

        job.output = str(final)
        job.status = 'done'
        job.logf(f'✔ 完成: {final}')
    except RuntimeError as e:
        if job.cancelled:
            job.status = 'cancelled'
            job.logf('■ 已取消')
        else:
            job.status = 'error'
            job.error = str(e)
            job.logf(f'✘ 失败: {e}')
            job.finish_step(ok=False, note=str(e)[:120])
    except Exception as e:
        job.status = 'error'
        job.error = f'{type(e).__name__}: {e}'
        job.logf(f'✘ 异常: {job.error}')
    finally:
        job.t_end = time.time()
        for f in tmp_files:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
        with Job._lock:
            if Job.active is job:
                Job.active = None


def start_job(params):
    with Job._lock:
        if Job.active is not None and Job.active.status == 'running':
            return None, '已有任务在运行'
        job = Job(params)
        Job.active = job
    threading.Thread(target=job_worker, args=(job,), daemon=True).start()
    return job, None


def _validate(params):
    mode = params.get('mode')
    if mode not in ('upscale', 'fi', 'chain'):
        return '无效的模式'
    if not params.get('input') or not os.path.isfile(params['input']):
        return '输入视频不存在'
    up, fi = params.get('upscale', {}), params.get('fi', {})
    if mode in ('upscale', 'chain'):
        if up.get('model') not in [m for _, m in SLP_MODELS]:
            return '无效的超分模型'
        s = float(up.get('scale') or 0)
        if not (1.0 <= s <= 4.0):
            return '放大倍数需在 1.0 ~ 4.0'
    if mode in ('fi', 'chain'):
        fps_in, fps_out = float(fi.get('fps_in') or 0), float(fi.get('fps_out') or 0)
        if fps_in <= 0 or fps_out <= 0:
            return '插帧需要输入/输出帧率'
        if fps_out < fps_in:
            return '输出帧率不能小于输入帧率'
        if fps_out == fps_in and float(fi.get('slowmo') or 1) <= 1.0:
            return '输出帧率需大于输入帧率,或慢动作倍数 >1'
    out = params.get('out', {})
    if out.get('dir') and not os.path.isdir(out['dir']):
        try:
            os.makedirs(out['dir'], exist_ok=True)
        except Exception:
            return '输出目录无法创建'
    seg = int(float(params.get('seg_seconds') or 0))
    if seg and not (10 <= seg <= 3600):
        return '分段秒数需为 0(按显存自动)或 10 ~ 3600'
    tune = params.get('upscale', {}).get('tune')
    if tune:
        if not (SLPTUNE_DIR / 'sitecustomize.py').is_file():
            return '缺少 slptune 注入组件(与主程序同目录)'
        chunk = tune.get('PIX_CHUNK')
        if chunk is not None and not (chunk >= 5 and (chunk - 1) % 4 == 0 and chunk <= 161):
            return '时间块须为 4n+1(如 33/49/121)且 ≤161'
        for k in ('ENC_TILE', 'DEC_TILE'):
            if tune.get(k) and tune[k] < 128:
                return 'VAE tile 不得小于 128'
        if tune.get('ENC_OVERLAP', 0) >= tune.get('ENC_TILE', 1 << 30) or \
           tune.get('DEC_OVERLAP', 0) >= tune.get('DEC_TILE', 1 << 30):
            return 'tile 重叠须小于 tile 本身'
    return None


def _load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_config(cfg):
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1),
                               encoding='utf-8')
    except Exception:
        pass


# ===========================================================================
# tkinter UI(Windows 原生 vista 主题风格:系统控件 + Segoe UI + 浅色窗口)
# ===========================================================================

BG = '#f0f0f0'; BG2 = '#ffffff'; LINE = '#d0d0d0'
TX = '#1b1b1b'; TX2 = '#6b6b6b'
GOLD = '#0067c0'      # Windows 强调色(蓝)
GOLD2 = '#005a9e'
OK = '#107c10'; ERR = '#c42b1c'; WARN = '#9c6b00'
LOG_BG = '#1e1e1e'; LOG_FG = '#d4d4d4'
FONT = 'Segoe UI'


class App:
    def __init__(self, root):
        self.root = root
        self.job = None            # 当前展示的任务
        self.shown_log_n = 0
        self.info = None           # 最近一次探测结果
        self._probe_after = None
        root.title('Topaz 星光独立UI — 无需 ComfyUI')
        try:
            sw = root.winfo_fpixels('1i') / 96.0   # DPI 等比(150% 缩放 ≈ 1.5)
        except Exception:
            sw = 1.0
        root.minsize(int(700 * sw), int(600 * sw))

        self._style()
        self.vars = self._make_vars()
        self._build()
        self._load_last()
        self._poll()
        root.protocol('WM_DELETE_WINDOW', self._on_close)

    # ---- 样式:Windows 原生 vista 主题(系统控件/焦点/悬停/DPI 渲染全走系统) ----
    def _style(self):
        s = ttk.Style(self.root)
        for theme in ('vista', 'winnative', 'clam'):
            try:
                s.theme_use(theme)
                break
            except tk.TclError:
                continue
        s.configure('.', font=(FONT, 9))
        s.configure('Dim.TLabel', foreground=TX2, font=(FONT, 8))
        s.configure('Head.TLabel', foreground=GOLD, font=(FONT, 9, 'bold'))
        s.configure('TLabelframe.Label', foreground='#2b2b2b',
                    font=(FONT, 9, 'bold'))
        s.configure('Run.TButton', font=(FONT, 11, 'bold'), padding=(16, 6))
        s.configure('Accent.TButton', font=(FONT, 9))

    # ---- 变量 ----
    def _make_vars(self):
        v = {}
        v['inpath'] = tk.StringVar()
        v['mode'] = tk.StringVar(value='upscale')
        v['u_model'] = tk.StringVar(value='slp-26')
        v['u_scale'] = tk.StringVar(value='2.0')
        v['u_strength'] = tk.DoubleVar(value=1.0)
        v['u_strength_v'] = tk.StringVar(value='1.0')
        v['u_vram'] = tk.StringVar(value='96')
        v['u_fps'] = tk.StringVar(value='24')
        v['f_model'] = tk.StringVar(value='aion-1')
        v['f_fps_in'] = tk.StringVar(value='24')
        v['f_fps_out'] = tk.StringVar(value='60')
        v['f_slowmo'] = tk.StringVar(value='1.0')
        v['f_inst'] = tk.StringVar(value='2')
        v['f_vram'] = tk.StringVar(value='0.85')
        v['f_rdt'] = tk.BooleanVar(value=True)
        v['f_sens'] = tk.StringVar(value='5')
        v['f_scene'] = tk.BooleanVar(value=False)
        v['gpu'] = tk.StringVar(value='auto')
        v['enc'] = tk.StringVar(value='GPU (h264_nvenc 快)')
        v['trim'] = tk.StringVar(value='0')
        v['o_dir'] = tk.StringVar()
        v['o_prefix'] = tk.StringVar(value='Topaz')
        v['o_date'] = tk.BooleanVar(value=True)
        v['o_audio'] = tk.BooleanVar(value=True)
        v['seg'] = tk.StringVar(value='0')
        v['tune'] = tk.StringVar(value=TUNE_PRESETS[0])
        v['tune_chunk'] = tk.StringVar(value='33')
        v['tune_conv'] = tk.StringVar(value='4')
        v['tune_enc'] = tk.StringVar(value='512')
        v['tune_dec'] = tk.StringVar(value='384')
        v['outsize'] = tk.StringVar(value='')
        v['status'] = tk.StringVar(value='')
        return v

    # ---- 布局 ----
    def _row(self, parent, *children, pady=2, fill_x=True):
        f = ttk.Frame(parent)
        f.pack(fill='x', pady=pady)
        f.lower()   # 子控件先于 f 创建(作为实参),Tk 按创建序堆叠,必须把 f 压到子控件之下
        for c in children:
            if isinstance(c, tuple):
                w, opt = c
            else:
                w, opt = c, {}
            if w is not None:
                w.pack(in_=f, side='left', **opt)
        return f

    def _lab(self, parent, text, dim=False):
        return ttk.Label(parent, text=text, style='Dim.TLabel' if dim else 'TLabel',
                         width=9 if not dim else 0, anchor='e' if not dim else 'w')

    def _build(self):
        pad = {'padx': 10, 'pady': 6}
        main = ttk.Frame(self.root)
        main.pack(fill='both', expand=True, **pad)

        # ── 标题 + 引擎状态 ──
        head = self._row(main, pady=(0, 4),
                         fill_x=True)
        ttk.Label(head, text='Topaz 星光独立UI', font=(FONT, 13, 'bold'),
                  foreground=GOLD).pack(side='left')
        ttk.Label(head, text='  直接调用本机 Topaz Video 引擎 · 单文件 · 无浏览器',
                  style='Dim.TLabel').pack(side='left')
        self.eng_btn = ttk.Button(head, text='引擎设置…', width=11,
                                  command=self._engine_dialog)
        self.eng_btn.pack(side='right', padx=(8, 0))
        self.eng_lbl = ttk.Label(head, text='', font=(FONT, 8))
        self.eng_lbl.pack(side='right')
        self._refresh_engine_ui()

        # ── ① 输入 ──
        lf1 = ttk.Labelframe(main, text=' ① 输入视频 ')
        lf1.pack(fill='x', pady=4)
        self._row(lf1,
                  (ttk.Entry(lf1, textvariable=self.vars['inpath'], width=46),
                   {'fill': 'x', 'expand': True, 'padx': (0, 6)}),
                  (ttk.Button(lf1, text='浏览…', width=8, command=self._browse), {}),
                  pady=4)
        self.info_lbl = ttk.Label(lf1, text='改完路径自动探测分辨率/帧率/帧数/音轨', style='Dim.TLabel',
                                  wraplength=780, justify='left')
        self.info_lbl.pack(fill='x', pady=(0, 5))

        # ── ② 模式 ──
        lf2 = ttk.Labelframe(main, text=' ② 模式 ')
        lf2.pack(fill='x', pady=4)
        mrow = self._row(lf2, pady=4)
        for text, val in (('星光超分', 'upscale'), ('插帧', 'fi'), ('超分 + 插帧', 'chain')):
            ttk.Radiobutton(mrow, text=text, value=val, variable=self.vars['mode'],
                            command=self._on_mode, width=11).pack(side='left', padx=(0, 8))

        # -- 超分参数 --
        self.p_up = ttk.Labelframe(main, text=' 超分参数 ')
        self.p_up.pack(fill='x', pady=4)
        um = self._row(self.p_up,
                       (self._lab(self.p_up, '模型'), {'padx': (0, 6)}),
                       (ttk.Combobox(self.p_up, textvariable=self.vars['u_model'], width=24,
                                     state='readonly',
                                     values=[d for d, _ in SLP_MODELS]), {}),
                       (ttk.Label(self.p_up, text='放大倍数'), {'padx': (16, 6)}),
                       (ttk.Spinbox(self.p_up, textvariable=self.vars['u_scale'],
                                    from_=1.0, to=4.0, increment=0.01, width=7,
                                    command=self._upd_size), {}),
                       (ttk.Label(self.p_up, textvariable=self.vars['outsize'],
                                  foreground=GOLD), {'padx': (8, 0)}),
                       pady=4)
        um.pack(fill='x')
        qrow = self._row(self.p_up, pady=1)
        ttk.Label(qrow, text='目标:', style='Dim.TLabel').pack(side='left')
        for txt, tw in (('1080p', 1920), ('2K', 2560), ('4K', 3840)):
            ttk.Button(qrow, text='→' + txt, width=7,
                       command=lambda t=tw: self._set_target(t)).pack(side='left', padx=2)
        ttk.Label(qrow, text='  增强强度(0.7柔和/1.0默认/1.3最猛):', style='Dim.TLabel').pack(side='left', padx=(10, 2))
        sc = ttk.Scale(qrow, from_=0.5, to=1.5, variable=self.vars['u_strength'],
                       command=self._on_strength, length=130)
        sc.pack(side='left')
        ttk.Label(qrow, textvariable=self.vars['u_strength_v'], foreground=GOLD,
                  width=4).pack(side='left')
        self._row(self.p_up,
                  (self._lab(self.p_up, '显存上限'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.p_up, textvariable=self.vars['u_vram'],
                               from_=4.0, to=96.0, increment=0.1, width=7), {}),
                  (ttk.Label(self.p_up, text='GiB(96=不设限;16G卡建议14)', style='Dim.TLabel'),
                   {'padx': (6, 20)}),
                  (ttk.Label(self.p_up, text='输出帧率'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.p_up, textvariable=self.vars['u_fps'],
                               from_=1, to=120, increment=1, width=6), {}),
                  (ttk.Label(self.p_up, text='与视频实际帧率一致', style='Dim.TLabel'),
                   {'padx': (6, 0)}),
                  pady=4)

        # -- 插帧参数 --
        self.p_fi = ttk.Labelframe(main, text=' 插帧参数 ')
        frow = self._row(self.p_fi,
                         (self._lab(self.p_fi, '模型'), {'padx': (0, 6)}),
                         (ttk.Combobox(self.p_fi, textvariable=self.vars['f_model'], width=22,
                                       state='readonly',
                                       values=[d for d, _ in FI_MODELS]), {}),
                         (ttk.Label(self.p_fi, text='帧率'), {'padx': (16, 6)}),
                         (ttk.Spinbox(self.p_fi, textvariable=self.vars['f_fps_in'],
                                      from_=1, to=240, increment=0.001, width=7), {}),
                         (ttk.Label(self.p_fi, text='→'), {'padx': 4}),
                         (ttk.Spinbox(self.p_fi, textvariable=self.vars['f_fps_out'],
                                      from_=2, to=240, increment=1, width=7), {}),
                         pady=4)
        frow.pack(fill='x')
        self._row(self.p_fi,
                  (self._lab(self.p_fi, '慢动作'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.p_fi, textvariable=self.vars['f_slowmo'],
                               from_=1.0, to=8.0, increment=0.1, width=7), {}),
                  (ttk.Label(self.p_fi, text='倍(2.0=时长翻倍)', style='Dim.TLabel'), {'padx': (6, 20)}),
                  (ttk.Label(self.p_fi, text='并行实例'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.p_fi, textvariable=self.vars['f_inst'],
                               from_=0, to=3, increment=1, width=5), {}),
                  (ttk.Label(self.p_fi, text='(2 比 1 快约31%)', style='Dim.TLabel'), {'padx': (6, 20)}),
                  (ttk.Label(self.p_fi, text='显存占用'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.p_fi, textvariable=self.vars['f_vram'],
                               from_=0.1, to=1.0, increment=0.05, width=5), {}),
                  pady=1)
        self._row(self.p_fi,
                  (ttk.Checkbutton(self.p_fi, text='重复帧检测',
                                   variable=self.vars['f_rdt']), {}),
                  (ttk.Label(self.p_fi, text='敏感性1-100'), {'padx': (8, 4)}),
                  (ttk.Spinbox(self.p_fi, textvariable=self.vars['f_sens'],
                               from_=1, to=100, increment=1, width=6), {}),
                  (ttk.Checkbutton(self.p_fi, text='场景检测(多镜头素材建议开)',
                                   variable=self.vars['f_scene']), {'padx': (16, 0)}),
                  pady=4)

        # -- 公共 --
        lf3 = ttk.Labelframe(main, text=' GPU / 编码 / 试跑 ')
        lf3.pack(fill='x', pady=4)
        self.gpu_cb = ttk.Combobox(lf3, textvariable=self.vars['gpu'], state='readonly', width=30)
        self._row(lf3,
                  (self._lab(lf3, 'GPU'), {'padx': (0, 6)}),
                  (self.gpu_cb, {}),
                  (ttk.Label(lf3, text='输出编码'), {'padx': (20, 6)}),
                  (ttk.Combobox(lf3, textvariable=self.vars['enc'], state='readonly', width=18,
                                values=['GPU (h264_nvenc 快)', 'CPU (h264_mf 兼容)']), {}),
                  (ttk.Label(lf3, text='试跑帧数(0=全部;先填10试通)'), {'padx': (20, 6)}),
                  (ttk.Spinbox(lf3, textvariable=self.vars['trim'],
                               from_=0, to=100000, increment=1, width=7), {}),
                  pady=4)
        self.gpu_cb['values'] = ['Auto (自动)'] + [f'GPU {i}: {n}' for i, n in
                                                   ((g['driver_idx'], g['name']) for g in GPUS)]
        self.gpu_cb.current(0)
        self.seg_spin = ttk.Spinbox(lf3, textvariable=self.vars['seg'],
                                    from_=0, to=3600, increment=10, width=7)
        self.seg_hint = ttk.Label(lf3, text='0=按显存自动', style='Dim.TLabel')
        self._row(lf3,
                  (self._lab(lf3, '分段秒数'), {'padx': (0, 6)}),
                  (self.seg_spin, {}),
                  (self.seg_hint, {'padx': (10, 0)}),
                  (ttk.Label(lf3, text='长视频自动切分→逐段处理→无损拼接,防显存溢出、进度更细',
                             style='Dim.TLabel'), {'padx': (12, 0)}),
                  pady=1)

        # ── 引擎调优(星光超分) ──
        lf6 = ttk.Labelframe(main, text=' 引擎调优 (星光超分) ')
        lf6.pack(fill='x', pady=4)
        self.tune_cb = ttk.Combobox(lf6, textvariable=self.vars['tune'], state='readonly',
                                    width=20, values=TUNE_PRESETS)
        self.tune_hint = ttk.Label(lf6, text='', style='Dim.TLabel')
        self.tune_test_btn = ttk.Button(lf6, text='测试注入', width=10,
                                        command=self._test_tune)
        self._row(lf6,
                  (self._lab(lf6, '预设'), {'padx': (0, 6)}),
                  (self.tune_cb, {}),
                  (self.tune_test_btn, {'padx': (10, 0)}),
                  (self.tune_hint, {'padx': (10, 0)}),
                  pady=4)
        self.tune_custom = ttk.Frame(lf6)
        self._row(self.tune_custom,
                  (self._lab(self.tune_custom, '时间块'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.tune_custom, textvariable=self.vars['tune_chunk'],
                               from_=5, to=161, increment=4, width=6), {}),
                  (ttk.Label(self.tune_custom, text='帧(4n+1)'), {'padx': (2, 16)}),
                  (ttk.Label(self.tune_custom, text='VAE上限'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.tune_custom, textvariable=self.vars['tune_conv'],
                               from_=1, to=64, increment=1, width=5), {}),
                  (ttk.Label(self.tune_custom, text='GB'), {'padx': (2, 16)}),
                  (ttk.Label(self.tune_custom, text='编码tile'), {'padx': (0, 6)}),
                  (ttk.Spinbox(self.tune_custom, textvariable=self.vars['tune_enc'],
                               from_=128, to=2048, increment=32, width=6), {}),
                  (ttk.Label(self.tune_custom, text='解码tile'), {'padx': (16, 6)}),
                  (ttk.Spinbox(self.tune_custom, textvariable=self.vars['tune_dec'],
                               from_=128, to=2048, increment=32, width=6), {}),
                  pady=1)
        self.tune_cb.bind('<<ComboboxSelected>>', lambda *_: self._upd_tune())

        # ── ③ 输出 ──
        lf4 = ttk.Labelframe(main, text=' ③ 输出 ')
        lf4.pack(fill='x', pady=4)
        self._row(lf4,
                  (self._lab(lf4, '目录'), {'padx': (0, 6)}),
                  (ttk.Entry(lf4, textvariable=self.vars['o_dir'], width=40),
                   {'fill': 'x', 'expand': True, 'padx': (0, 6)}),
                  (ttk.Button(lf4, text='…', width=4, command=self._browse_dir), {}),
                  (ttk.Label(lf4, text='前缀'), {'padx': (16, 6)}),
                  (ttk.Entry(lf4, textvariable=self.vars['o_prefix'], width=12), {}),
                  pady=4)
        self._row(lf4,
                  (ttk.Checkbutton(lf4, text='日期子文件夹', variable=self.vars['o_date']), {}),
                  (ttk.Checkbutton(lf4, text='保留原音频', variable=self.vars['o_audio']),
                   {'padx': (20, 0)}),
                  (ttk.Label(lf4, text='命名: 前缀_日期时间.mp4,不覆盖旧文件', style='Dim.TLabel'),
                   {'padx': (20, 0)}),
                  pady=2)

        # ── 开始/取消 + 进度 ──
        act = ttk.Frame(main)
        act.pack(fill='x', pady=(8, 2))
        self.btn_run = ttk.Button(act, text='开 始 处 理', style='Run.TButton',
                                  command=self._run)
        self.btn_run.pack(side='left')
        self.btn_cancel = ttk.Button(act, text='取消任务', command=self._cancel,
                                     state='disabled')
        self.btn_cancel.pack(side='left', padx=8)
        self.status_lbl = ttk.Label(act, textvariable=self.vars['status'],
                                    foreground=TX2, font=(FONT, 9))
        self.status_lbl.pack(side='left', padx=12)

        self.bar = ttk.Progressbar(main, maximum=100)
        self.bar.pack(fill='x', pady=(4, 2))
        self.step_lbl = ttk.Label(main, text='就绪', style='Dim.TLabel')
        self.step_lbl.pack(fill='x')

        # ── 日志 ──
        lf5 = ttk.Labelframe(main, text=' 引擎日志 ')
        lf5.pack(fill='both', expand=True, pady=4)
        self.log_txt = tk.Text(lf5, height=11, width=70, bg=LOG_BG, fg=LOG_FG, bd=0,
                               font=('Consolas', 9), state='disabled',
                               insertbackground=TX, wrap='none')
        ys = ttk.Scrollbar(lf5, command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=ys.set)
        self.log_txt.pack(side='left', fill='both', expand=True, padx=(4, 0), pady=4)
        ys.pack(side='right', fill='y', pady=4)
        for tag, color in (('err', '#ff6b6b'), ('ok', '#57d98b'), ('tag', '#5ad1e6')):
            self.log_txt.tag_configure(tag, foreground=color)

        # ── 上次成品 ──
        last = ttk.Frame(main)
        last.pack(fill='x', pady=(0, 2))
        ttk.Label(last, text='上次成品:', style='Dim.TLabel').pack(side='left')
        self.last_lbl = ttk.Label(last, text='—', foreground=GOLD)
        self.last_lbl.pack(side='left', padx=6)
        ttk.Button(last, text='打开位置', width=9,
                   command=self._open_last).pack(side='right')

        self._on_mode()
        self.vars['u_scale'].trace_add('write', lambda *_: self._upd_size())
        self.vars['u_vram'].trace_add('write', lambda *_: (self._upd_seg(), self._upd_tune()))
        self.vars['seg'].trace_add('write', lambda *_: self._upd_seg())
        self.gpu_cb.bind('<<ComboboxSelected>>',
                         lambda *_: (self._upd_seg(), self._upd_tune()))
        self._upd_tune()
        # 按内容实际尺寸开窗,夹在屏幕范围内(任何 DPI 下不裁切、不超屏)
        self.root.update_idletasks()
        rw, rh = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        gw = min(rw + 16, self.root.winfo_screenwidth() - 16)
        gh = min(rh + 16, self.root.winfo_screenheight() - 60)
        self.root.geometry(f'{int(gw)}x{int(gh)}+60+40')

    # ---- 配置记忆 ----
    def _load_last(self):
        cfg = _load_config().get('last', {})
        if not cfg:
            return
        if cfg.get('input'):
            self.vars['inpath'].set(cfg['input'])
            self._probe_async()
        if cfg.get('out', {}).get('dir'):
            self.vars['o_dir'].set(cfg['out']['dir'])
        if cfg.get('out', {}).get('prefix'):
            self.vars['o_prefix'].set(cfg['out']['prefix'])
        if cfg.get('mode'):
            self.vars['mode'].set(cfg['mode'])
            self._on_mode()
        if cfg.get('upscale'):
            u = cfg['upscale']
            if u.get('scale'):
                self.vars['u_scale'].set(str(u['scale']))
            if u.get('fps'):
                self.vars['u_fps'].set(str(u['fps']))
        if cfg.get('seg_seconds') is not None:
            self.vars['seg'].set(str(cfg.get('seg_seconds') or 0))
        tu = cfg.get('tune_ui')
        if tu:
            if tu.get('preset') in TUNE_PRESETS:
                self.vars['tune'].set(tu['preset'])
            for k in ('chunk', 'conv', 'enc', 'dec'):
                if tu.get(k):
                    self.vars['tune_' + k].set(str(tu[k]))

    # ---- 交互 ----
    def _browse(self):
        p = filedialog.askopenfilename(
            title='选择输入视频',
            filetypes=[('视频文件', '*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts *.flv'),
                       ('所有文件', '*.*')])
        if p:
            self.vars['inpath'].set(p)
            self._probe_async()

    def _browse_dir(self):
        start = self.vars['o_dir'].get() or os.path.dirname(self.vars['inpath'].get()) or None
        p = filedialog.askdirectory(title='选择输出目录', initialdir=start)
        if p:
            self.vars['o_dir'].set(p)

    def _probe_async(self):
        path = self.vars['inpath'].get().strip()
        if not path or not os.path.isfile(path):
            self.info = None
            self.info_lbl.config(text=f'✘ 文件不存在: {path[:120]}' if path
                                 else '✘ 请先填写视频路径', foreground=ERR)
            return
        self.info_lbl.config(text='探测中…', foreground=TX2)
        def work():
            dbg = []
            try:
                info = probe_video(path, log=dbg.append)
            except Exception as e:
                info = {'error': str(e)}
            def done():
                # 探测调试链路进控制台(成功也显示一行,失败显示全过程)
                if dbg:
                    self.log_txt.config(state='normal')
                    for l in dbg:
                        tag = 'err' if ('✘' in l or '失败' in l or '异常' in l) else 'tag'
                        self.log_txt.insert('end', l + '\n', tag or ())
                    self.log_txt.see('end')
                    self.log_txt.config(state='disabled')
                if 'error' in info:
                    self.info = None
                    self.info_lbl.config(text='✘ ' + info['error'], foreground=ERR)
                    return
                self.info = info
                self.info_lbl.config(foreground='#1b1b1b', text=(
                    f'{info["width"]}×{info["height"]} · {info["fps"]:g} fps · '
                    f'{info["frames"]} 帧 · {info["duration"]}s · {info["vcodec"]}'
                    + (f' · 有音轨({info["audio_codec"]})' if info['has_audio'] else ' · 无音轨')))
                if info['fps'] > 0:
                    self.vars['u_fps'].set(str(int(round(info['fps']))))
                    self.vars['f_fps_in'].set(str(info['fps']))
                self._upd_size()
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _on_mode(self):
        m = self.vars['mode'].get()
        if m == 'fi':
            self.p_fi.pack(fill='x', pady=4);  self.p_up.pack_forget()
            if hasattr(self, 'tune_cb'):
                self.tune_cb.state(['disabled'])
                self.tune_hint.config(text='仅作用于星光超分', foreground=TX2)
        else:
            self.p_up.pack(fill='x', pady=4)
            if m == 'chain':
                self.p_fi.pack(fill='x', pady=4)
            else:
                self.p_fi.pack_forget()
            if hasattr(self, 'tune_cb'):
                self.tune_cb.state(['!disabled'])
                self._upd_tune()
        self._upd_size()
        self._upd_seg()

    def _on_strength(self, val):
        v = round(float(val), 1)
        self.vars['u_strength_v'].set(f'{v:.1f}')

    def _set_target(self, target_w):
        if not self.info or not self.info['width']:
            return
        s = round(target_w / self.info['width'], 2)
        self.vars['u_scale'].set(f'{s:.2f}')
        self._upd_size()

    def _upd_size(self, *_):
        if not self.info:
            self.vars['outsize'].set('')
            return
        try:
            s = float(self.vars['u_scale'].get())
        except ValueError:
            return
        w = round(self.info['width'] * s / 2) * 2
        h = round(self.info['height'] * s / 2) * 2
        self.vars['outsize'].set(f'→ {w}×{h}')
        self._upd_seg()

    def _gpu_idx(self):
        gsel = self.gpu_cb.get()
        if gsel.startswith('GPU '):
            try:
                return int(gsel.split(':')[0].replace('GPU ', '').strip())
            except ValueError:
                return 'auto'
        return 'auto'

    def _upd_seg(self, *_):
        """分段秒数输入框旁的动态提示。"""
        if not hasattr(self, 'seg_hint'):
            return
        if self.vars['mode'].get() == 'fi':
            # 插帧永远整片单趟:流式处理显存恒定,分段无益还丢接缝帧
            self.seg_spin.state(['disabled'])
            self.seg_hint.config(text='插帧为流式处理,不分段', foreground=TX2)
            self._upd_tune()
            return
        self.seg_spin.state(['!disabled'])
        chain = self.vars['mode'].get() == 'chain'
        prefix = '分段只切超分,合并后再整片插帧; ' if chain else ''
        try:
            v = float(self.vars['seg'].get() or 0)
        except ValueError:
            v = 0
        if v > 0:
            if self.info and self.info.get('duration'):
                n = max(1, int(-(-self.info['duration'] // v)))
                self.seg_hint.config(text=prefix + f'手动 {v:g}s/段 · 共 {n} 段', foreground=GOLD)
            else:
                self.seg_hint.config(text=prefix + f'手动 {v:g}s/段', foreground=GOLD)
            return
        if not self.info or not self.vars['u_scale'].get():
            self.seg_hint.config(text='0=按显存自动', foreground=TX2)
            return
        try:
            s = float(self.vars['u_scale'].get())
            w = round(self.info['width'] * s / 2) * 2
            h = round(self.info['height'] * s / 2) * 2
            sec = auto_seg_seconds(float(self.vars['u_vram'].get() or 96),
                                   self._gpu_idx(), w, h)
            msg = prefix + f'0=自动 ≈{sec}s/段'
            if self.info.get('duration'):
                msg += f' · 全程约 {max(1, int(-(-self.info["duration"] // sec)))} 段'
            self.seg_hint.config(text=msg, foreground=GOLD)
        except Exception:
            self.seg_hint.config(text='0=按显存自动', foreground=TX2)

    def _upd_tune(self, *_):
        """调优预设的动态提示 + 自定义行显隐 + 验证状态。"""
        if not hasattr(self, 'tune_hint'):
            return
        cache = get_tune_cache()
        if cache and cache.get('ok'):
            vtag = f"✓已验证({cache.get('checked_at', '')}) · "
        elif cache is not None:
            vtag = '✗验证失败 · '
        else:
            vtag = ''
        preset = self.vars['tune'].get()
        vram = gpu_vram_gb(self._gpu_idx())
        if preset == TUNE_PRESETS[2]:                    # 保守省显存
            self.tune_custom.pack(fill='x', pady=(0, 4))
            self.tune_hint.config(
                text=vtag + f'块33+VAE上限{max(2, int(vram * 0.4))}G+tile 512/384 · 低显存救急(慢~2x)',
                foreground=GOLD)
            return
        if preset == TUNE_PRESETS[3]:                    # 自定义
            self.tune_custom.pack(fill='x', pady=(0, 4))
            self.tune_hint.config(text=vtag + '块窗须为 4n+1(33/49/121);tile 过大会显存溢出变慢',
                                  foreground=TX2)
            return
        self.tune_custom.pack_forget()
        if preset == TUNE_PRESETS[1]:                    # 原生
            self.tune_hint.config(text=vtag + '不注入 · 引擎出厂参数(块121/tile640-480)', foreground=TX2)
            return
        # 自动
        if vram >= 14:
            txt = f'显存 {vram:.0f}G ≥ 14G → 保持原生(实测最快,不注入)'
            col = OK
        elif vram >= 10:
            txt = f'显存 {vram:.0f}G → 块49 + VAE上限 {max(2, int(vram - 4))}G'
            col = GOLD
        elif vram >= 8:
            txt = f'显存 {vram:.0f}G → 块33 + VAE上限 {max(2, int(vram - 4))}G + tile 512/384'
            col = GOLD
        else:
            txt = f'显存 {vram:.0f}G → 最保守(块33/上限3G/tile 384/288)'
            col = GOLD
        if vram >= 14:
            self.tune_hint.config(text=vtag + txt, foreground=col)
        else:
            # 需要注入的档位才强调验证状态
            self.tune_hint.config(
                text=(vtag if vtag else '未验证(首跑自动验/点「测试注入」) · ') + txt, foreground=col)

    def _test_tune(self):
        """一键注入实测:后台跑 6 帧真任务,完成后弹窗+更新提示(任意机器可用)。"""
        self.tune_test_btn.state(['disabled'])
        self.vars['status'].set('注入测试中(约1分钟,加载模型)…')
        lines = []

        def log(msg):
            lines.append(msg)

        def work():
            ok, detail = verify_tune(log)

            def done():
                self.tune_test_btn.state(['!disabled'])
                self.vars['status'].set(f'注入测试: {"✓ " + detail if ok else "✘ " + detail}')
                self._upd_tune()
                for l in lines[-8:]:
                    self.log_txt.config(state='normal')
                    self.log_txt.insert('end', l + '\n', 'tag' if '[slp-tune]' in l else ())
                    self.log_txt.see('end')
                    self.log_txt.config(state='disabled')
                messagebox.showinfo(
                    '注入测试' if ok else '注入测试失败',
                    ('✓ ' + detail + '\n引擎签名已记忆,Topaz 未升级就不再重测。'
                     if ok else '✘ ' + detail + '\n详见下方日志;调优将按原生参数运行。'),
                    parent=self.root)
            self.root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    def _collect(self):
        enc = 'gpu' if self.vars['enc'].get().startswith('GPU') else 'cpu'
        gpu = self._gpu_idx()
        preset = self.vars['tune'].get()
        custom = None
        if preset == TUNE_PRESETS[3]:
            et = int(float(self.vars['tune_enc'].get() or 512))
            dt = int(float(self.vars['tune_dec'].get() or 384))
            custom = {'chunk': int(float(self.vars['tune_chunk'].get() or 33)),
                      'conv': int(float(self.vars['tune_conv'].get() or 4)),
                      'enc_tile': et, 'enc_overlap': max(8, et // 8),
                      'dec_tile': dt, 'dec_overlap': max(8, dt // 8)}
        tune = tune_settings(preset, custom, gpu,
                             float(self.vars['u_vram'].get() or 96))
        return {
            'mode': self.vars['mode'].get(),
            'input': self.vars['inpath'].get().strip(),
            'trim_frames': int(float(self.vars['trim'].get() or 0)),
            'seg_seconds': 0 if self.vars['mode'].get() == 'fi'
                else int(float(self.vars['seg'].get() or 0)),
            'upscale': {'model': self.vars['u_model'].get(),
                        'scale': float(self.vars['u_scale'].get() or 2.0),
                        'fps': int(float(self.vars['u_fps'].get() or 24)),
                        'strength': round(float(self.vars['u_strength'].get()), 1),
                        'vram': float(self.vars['u_vram'].get() or 96),
                        'enc': enc, 'gpu': gpu, 'tune': tune},
            'fi': {'model': self.vars['f_model'].get(),
                   'fps_in': float(self.vars['f_fps_in'].get() or 0),
                   'fps_out': float(self.vars['f_fps_out'].get() or 0),
                   'slowmo': float(self.vars['f_slowmo'].get() or 1.0),
                   'instances': int(float(self.vars['f_inst'].get() or 2)),
                   'vram': float(self.vars['f_vram'].get() or 0.85),
                   'rdt_on': self.vars['f_rdt'].get(),
                   'rdt_sens': int(float(self.vars['f_sens'].get() or 5)),
                   'scene': self.vars['f_scene'].get(),
                   'enc': enc, 'gpu': gpu},
            'out': {'dir': self.vars['o_dir'].get().strip(),
                    'prefix': self.vars['o_prefix'].get().strip() or 'Topaz',
                    'date_sub': self.vars['o_date'].get(),
                    'keep_audio': self.vars['o_audio'].get()},
            'tune_ui': {'preset': preset,
                        'chunk': self.vars['tune_chunk'].get(),
                        'conv': self.vars['tune_conv'].get(),
                        'enc': self.vars['tune_enc'].get(),
                        'dec': self.vars['tune_dec'].get()},
        }

    def _run(self):
        params = self._collect()
        err = _validate(params)
        if err:
            messagebox.showwarning('参数有误', err, parent=self.root)
            return
        job, e2 = start_job(params)
        if e2:
            messagebox.showinfo('请稍候', e2, parent=self.root)
            return
        self.job = job
        self.shown_log_n = 0
        self.log_txt.config(state='normal')
        self.log_txt.delete('1.0', 'end')
        self.log_txt.config(state='disabled')
        self.btn_run.config(state='disabled')
        self.btn_cancel.config(state='normal')
        self.vars['status'].set('启动中…')
        _save_config({'last': params})

    def _cancel(self):
        if self.job and self.job.status == 'running':
            if messagebox.askokcancel('取消', '确定取消当前任务?', parent=self.root):
                self.job.kill_tree()

    # ---- 引擎状态 / 手动选择 ----
    def _refresh_engine_ui(self):
        """刷新标题栏引擎状态、GPU 下拉、分段/调优提示(引擎变更后调用)。"""
        st = engine_status()
        if st['ok']:
            self.eng_lbl.config(text=f'引擎 OK · {st["gpus"][0]["name"] if st["gpus"] else "无N卡"}',
                                foreground=OK)
        else:
            self.eng_lbl.config(text='引擎不完整: ' + '; '.join(st['errors'])[:90],
                                foreground=ERR)
        if hasattr(self, 'gpu_cb'):
            self.gpu_cb['values'] = ['Auto (自动)'] + [
                f'GPU {g["driver_idx"]}: {g["name"]}' for g in GPUS]
            try:
                self.gpu_cb.current(0)
            except Exception:
                pass
            self._upd_seg()
            self._upd_tune()

    def _engine_dialog(self):
        """引擎设置:自动检测失败时可手动指定 Topaz 安装目录与模型库(持久记忆)。"""
        win = tk.Toplevel(self.root)
        win.title('引擎设置')
        win.transient(self.root)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=14)
        body.pack(fill='both', expand=True)

        st = engine_status()
        cur = _cfg_engine()
        ttk.Label(body, text='自动检测: ' + ('✓ 完整' if st['ok'] else '✗ ' + '; '.join(st['errors'])),
                  foreground=OK if st['ok'] else ERR, wraplength=430,
                  justify='left').grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 10))

        def status_cell(row, ok, text):
            ttk.Label(body, text=text, foreground=OK if ok else TX2,
                      font=(FONT, 8), wraplength=430, justify='left'
                      ).grid(row=row, column=0, columnspan=3, sticky='w')

        ttk.Label(body, text='安装目录(须含 neuroserver\\neuroserver.exe 与 ffmpeg.exe)').grid(
            row=1, column=0, columnspan=3, sticky='w')
        e_install = ttk.Entry(body, width=52)
        e_install.grid(row=2, column=0, sticky='we', pady=(2, 2))
        e_install.insert(0, str(INSTALL) if INSTALL else cur.get('install_dir', ''))
        l_install = ttk.Label(body, text='', font=(FONT, 8))

        def pick_install():
            p = filedialog.askdirectory(title='选择 Topaz Video 安装目录', parent=win)
            if p:
                e_install.delete(0, 'end')
                e_install.insert(0, p)
                upd_install()
        ttk.Button(body, text='浏览…', command=pick_install).grid(
            row=2, column=1, padx=(6, 0))

        def upd_install(*_):
            p = e_install.get().strip()
            l_install.config(text='✓ 有效安装目录' if _valid_install(p)
                             else ('留空=自动检测' if not p else '✗ 缺 neuroserver\\neuroserver.exe 或 ffmpeg.exe'),
                             foreground=OK if _valid_install(p) else (TX2 if not p else ERR))
        e_install.bind('<KeyRelease>', upd_install)
        l_install.grid(row=3, column=0, columnspan=3, sticky='w')

        ttk.Label(body, text='模型库(须含 slp26 或 slp25 子目录;星光权重所在)').grid(
            row=4, column=0, columnspan=3, sticky='w', pady=(10, 0))
        e_store = ttk.Entry(body, width=52)
        e_store.grid(row=5, column=0, sticky='we', pady=(2, 2))
        e_store.insert(0, str(MODEL_STORE) if MODEL_STORE else cur.get('model_store', ''))
        l_store = ttk.Label(body, text='', font=(FONT, 8))

        def pick_store():
            p = filedialog.askdirectory(title='选择模型库目录(含 slp26/ 的 models 目录)',
                                        parent=win)
            if p:
                e_store.delete(0, 'end')
                e_store.insert(0, p)
                upd_store()
        ttk.Button(body, text='浏览…', command=pick_store).grid(
            row=5, column=1, padx=(6, 0))

        def upd_store(*_):
            p = e_store.get().strip()
            l_store.config(text='✓ 有效模型库' if _valid_model_store(p)
                           else ('留空=自动检测' if not p else '✗ 未找到 slp26/slp25 子目录'),
                           foreground=OK if _valid_model_store(p) else (TX2 if not p else ERR))
        e_store.bind('<KeyRelease>', upd_store)
        l_store.grid(row=6, column=0, columnspan=3, sticky='w')

        body.columnconfigure(0, weight=1)
        upd_install()
        upd_store()

        btns = ttk.Frame(body)
        btns.grid(row=7, column=0, columnspan=3, sticky='e', pady=(14, 0))

        def save():
            cfg = _load_config()
            cfg['engine'] = {'install_dir': e_install.get().strip(),
                             'model_store': e_store.get().strip()}
            _save_config(cfg)
            refresh_engine()
            self._refresh_engine_ui()
            win.destroy()

        def reset():
            cfg = _load_config()
            cfg.pop('engine', None)
            _save_config(cfg)
            refresh_engine()
            self._refresh_engine_ui()
            win.destroy()

        ttk.Button(btns, text='保存', command=save).pack(side='left', padx=4)
        ttk.Button(btns, text='恢复自动检测', command=reset).pack(side='left', padx=4)
        ttk.Button(btns, text='取消', command=win.destroy).pack(side='left', padx=4)
        win.grab_set()

    def _open_last(self):
        p = self.last_lbl.cget('text')
        if p and p != '—' and os.path.isfile(p):
            subprocess.Popen(['explorer', '/select,', os.path.normpath(p)])

    def _on_close(self):
        if self.job and self.job.status == 'running':
            if not messagebox.askokcancel('退出', '任务正在运行,退出会取消任务。确定退出?',
                                          parent=self.root):
                return
            self.job.kill_tree()
        self.root.destroy()

    # ---- 轮询刷新 ----
    def _poll(self):
        job = self.job
        if job is not None:
            logs = list(job.log)
            if len(logs) > self.shown_log_n:
                self.log_txt.config(state='normal')
                for line in logs[self.shown_log_n:]:
                    tag = ''
                    if '✘' in line or 'Error' in line or 'error' in line:
                        tag = 'err'
                    elif '✔' in line:
                        tag = 'ok'
                    elif line.startswith('[') and ('超分]' in line or '插帧]' in line
                                                   or '合成]' in line or '试跑]' in line):
                        tag = 'tag'
                    self.log_txt.insert('end', line + '\n', tag or ())
                # 内存友好: 只保留最近 400 行
                n = int(self.log_txt.index('end-1c').split('.')[0])
                if n > 400:
                    self.log_txt.delete('1.0', f'{n - 400}.0')
                self.log_txt.see('end')
                self.log_txt.config(state='disabled')
                self.shown_log_n = len(logs)
            cur = next((s for s in job.steps if s['status'] == 'running'), None)
            self.bar['value'] = job.overall()
            if cur:
                seta = job.step_eta()
                eta_txt = f' · 剩~{seta:.0f}s' if seta else ''
                self.step_lbl.config(text=f'{cur["name"]} — {cur["pct"]}%{eta_txt}'
                                     + (f' · {cur["note"]}' if cur['note'] else ''))
                oeta = job.overall_eta()
                ot = f' · 预计剩 {oeta:.0f}s' if oeta else ''
                self.vars['status'].set(f'运行中 · {job.elapsed()}s{ot}')
            if job.status != 'running':
                self.btn_run.config(state='normal')
                self.btn_cancel.config(state='disabled')
                if job.status == 'done':
                    self.vars['status'].set(f'✔ 完成 ({job.elapsed()}s)', )
                    self.status_lbl.config(foreground=OK)
                    self.step_lbl.config(text='全部步骤完成')
                    self.last_lbl.config(text=job.output)
                    self.bar['value'] = 100
                elif job.status == 'cancelled':
                    self.vars['status'].set('已取消')
                    self.status_lbl.config(foreground=WARN)
                    self.step_lbl.config(text='任务已取消')
                else:
                    self.vars['status'].set('✘ 失败')
                    self.status_lbl.config(foreground=ERR)
                    self.step_lbl.config(text=job.error or '')
                    messagebox.showerror('任务失败', job.error or '未知错误', parent=self.root)
                self.job = None
        self.root.after(400, self._poll)


# ===========================================================================
# main
# ===========================================================================

def main():
    if '--check' in sys.argv:
        st = engine_status()
        lines = [f'引擎: {"OK" if st["ok"] else "不完整"}']
        lines += ['安装目录: ' + str(st['install'] or '未找到'),
                  '模型库: ' + str(st['model_store'] or '未找到'),
                  'slp26 权重: ' + ('✓' if st['slp26_ready'] else '✗'),
                  '模型元数据: ' + str(st['model_meta'] or '未找到'),
                  'GPU: ' + (', '.join(f"#{g['driver_idx']} {g['name']}" for g in st['gpus']) or '未检测到')]
        tc = get_tune_cache()
        if tc is None:
            lines.append('调优注入: 未验证(启动程序后点「测试注入」,或首次带调优任务自动验证)')
        elif tc.get('ok'):
            lines.append(f"调优注入: ✓ 已生效({tc.get('module', '?')},{tc.get('checked_at', '')})")
        else:
            lines.append('调优注入: ✗ 未生效(按原生运行)')
        lines.append('slptune 组件: ' + ('✓' if (SLPTUNE_DIR / 'sitecustomize.py').is_file() else '✗ 缺失'))
        lines += st['errors']
        try:
            print('\n'.join(lines))
        except Exception:
            pass
        r = tk.Tk(); r.withdraw()
        messagebox.showinfo('Topaz 星光独立UI — 引擎检测', '\n'.join(lines))
        return 0 if st['ok'] else 1

    st = engine_status()
    root = tk.Tk()
    try:
        dpi = root.winfo_fpixels('1i')
        if dpi > 96:
            root.tk.call('tk', 'scaling', dpi / 72.0)
    except Exception:
        pass
    try:
        App(root)
    except Exception:
        import traceback
        err = traceback.format_exc()
        try:
            (APP_DIR / 'topaz_ui_error.log').write_text(err, encoding='utf-8')
            messagebox.showerror('启动失败', err[-1500:])
        except Exception:
            pass
        raise
    print('Topaz Starlight UI | 引擎: ' + ('OK' if st['ok'] else '; '.join(st['errors'])))
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())

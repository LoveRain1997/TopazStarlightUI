# Rev.5.5 Topaz 星光独立UI — 使用说明

| 版本记录 | |
|---|---|
| Rev.5.5 \| 2026-10-07 \| 新增「完成后自动关机」:勾选后任务成功完成 60 秒倒计时自动关机(失败/取消不触发),「取消关机」按钮可中止,跑新任务自动作废旧倒计时;勾选状态随配置记忆 | |
| Rev.5.4 \| 2026-10-07 \| 探测崩溃修复+全链调试日志:修复无控制台 exe 里子进程 stdout/stderr 为 None 的 AttributeError(stdin 显式 DEVNULL 断继承+捕获全空自动二进制重试);探测每级打印调试日志(命令/rc/输出大小/stderr 原文)进任务日志与 UI 控制台,「找不到命令 vs 路径问题 vs 捕获失效」一目了然 | |
| Rev.5.3 \| 2026-10-07 \| 探测诊断完善:每级尝试带出 ffmpeg 原始错误行(如 moov atom not found/Invalid data);识别"非视频/未生成完/损坏文件"并给针对性提示;异常记录含具体消息便于远程排查 | |
| Rev.5.2 \| 2026-10-07 \| 探测链路加固:ffprobe 全挂时回退到「ffmpeg -i 解析」(Topaz/系统双候选);修复 Video: 误匹配警告行导致的帧率/时长丢失;探测失败报错改为列出全部已尝试手段+准确修复建议(引擎设置/装ffmpeg) | |
| Rev.5.1 \| 2026-10-07 \| 建立项目独立 git 仓库(源码/注入器/脚本入库);新增打包.bat 一键重建;新增致谢章节 | |
| Rev.5.0 \| 2026-10-06 \| 健壮性+原生风格:UI 换 Windows 原生 vista 主题(系统控件/Segoe UI/浅色窗口/深色控制台日志);新增「引擎设置」对话框——自动检测失败可手动指定安装目录与模型库(实时校验、持久记忆、无效自动回退);引擎状态可运行时刷新 | |
| Rev.4.3 \| 2026-10-06 \| 分段逻辑定稿:插帧移除分段(输入禁用,传值也被忽略);链式改为「分段只切超分→超分完无损合并→插帧整片单趟」,实测分段与不分段帧数完全一致(178=178,接缝零丢失) | |
| Rev.4.2 \| 2026-10-06 \| 插帧放开分段输入(超长片断点保护);新增预估进度:运行中整体/步骤双"预计剩余时间"+跑前按历史实测速度预估超分/插帧耗时(自动记忆秒/帧·百万像素);链式分段对比实测入档 | |
| Rev.4.1 \| 2026-10-06 \| 调优验证机制:「测试注入」一键实测(任意机器,6帧真任务)+ 首跑自动验证 + 引擎签名缓存(Topaz 升级才重测,平时零开销);提示栏显示 ✓已验证/✗/未验证 | |
| Rev.4.0 \| 2026-10-06 \| 新增引擎调优:学 skv89/Topaz-SLP-Launcher 的 sitecustomize 注入思路,在 neuroserver 进程内调 SLP 模型参数(时间块/VAE tile/显存封顶);按显存自动选策略(≥14G 保持原生实测最快),含保守/自定义预设;实测注入生效 | |
| Rev.3.0 \| 2026-10-06 \| 新增长视频分段处理:按显存智能分段(默认)+手动秒数,分割→逐段超分/插帧→无损拼接→合并音轨;实测5段链式通过 | |
| Rev.2.0 \| 2026-10-06 \| 按需求重做:UI 从浏览器改为 tkinter 单窗口桌面程序(内存 ~34MB),PyInstaller 打成单文件 exe(11.7MB,零依赖双击即用);引擎调用层不变,三种模式重新实测通过 | |
| Rev.1.0 \| 2026-10-06 \| 首版:单文件 Web UI(topaz_ui.py),直接调用本机 Topaz Video 1.7.1 引擎完成星光2.6超分 / Apollo·Aion·Chronos 插帧 / 超分+插帧链式,三种模式全部实测通过 | |

---

## 一、这是什么

**不装 ComfyUI、不装 Python、不依赖浏览器**,一个 exe 直接调用你机器上已装好的
Topaz Video(1.7.1)引擎,在桌面窗口里完成:

```
选视频 → [星光2.6 超分] → [Apollo/Aion 插帧] → 合成音轨 → 成品 mp4
```

- 位置:`D:\Users\Administrator\Downloads\TopazStarlightUI\`
- 组成:
  - `Topaz星光UI.exe` —— **成品,双击即用**(PyInstaller 单文件,11.7MB)
  - `topaz_ui.py` —— 源码(tkinter 界面 + 引擎调用,想改参数逻辑就改它)
  - `启动星光UI.bat` —— 双击启动器(优先 exe,兜底 python)
- 资源占用:内存 ~34MB,无后台服务、无浏览器进程
- 引擎:`E:\Program Files\Topaz Labs LLC\Topaz Video`(自动检测:注册表 + 常见路径)
- 模型库:`E:\ProgramData\Topaz Labs LLC\Topaz Video\models\models`(slp26 权重 7.4GB,已确认在位)
- 调用方式与 ComfyUI-TopazStarlight 节点包**完全一致**(neuroserver.exe --once / ffmpeg tvai_fi),参数默认值同源

## 二、启动

**双击 `Topaz星光UI.exe`**(或 `启动星光UI.bat`)。命令行方式:

```bat
Topaz星光UI.exe               # 桌面窗口
python topaz_ui.py --check    # 引擎环境检测(弹窗)
python topaz_ui.py            # 源码运行(需 Python 3.10+,纯标准库)
```

窗口标题栏右侧显示引擎状态(引擎 OK · 显卡名);不完整时红色显示原因。

界面为 **Windows 原生控件风格**(系统按钮/输入框/下拉框,Segoe UI 字体,浅色窗口,深色控制台日志区),高 DPI 渲染与系统一致。

### 2.1 引擎找不到?手动指定(标题栏「引擎设置…」)

自动检测顺序:手动配置 → 注册表 InstallDir → 常见安装路径(C/D/E 盘)。模型库另按盘符扫 ProgramData。都失败时点标题栏「**引擎设置…**」:

- **安装目录**:选 Topaz Video 所在目录,须含 `neuroserver\neuroserver.exe` 与 `ffmpeg.exe`(对话框实时校验,绿 ✓ / 红 ✗)
- **模型库**:选含 `slp26\` 或 `slp25\` 的 models 目录(星光权重所在)
- 「保存」持久记忆到 config.json(下次启动直接用);「恢复自动检测」清掉手动配置
- **无效手动路径自动回退**:手填的目录失效(比如 Topaz 被移动/卸载)时不卡死,自动落回注册表/常见路径检测
- 保存后 GPU 下拉、分段/调优提示全部即时刷新,无需重启

## 三、操作流程

1. **输入视频**:粘贴路径或点「浏览…」——自动探测分辨率、帧率、帧数、音轨
2. **模式**:星光超分 / 插帧 / 超分+插帧(链式,先升清再补帧)
3. **参数**(默认即节点包推荐值,通常不用动):
   - 超分:模型(星光2.6/Astra 全家族)、放大倍数(1.0~4.0,支持两位小数;→1080p/2K/4K 按钮按源宽反算精确倍率)、增强强度滑杆(0.7 柔和 / 1.0 默认 / 1.3 细节最猛)、显存上限(96=不设限;16G 卡建议 14)、输出帧率
   - 插帧:模型(Aion=AI 视频推荐又快又合适;Apollo=质量主力;Chronos=备选)、输入帧率(自动填探测值)→ 输出帧率、慢动作倍数、并行实例(2 比 1 快约 31%)、显存占用、重复帧检测+敏感性、场景检测
4. **GPU / 编码 / 试跑 / 分段**:
   - 分段秒数:**0=按显存智能分段**(默认)。公式:`每段秒数 = 显存GiB × 6 × (1080p面积 ÷ 输出面积)`,夹在 20~300s;显存上限填 96(不设限)时按显卡实际显存算。输入框旁实时显示「自动 ≈Ns/段 · 全程约 N 段」
   - 也可手动填 10~3600 秒。**分段只作用于星光超分**;纯插帧模式不分段(流式处理,输入框禁用,传值也被忽略)
   - 分段流程:`精确分割(nvenc qp18 重编码,帧对齐) → 逐段超分 → 无损合并成整片 → 插帧(整片单趟) → 合并原音轨`,防长视频超分显存溢出、进度更细、单段失败只损失该段
   - **接缝零丢失(实测)**:链式流程里插帧吃的是超分合并后的整片,分段与不分段输出帧数完全一致(3s 片分 3 段 @60fps:178 帧 = 178 帧),时长/音轨/分辨率不受影响
   - GPU 选择、输出编码(GPU=h264_nvenc / CPU=h264_mf)、试跑帧数(0=全部;先填 10 试通)
5. **输出**:目录(默认=输入视频所在目录)、文件名前缀、日期子文件夹、保留原音频、**完成后自动关机**(挂机跑长视频用:任务成功完成 60 秒倒计时后关机,失败/取消不触发;出现「取消关机」按钮或命令行 `shutdown /a` 可中止;再跑新任务旧倒计时自动作废)
6. **开始处理 + 预估进度**:
   - **跑前预估**:日志先给 `[预估] 超分 ~74秒 + 插帧 ~53秒`(按本机历史实测速度"秒/帧·百万像素"推算,每次跑完自动记忆更新;第一次跑没有基准则不显示)
   - **运行中**:状态栏「运行中 · 123s · 预计剩 45s」(整体)+ 步骤行「星光超分 — 64% · 剩~30s」(当前步骤);进度≥3% 才开始估,前段模型加载期显示为无预估
   - 进度条(分段时按段累计)+ 实时引擎日志(自动滚动、只留最近 400 行省内存),运行中可取消;完成后「上次成品」栏点「打开位置」直接定位成品
7. **试跑建议**:「试跑帧数=10」跑通看效果,再改回 0(全部帧)正式跑

## 四、引擎调优(仅星光超分)

思路源自开源项目 **skv89/Topaz-SLP-Launcher**(MIT):neuroserver 的星光模型是编译 .pyd,
行为由模块级全局变量控制。本项目用同款机制——`PYTHONPATH` 挂 `slptune/sitecustomize.py`,
在 neuroserver 的 Python 里钩住 .pyd 导入,把参数写进模型模块(不改 Topaz 安装任何文件,失败自动回退原生)。

**原生默认值**(本机 `models.slp25/m0vm5nn8kicb.pyd` 实测读取):
时间块 PIX_CHUNK_SIZE=121(须 4n+1)、重叠 21、VAE 编码 tile 640/80、解码 tile 480/60、VAE_CONV_MAX_MEM=不限。

**预设**:

| 预设 | 行为 | 适用 |
|---|---|---|
| 自动(默认,推荐) | ≥14G显存→保持原生(实测最快);10~14G→块49+VAE封顶;8~10G→块33+缩tile;<8G→最保守 | 不知道选什么就选它 |
| 原生默认 | 完全不注入,引擎出厂参数 | 追求与 GUI 完全一致 |
| 保守省显存 | 块33 + VAE上限(显存×0.4)G + tile 512/384 | 显存不足 OOM / 借共享内存变慢时救急 |
| 自定义 | 时间块(4n+1,5~161)/ VAE上限GB / 编码tile / 解码tile | 想自己实验 |

**本机实测数据**(RTX 5080 16G,144帧 320×240×2):
- 原生(块121):46.2s —— 最快基准
- 块33+封顶2G:94.6s —— 慢约2x,但显存峰值显著更低(小显存卡的可用解)
- tile 拉大到 1024/960:显存打满、GPU 100% 但爬行 —— **16G 卡别拉大 tile**

结论已写进自动策略:**大显存=原生最优,小显存=降块窗+封VAE续命**。调优参数会打进任务日志(`[调优]`/`[slp-tune]` 行),可核对实际生效值。纯插帧模式不注入。

### 4.1 怎么知道注入生效了?(换机器必读)

三条机制,回答「任意机器能用吗 / 怎么确认生效 / 每次都要测吗」:

1. **「测试注入」按钮(任意机器一键实测)**:引擎调优区右侧。点击后自动生成 6 帧小片→带注入真跑一次 neuroserver(约1分钟,主要是模型加载)→弹窗报告 `✓ 注入生效 (models.slp25.m0vm5nn8kicb)` 或失败原因,详细过程进日志。只依赖本机装好的 Topaz,与显卡型号无关(无 N 卡会直接报引擎错误)。
2. **首跑自动验证(零额外开销)**:每次带调优参数的任务,跑完自动检查自己的 `[slp-tune] applied` 日志——生效则写缓存并提示「已自动验证并记忆」;不生效则提示「⚠ 已按原生参数运行」,任务不失败,只是退回出厂参数。
3. **引擎签名缓存(不用每次测)**:验证结果绑定引擎指纹(neuroserver.exe + slp25 模块 .pyd 的大小/修改时间哈希),存在 config.json。**Topaz 没升级 → 一直是 ✓ 状态,永不再测**;Topaz 升级/换机 → 签名不符自动失效,提示栏回到「未验证」,下次跑任务或点按钮重新确认。提示栏实时显示:`✓已验证(日期时间)` / `✗验证失败` / `未验证(首跑自动验/点「测试注入」)`。

命令行 `python topaz_ui.py --check` 也会输出调优注入验证状态与 slptune 组件在位情况。

## 五、每种模式的内部流程

| 模式 | 引擎调用 | 音轨 |
|---|---|---|
| 星光超分 | `neuroserver.exe --once --filters '[{"model":"slp-26","enhancement_strength":1.0,"softness":1}]' --upscale-factor 2 ...` | 结尾从原视频合并(aac 192k, +faststart) |
| 插帧 | `ffmpeg.exe -hwaccel cuvid -filter_complex '[0:v]hwdownload,format=nv12,format=rgb48le,tvai_fi=model=aion-1:rdt=0.01:fps=60:...'` | 同上 |
| 超分+插帧 | 上一级输出作为下一级输入串行执行 | 同上 |

分段时每段都走一遍上表流程,最后 `ffmpeg -f concat -c copy` 无损拼接(各段编码参数完全一致)再合并音轨,画面与不分段完全同规格,音画同步不受影响(分割按帧精确对齐)。

- 输出编码:GPU=h264_nvenc constqp qp18(与节点包 NS_ENC 一致);CPU 回退=h264_mf(Topaz ffmpeg 是 LGPL 构建,无 libx264)
- 插帧首次用新分辨率偶发 0xC0000005 编译崩溃 → 自动重试一次(与节点包同款处理)
- Astra 系列自动拦截 <9 帧输入;插帧自动校验输出帧率 ≥ 输入帧率
- 中间临时文件放输出目录,任务结束(含取消/失败)自动清理;关窗=自动取消任务

## 六、本机踩坑记录(写死在代码里了)

1. **Topaz 自带 ffmpeg/ffprobe 是 `--disable-decoder=h264/hevc` 的定制构建**:它的 ffprobe 对 h264 跑 `-show_streams` 会段错误。探测一律优先用系统 ffprobe(winget 装的 gyan 全功能版),Topaz 的只做兜底,再不行就解析 `ffmpeg -i` 输出。
2. **凡调用 Topaz ffmpeg 做解码的命令必须带 `-hwaccel cuvid`**(试跑裁剪、插帧都是),否则无软解可用直接报错。
3. **模型库不在 C 盘**:GUI 把模型放到了 `E:\ProgramData\...\models\models`,代码按盘符(C/D/E/F)自动扫。
4. neuroserver 必须以自己目录为 cwd 启动(内置 python312 + torch 2.7.0+cu128)。
5. GPU 选择:Auto=引擎默认;手动选卡用 `CUDA_DEVICE_ORDER=PCI_BUS_ID + CUDA_VISIBLE_DEVICES=<驱动号> + --device 0`,不受 CUDA FASTEST_FIRST 枚举顺序影响。本机现为单卡 RTX 5080,Auto 即可。
6. **DPI 感知必须在 `import tkinter` 之前调用** `SetProcessDpiAwareness`(150% 缩放屏实测:放后面会坐标错位、字体模糊);窗口尺寸按内容自适应并夹在屏幕范围内。
7. **tkinter 布局坑**:行容器若晚于子控件创建(子控件作为实参先生成),不透明行框会盖住子控件——`f.lower()` 压底解决;PyInstaller `--windowed` 下 `sys.stdout` 是 `None`,reconfigure 要判空。
8. **SLP 调优注入**:星光模型 .pyd 可被本机 Python 3.12 直接导入读取默认值(`models.slp25/m0vm5nn8kicb`);注入靠 PYTHONPATH+sitecustomize 钩住 `ExtensionFileLoader.exec_module`,模块导入后 setattr 全局变量——只改进程内存,不动安装文件;时间块必须是 4n+1(33/49/121),tile 过大反而显存溢出变慢。

## 七、与 ComfyUI 节点包的差异

| | ComfyUI-TopazStarlight | 本独立UI |
|---|---|---|
| 依赖 | ComfyUI + VHS + torch | **无(exe 双击即用)** |
| 界面 | ComfyUI 节点图 | tkinter 单窗口,内存 ~34MB |
| 输入 | ComfyUI 里的帧张量(需中间编码 qp14,有一代损耗) | **原始视频直接喂引擎,零额外损耗** |
| 音频 | LoadVideo.audio 旁路到 VideoCombine | 结尾自动从原视频合并 |
| 适用 | 接 AI 生成工作流(生成→星光一条龙) | 手头已有视频文件,直接处理 |

以后想接回 ComfyUI 工作流(如 Wan 生成的帧直接进星光),仍用原节点包;两边可共存,引擎是同一个。

## 八、改了源码想重新打包

双击 **`打包.bat`**,或命令行:

```bat
pip install pyinstaller
pyinstaller --onefile --windowed --name "Topaz星光UI" --add-data "%CD%\slptune;slptune" --workpath _build --specpath _build --distpath . topaz_ui.py
```

(add-data 必须用绝对路径;slptune 目录与 topaz_ui.py 同在,已内嵌进 exe,成品仍是单文件。exe 与构建产物不入 git 库,源码都在库里,随时可重建。)

## 九、项目仓库与致谢

本项目自 Rev.5.1 起由独立 git 仓库管理(`TopazStarlightUI\.git`,main 分支):
入库 `topaz_ui.py`(全部源码)、`slptune/sitecustomize.py`(引擎调优注入器)、`打包.bat`、`启动星光UI.bat`、`README.md`;
不入库 exe 与 config.json(构建产物/本地状态,可随时重建)。上级 Downloads 仓库的 md 白名单机制照常生效。

**致谢**(排名不分先后):

| 致谢对象 | 本项目从中得到什么 |
|---|---|
| [Topaz Labs](https://www.topazlabs.com/) | Topaz Video 1.7.1 引擎本体:neuroserver(星光扩散超分)、ffmpeg tvai 滤镜族(插帧)、模型权重与授权环境。本项目只是它的一个命令行调度界面 |
| [skv89/Topaz-SLP-Launcher](https://github.com/skv89/Topaz-SLP-Launcher)(MIT) | 引擎调优的核心思路:PYTHONPATH+sitecustomize 钩住 .pyd 导入、在 neuroserver 进程内改 SLP 模块全局变量,以及"按显存留安全余量"的策略思想。本项目的 `slptune/sitecustomize.py` 是受其启发的独立精简实现(未复用其代码),特此致谢 |
| ComfyUI-TopazStarlight 节点包 | 引擎调用方式的同源出处——neuroserver 命令行参数、NS_ENC 编码串、tvai_fi 滤镜参数(GUI 同款)、分段/进度解析等均与该节点包一脉相承,并继承其大量踩坑经验(禁软解、0xC0000005 重试、Astra≥9帧等) |
| [extremecoders-re/pyinstxtractor](https://github.com/extremecoders-re/pyinstxtractor) | 研究阶段用它解开 Topaz-SLP-Launcher 的 PyInstaller 包,才得以确认 sitecustomize 注入机制(仅用于学习理解,未复制其代码) |
| Python 标准库 / tkinter / PyInstaller | 单文件、零第三方依赖的桌面应用基础;FFmpeg(Topaz 内置 LGPL 构建与 gyan 完整构建)承担全部音视频分割/拼接/探测工作 |

项目沿革:Rev.1.0 Web UI 原型 → Rev.2.0 tkinter 桌面化+exe → Rev.3.0 长视频分段 → Rev.4.x 引擎调优+验证+预估进度 → Rev.5.x 原生风格+引擎手动选择。

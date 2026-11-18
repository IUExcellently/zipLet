# Ziplet

在本地递归解压归档并保存经过校验的图片和视频。
支持 Windows、macOS 和 Linux。运行需要 Python 3.10+、官方 7-Zip（`7zz` 或 `7z`）和 Pillow。校验视频时需要 `ffprobe` 在 PATH 中。

### 启动与配置

在项目目录运行 `python3 main.py`。Windows PowerShell 可运行 `py -3 main.py`。首次运行会创建缺失的 `ziplet.conf` 和空的 `zipPwd.txt`，并提示填写。配置文件预置两个必填项和带注释的路径示例。已有文件会保留原内容。

在项目根目录的 UTF-8 `ziplet.conf` 中填写输入和输出目录，例如 macOS 或 Linux 可以写：

```ini
ZIPLET_INPUT_DIR=~/Downloads
ZIPLET_OUTPUT_DIR=~/ZipletOutput
```

Windows 示例：

```ini
ZIPLET_INPUT_DIR=C:\Archives
ZIPLET_OUTPUT_DIR=D:\Media
```

保存后再次运行即可。相对路径以项目目录为基准，带空格的路径可加引号。程序直接读取该文件中的配置。默认并发为 1，可通过 `ZIPLET_MAX_WORKERS` 调整。

Windows 会从 PATH 和 7-Zip 默认安装目录查找解压引擎。请使用支持硬链接的输出卷，例如本地 NTFS，并选择访问权限受限的输出目录。安装 Pillow 可运行 `python3 -m pip install Pillow`，Windows 可用 `py -3 -m pip install Pillow`。

### 密码与输出

默认密码文件为项目根目录下的 `zipPwd.txt`，每行一个候选密码。空文件可用于处理无密码归档。可在 `ziplet.conf` 中用 `ZIPLET_PASSWORD_FILE` 指定自定义路径，并将该文件放在仓库外或仓库内已被 Git 忽略的 `.ziplet-local/` 目录。

输入和输出目录须相互独立。媒体完整校验且成功发布后，程序会删除源包和本任务的工作区。重跑时，已有输出只有在源包指纹和媒体文件均与发布记录匹配时才会清理原档。解压失败、空间不足或已有输出无法核验时，源包会保留。

每个外层归档使用输入相对路径作为输出目录名。例如 `in/sub/a.7z` 的媒体保存在 `out/sub/a.7z/`。输出目录中的 `.ziplet` 保存检查点和密码候选标识，重跑时会校验并复用已有结果。建议将密码文件保存在扫描目录之外，并限制访问权限。

控制台按外层归档显示解压轮次、层级、密码尝试序号、压缩包大小、已解压字节和状态。解压耗时超过一秒时会定期更新字节数。并发运行时，各行可通过开头的归档路径区分。

输入目录及任意解包层级都支持同目录、同名的数字分卷，例如 `a.7z.001`、`a.7z.002` 或 `a.zip.001`、`a.zip.002`。程序检查编号连续性，并让 7-Zip 从首卷读取整组分卷，核对卷数、总大小和内部归档格式。无需写出一份合并文件。成功发布后清理任务生成的中间分卷及输入目录中该任务的原始分卷。

运行时会把应用控制台的进度、错误和退出码追加到项目根目录的 `ziplet-run.log`。该文件由 Git 跟踪，排查 Windows 问题时可提交对应运行记录。日志包含归档名和本地路径，提交前请检查其中是否有需要隐去的信息。

### 清理失败任务

关闭正在运行的 Ziplet，然后在 PyCharm 运行 `cleanup_failed.py`。脚本顶部的 `APPLY_CLEANUP = False` 默认仅预览。确认清单后改为 `APPLY_CLEANUP = True` 并重新运行，即可清理。清理完毕后改回 `False`，下次运行会重新预览。

脚本读取 `ziplet.conf`，只处理输出目录 `.ziplet` 内没有发布记录的任务暂存目录。清理后这些任务需要重新解包。原始归档、已发布的媒体和密码命中记录会保留。

测试时可在 `main.py` 顶部设置 `MAX_NEW_ARCHIVES = 2`，每次最多开始处理两个新原包。默认 `None` 表示不限，`0` 表示只核验和补清理已有输出。整组分卷算一个，嵌套解压轮次不计入。已开始但解压失败的任务也占本次名额。

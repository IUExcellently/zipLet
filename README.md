# Ziplet

在本地递归解压归档并保存经过校验的图片和视频。
运行需要 macOS 或 Linux、Python 3.10+、官方 7-Zip（`7zz` 或 `7z`）、Pillow 和 `ffprobe`。

```sh
python3 -m pip install Pillow
export ZIPLET_INPUT_DIR=/path/to/archives
export ZIPLET_OUTPUT_DIR=/path/to/media
python3 main.py
```

首次运行时，如果密码文件缺失，程序会创建空文件并提示填写。本次运行结束后，每行填写一个候选密码，再运行原命令。已有空文件可用于处理无密码归档。默认密码文件为项目根目录下的 `zipPwd.txt`。使用 `ZIPLET_PASSWORD_FILE` 自定义路径时，可放在仓库外或仓库内已被 Git 忽略的 `.ziplet-local/` 目录。使用 `ZIPLET_MAX_WORKERS` 控制并发（默认 1）。

输入和输出目录须相互独立。成功发布后清理工作区，源包保留。失败或空间不足时查看逐任务输出，处理原因后用相同配置重跑。

每个外层归档使用输入相对路径作为输出目录名。例如 `in/sub/a.7z` 的媒体保存在 `out/sub/a.7z/`。输出目录中的 `.ziplet` 保存检查点和密码候选标识，重跑时会校验并复用已有结果。建议将密码文件保存在扫描目录之外，并限制访问权限。

控制台按外层归档显示解压轮次、层级、密码尝试序号、压缩包大小、已解压字节和状态。解压耗时超过一秒时会定期更新字节数。并发运行时，各行可通过开头的归档路径区分。

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import os
from pathlib import Path
import sys
import traceback


# 每次最多开始处理的最外层原包数量，None 表示不限。
MAX_NEW_ARCHIVES = None


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_KEYS = {"ZIPLET_INPUT_DIR", "ZIPLET_OUTPUT_DIR", "ZIPLET_PASSWORD_FILE", "ZIPLET_MAX_WORKERS"}

CONFIG_TEMPLATE = r"""# 填写输入目录和输出目录后保存，再运行 Ziplet。
# Windows 示例
# ZIPLET_INPUT_DIR=C:\Archives
# ZIPLET_OUTPUT_DIR=D:\Media
# macOS / Linux 示例
# ZIPLET_INPUT_DIR=~/Downloads
# ZIPLET_OUTPUT_DIR=~/ZipletOutput

ZIPLET_INPUT_DIR=
ZIPLET_OUTPUT_DIR=
"""


def read_config_file(path):
    config = {}
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            key = key.strip()
            if not separator or key not in CONFIG_KEYS:
                raise ValueError(f"ziplet.conf 第 {number} 行须填写受支持的配置项，格式为 键=值")
            value = value.strip()
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    raise ValueError(f"ziplet.conf 第 {number} 行的引号未闭合")
                value = value[1:-1]
            config[key] = value
    return config


def create_file(path, content=""):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    return True


def config_path(value):
    return (PROJECT_DIR / Path(value).expanduser()).resolve()


def read_config(config, password_file):
    source_value = config.get("ZIPLET_INPUT_DIR")
    output_value = config.get("ZIPLET_OUTPUT_DIR")
    missing = [key for key in ("ZIPLET_INPUT_DIR", "ZIPLET_OUTPUT_DIR") if not config.get(key)]
    if missing:
        raise ValueError(f"请在 ziplet.conf 填写 {'、'.join(missing)}")
    source = config_path(source_value)
    output = config_path(output_value)
    if not source.is_dir():
        raise ValueError("输入目录不存在")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("输入和输出目录必须相互独立")
    try:
        workers = int(config.get("ZIPLET_MAX_WORKERS", "1"))
        if workers < 1:
            raise ValueError
    except ValueError:
        raise ValueError("ZIPLET_MAX_WORKERS 必须是正整数") from None
    with password_file.open(encoding="utf-8", newline="") as stream:
        passwords = list(dict.fromkeys(line.rstrip("\r\n") for line in stream))
    return source, output, workers, [password for password in passwords if password]


def run_cli():
    argparse.ArgumentParser(description="递归解压归档并校验和保存媒体").parse_args()
    try:
        config_file = PROJECT_DIR / "ziplet.conf"
        created = create_file(config_file, CONFIG_TEMPLATE)
        config = read_config_file(config_file)
        password_file = config_path(config.get("ZIPLET_PASSWORD_FILE") or "zipPwd.txt")
        password_file.parent.mkdir(parents=True, exist_ok=True)
        password_created = create_file(password_file)
        if created:
            print(f"已创建配置文件 {config_file}。请填写 ZIPLET_INPUT_DIR 和 ZIPLET_OUTPUT_DIR 后重新运行。")
        if password_created:
            print(f"已创建空密码文件 {password_file}。如需密码，请每行填写一个候选密码后重新运行。")
        if created or password_created:
            return 1
        config = read_config(config, password_file)
        from ziplet import run
        return run(*config, max_new_archives=MAX_NEW_ARCHIVES)
    except (OSError, ValueError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


class RunLog:
    def __init__(self, console, file):
        self.console = console
        self.file = file

    def write(self, text):
        self.file.write(text)
        self.file.flush()
        return self.console.write(text)

    def flush(self):
        self.file.flush()
        self.console.flush()


def main():
    path = PROJECT_DIR / "ziplet-run.log"
    try:
        log = path.open("a", encoding="utf-8", errors="backslashreplace")
    except OSError as error:
        print(f"无法打开运行日志 {path}：{error}", file=sys.stderr)
        return 1
    with log, redirect_stdout(RunLog(sys.stdout, log)), redirect_stderr(RunLog(sys.stderr, log)):
        print(f"\n=== Ziplet 开始运行 {datetime.now().astimezone().isoformat(timespec='seconds')} ===",
              flush=True)
        try:
            status = run_cli()
        except KeyboardInterrupt:
            print("运行已由 Ctrl+C 中断", file=sys.stderr)
            status = 130
        except SystemExit as error:
            status = error.code
        except Exception:
            traceback.print_exc()
            status = 1
        print(f"=== Ziplet 运行结束，退出码 {status} ===", flush=True)
        return status


if __name__ == "__main__":
    sys.exit(main())

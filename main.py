import argparse
import os
from pathlib import Path
import sys


def candidate_file():
    default = Path(__file__).resolve().with_name("zipPwd.txt")
    return Path(os.environ.get("ZIPLET_PASSWORD_FILE", default)).expanduser()


def read_config(password_file=None):
    source_value = os.environ.get("ZIPLET_INPUT_DIR")
    output_value = os.environ.get("ZIPLET_OUTPUT_DIR")
    if not source_value or not output_value:
        raise ValueError("请设置 ZIPLET_INPUT_DIR 和 ZIPLET_OUTPUT_DIR")
    source = Path(source_value).expanduser().resolve()
    output = Path(output_value).expanduser().resolve()
    if not source.is_dir():
        raise ValueError("输入目录不存在")
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("输入和输出目录必须相互独立")
    try:
        workers = int(os.environ.get("ZIPLET_MAX_WORKERS", "1"))
        if workers < 1:
            raise ValueError
    except ValueError:
        raise ValueError("ZIPLET_MAX_WORKERS 必须是正整数") from None
    if password_file is None:
        password_file = candidate_file()
    with password_file.open(encoding="utf-8", newline="") as stream:
        passwords = list(dict.fromkeys(line.rstrip("\r\n") for line in stream))
    return source, output, workers, [password for password in passwords if password]


def main():
    argparse.ArgumentParser(description="递归解压归档并校验和保存媒体").parse_args()
    try:
        password_file = candidate_file()
        password_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            # 独占创建，避免并发启动时覆盖已有的密码文件。
            descriptor = os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
            print(f"已创建空密码文件 {password_file}。请每行填写一个候选密码后重新运行。")
            return 1
        config = read_config(password_file)
        from ziplet import run
        return run(*config)
    except (OSError, ValueError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

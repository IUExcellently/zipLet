import argparse
from pathlib import Path
import re
import shutil
import sys

from main import PROJECT_DIR, config_path, read_config_file
from ziplet import is_redirected, lock_output


def clean(apply):
    config = read_config_file(PROJECT_DIR / "ziplet.conf")
    input_value = config.get("ZIPLET_INPUT_DIR")
    output_value = config.get("ZIPLET_OUTPUT_DIR")
    if not input_value or not output_value:
        raise ValueError("请在 ziplet.conf 中填写输入和输出目录")
    source = config_path(input_value)
    output = config_path(output_value)
    if source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("输入和输出目录必须相互独立")

    work = output / ".ziplet"
    if is_redirected(work):
        raise ValueError("工作目录是链接或目录联接点")
    if not work.is_dir():
        print(f"没有待清理的工作目录：{work}")
        return 0

    lock = work / "lock"
    if is_redirected(lock):
        raise ValueError("工作目录锁文件是链接")
    with lock_output(lock):
        return clean_workspaces(work, apply)


def clean_workspaces(work: Path, apply: bool):
    errors = 0
    matched = 0
    for workspace in sorted(work.iterdir()):
        if not re.fullmatch(r"[0-9a-f]{64}", workspace.name):
            continue
        if is_redirected(workspace) or not workspace.is_dir():
            continue
        journal = workspace.with_suffix(".json")
        if journal.exists() or is_redirected(journal):
            print(f"保留已有发布记录的任务：{workspace}")
            continue
        matched += 1
        if not apply:
            print(f"待清理：{workspace}")
            continue
        try:
            shutil.rmtree(workspace)
        except OSError as error:
            print(f"清理失败：{workspace}｜{error}", file=sys.stderr)
            errors += 1
        else:
            print(f"已清理：{workspace}")
    if not matched:
        print("没有待清理的失败任务暂存目录")
    elif not apply:
        print("当前仅预览。确认后加 --apply 执行清理。")
    return 1 if errors else 0


def main():
    parser = argparse.ArgumentParser(description="清理 Ziplet 未发布任务留下的暂存目录")
    parser.add_argument("--apply", action="store_true", help="实际删除暂存目录，默认仅预览")
    try:
        return clean(parser.parse_args().apply)
    except (OSError, ValueError) as error:
        print(f"无法清理：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

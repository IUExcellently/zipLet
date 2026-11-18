from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import subprocess
from threading import Lock
import time
import unicodedata
import zipfile


class TaskError(Exception):
    pass


def archive_type(path):
    with path.open("rb") as stream:
        header = stream.read(8)
    signatures = {
        bytes.fromhex("377abcaf271c"): "7z",
        bytes.fromhex("526172211a0700"): "rar",
        bytes.fromhex("526172211a070100"): "rar5",
        b"MSWIM\0\0\0": "wim",
        b"PK\x03\x04": "zip",
        b"PK\x05\x06": "zip",
    }
    for magic, kind in signatures.items():
        if header.startswith(magic):
            if kind == "zip" and zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as document:
                    names = set(document.namelist())
                    if "[Content_Types].xml" in names and names.intersection(
                        {"ppt/presentation.xml", "word/document.xml", "xl/workbook.xml"}
                    ):
                        return None
            return kind
    return None


def engine_call(engine, command, source, kind, password, *options, on_progress=None):
    arguments = [engine, command, f"-t{kind}", "-sccUTF-8", "-y",
                 f"-p{password}" if password else "-p", *options, "--", str(source)]
    try:
        with subprocess.Popen(
            arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
        ) as process:
            deadline = time.monotonic() + 3600
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TaskError("解压引擎执行超时")
                    try:
                        stdout, stderr = process.communicate(
                            timeout=min(1, remaining) if on_progress else remaining
                        )
                    except subprocess.TimeoutExpired:
                        if on_progress:
                            on_progress()
                    else:
                        return subprocess.CompletedProcess([], process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    except ValueError:
        raise TaskError("无法启动解压引擎，请检查参数") from None


def members_and_size(listing):
    _, separator, records = listing.replace("\r\n", "\n").partition("\n----------\n")
    if not separator:
        raise TaskError("无法读取归档成员清单")
    members = {}
    total = 0
    for record in records.strip("\n").split("\n\n"):
        if not record:
            continue
        fields = {}
        for line in record.splitlines():
            key, sep, value = line.partition(" = ")
            if not sep or key in fields:
                raise TaskError("归档成员清单存在歧义")
            fields[key] = value
        name = fields.get("Path", "")
        parts = name.split("/")
        windows_path = PureWindowsPath(name)
        if (any(part in ("", ".", "..") for part in parts)
                or windows_path.drive or "\\" in name or "\x00" in name
                or any(":" in part for part in parts)):
            raise TaskError("归档包含非法成员路径")
        attributes = fields.get("Attributes", "")
        if (any(("link" in key.lower() or "reparse" in key.lower())
                    and value not in ("", "-")
                    and not (key == "Links" and value == "1")
                    for key, value in fields.items())
                or fields.get("Alternate Stream") == "+"
                or fields.get("Anti") == "+"
                or re.search(r"(?:^|\s)[lbcps][rwxstST-]{9}(?:\s|$)", attributes)
                or re.search(r"\b0[67][0-7]{4}\b", attributes)):
            raise TaskError("归档包含链接或特殊成员")
        directory = fields.get("Folder") == "+" or attributes.startswith("D")
        size = fields.get("Size", "")
        if not directory and not size.isdecimal():
            raise TaskError("归档成员大小不可用")
        normalized = unicodedata.normalize("NFC", name).casefold()
        if normalized in members:
            raise TaskError("归档包含重名成员")
        members[normalized] = directory
        if not directory:
            total += int(size)
    for name in members:
        parts = name.split("/")
        if any(members.get("/".join(parts[:i])) is False for i in range(1, len(parts))):
            raise TaskError("归档成员路径冲突")
    return members, total


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extracted_bytes(directory):
    total = 0
    for item in directory.rglob("*"):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def unpack(engine, source, kind, candidates, destination, budget, progress=None):
    rejected = "密码候选耗尽"
    for attempt, candidate in enumerate(candidates, 1):
        def report(status, written=0, total=None):
            if progress:
                progress(f"尝试 {attempt}/{len(candidates)}｜{status}", written, total)

        report("检查归档")
        listing = engine_call(engine, "l", source, kind, candidate, "-slt")
        if listing.returncode:
            message = (listing.stdout + listing.stderr).lower()
            if "wrong password" in message or "password is incorrect" in message:
                report("密码未通过，继续尝试")
                continue
            if "unexpected end" in message or "headers error" in message:
                raise TaskError("归档损坏")
            raise TaskError("归档清单读取失败，原因未决")
        members, size = members_and_size(listing.stdout)
        # 独立暂存目录避免错误密码写出的不完整文件混入下一次试验。
        with budget.reserve(size):
            destination.mkdir()
            try:
                report("解压中", total=size)
                result = engine_call(
                    engine, "x", source, kind, candidate, f"-o{destination}",
                    on_progress=(lambda: report("解压中", extracted_bytes(destination), size))
                    if progress else None,
                )
                message = (result.stdout + result.stderr).lower()
                if result.returncode:
                    if "no space left" in message or "disk full" in message:
                        raise TaskError("磁盘空间不足，任务暂停")
                    if "wrong password" in message or "data error" in message or "crc failed" in message:
                        rejected = "密码候选耗尽或归档完整性验证失败"
                        report("验证未通过，继续尝试", extracted_bytes(destination), size)
                        shutil.rmtree(destination)
                        continue
                    raise TaskError("解包失败，原因未决")
                extracted = set()
                for item in destination.rglob("*"):
                    mode = item.lstat().st_mode
                    name = unicodedata.normalize("NFC", item.relative_to(destination).as_posix()).casefold()
                    if stat.S_ISREG(mode) and item.stat().st_nlink == 1 and members.get(name) is False:
                        extracted.add(name)
                    elif not stat.S_ISDIR(mode):
                        raise TaskError("解包产生未声明文件或特殊成员")
                if extracted != {name for name, directory in members.items() if not directory}:
                    raise TaskError("解包结果与成员清单不符")
                report("解压完成", extracted_bytes(destination), size)
                encrypted = "Encrypted = +" in listing.stdout
                return candidate if encrypted else None
            except BaseException:
                if destination.exists():
                    shutil.rmtree(destination)
                raise
    raise TaskError(rejected)


class DiskBudget:
    def __init__(self, volume):
        self.volume = volume
        self.lock = Lock()
        self.reserved = 0

    @contextmanager
    def reserve(self, size):
        with self.lock:
            if shutil.disk_usage(self.volume).free - self.reserved < max(1, size):
                raise TaskError("磁盘空间不足，任务暂停")
            self.reserved += size
        try:
            yield
        finally:
            with self.lock:
                self.reserved -= size


class State:
    def __init__(self, directory, passwords):
        self.path = directory / "state.sqlite3"
        self.lock = Lock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value BLOB)")
            db.execute("INSERT OR IGNORE INTO settings VALUES ('key', ?)", (os.urandom(32),))
            key = db.execute("SELECT value FROM settings WHERE name = 'key'").fetchone()[0]
            db.execute("""CREATE TABLE IF NOT EXISTS archives (
                task TEXT, path TEXT, depth INTEGER, digest TEXT, candidate TEXT,
                PRIMARY KEY (task, path, depth, digest))""")
        self.passwords = {hmac.new(key, password.encode(), "sha256").hexdigest(): password
                          for password in passwords}
        self.ids = {password: identifier for identifier, password in self.passwords.items()}
        self.hits = []

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def candidates(self, identity, parent):
        with self.lock, self.connect() as db:
            match = db.execute(
                """SELECT candidate FROM archives WHERE digest = ? AND candidate IS NOT NULL
                   ORDER BY (task = ? AND path = ? AND depth = ?) DESC, rowid DESC""",
                (identity[3], *identity[:3]),
            )
            previous = next((self.passwords[row[0]] for row in match if row[0] in self.passwords), None)
            return list(dict.fromkeys((previous, parent, *self.hits, None, *self.passwords.values())))

    def confirmed(self, identity, password):
        with self.lock, self.connect() as db:
            db.execute("INSERT OR REPLACE INTO archives VALUES (?, ?, ?, ?, ?)",
                       (*identity, self.ids.get(password)))
            if password is not None and password not in self.hits:
                self.hits.append(password)


def walk_tree(engine, source, kind, workspace, task, state, budget):
    pending = [(source, kind, Path(), 0, None)]
    media = {}
    step = 0
    while pending:
        archive, current_kind, relative, depth, parent = pending.pop()
        destination = workspace / str(step)
        step += 1
        member_path = relative.as_posix() if depth else task
        packed = archive.stat().st_size

        def progress(status, written=0, total=None):
            expanded = f"{written:,} B"
            if total is not None:
                expanded += f" / {total:,} B"
            print(f"{task}｜第 {step} 轮 · 层级 {depth}｜{member_path}｜"
                  f"包大小 {packed:,} B｜已解压 {expanded}｜{status}", flush=True)
        identity = (task, member_path, depth, file_digest(archive))
        checkpoint = workspace / f"{step - 1}.json"
        record = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else None
        temporary = destination.with_suffix(".partial")
        # 若写入检查点后进程退出，先恢复已确认的暂存层。
        if (record and record["identity"] == list(identity)
                and not destination.exists() and temporary.is_dir()
                and output_manifest(temporary) == record["files"]):
            temporary.rename(destination)
        if record and destination.is_dir() and record["identity"] == list(identity):
            if output_manifest(destination) != record["files"]:
                raise TaskError("中间结果与任务记录不符")
            candidate = state.passwords.get(record["candidate"])
            progress("复用已校验结果", extracted_bytes(destination))
        else:
            if temporary.exists():
                shutil.rmtree(temporary)
            if destination.exists():
                raise TaskError("中间工作目录与任务记录冲突")
            try:
                candidate = unpack(engine, archive, current_kind,
                                   state.candidates(identity, parent), temporary, budget, progress)
            except TaskError as error:
                progress(f"异常：{error}")
                raise
            state.confirmed(identity, candidate)
            save_json(checkpoint, {"identity": list(identity), "candidate": state.ids.get(candidate),
                                   "files": output_manifest(temporary)})
            temporary.rename(destination)
        children = []
        for item in sorted(destination.rglob("*")):
            if item.is_dir():
                continue
            target = relative / item.relative_to(destination)
            detected = archive_type(item)
            if detected:
                children.append((item, detected, target, depth + 1, candidate))
            else:
                if target in media:
                    raise TaskError("解包结果路径冲突")
                media[target] = item
        pending.extend(reversed(children))
    return media


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif",
                  ".heic", ".ico"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".m4v", ".mpeg",
                  ".mpg", ".ts", ".flv", ".3gp"}


def valid_media(path):
    from PIL import Image, UnidentifiedImageError

    suffix = path.suffix.lower()
    try:
        with Image.open(path) as image:
            image.load()
        return True
    except UnidentifiedImageError:
        if suffix in IMAGE_SUFFIXES:
            raise TaskError("图片校验失败") from None
    except (OSError, ValueError, Image.DecompressionBombError):
        raise TaskError("图片校验失败") from None

    with path.open("rb") as stream:
        header = stream.read(16)
    video_header = (
        header[4:8] == b"ftyp" or header.startswith((b"\x1a\x45\xdf\xa3", b"FLV", b"\0\0\x01\xba"))
        or (header.startswith(b"RIFF") and header[8:12] == b"AVI ")
    )
    if not video_header and suffix not in VIDEO_SUFFIXES:
        return False
    probe = shutil.which("ffprobe")
    if not probe:
        raise TaskError("需要安装 ffprobe 才能校验视频")
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-select_streams", "v", "-show_entries",
             "stream=codec_name", "-of", "json", str(path)],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
        )
        streams = json.loads(result.stdout).get("streams", []) if result.returncode == 0 else []
    except (subprocess.TimeoutExpired, ValueError):
        raise TaskError("视频校验失败") from None
    if not any(stream.get("codec_name") not in (None, "unknown") for stream in streams):
        raise TaskError("视频校验失败")
    return True


def output_manifest(directory):
    manifest = {}
    for item in sorted(directory.rglob("*")):
        mode = item.lstat().st_mode
        if stat.S_ISREG(mode):
            manifest[item.relative_to(directory).as_posix()] = file_digest(item)
        elif not stat.S_ISDIR(mode):
            raise TaskError("输出包含链接或特殊文件")
    return manifest


def save_json(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def process_task(engine, origin, source, kind, output, work, state, budget):
    relative = source.relative_to(origin)
    task = relative.as_posix()
    identifier = hashlib.sha256(os.fsencode(task)).hexdigest()
    workspace = work / identifier
    journal = work / f"{identifier}.json"
    final = output / relative
    source_digest = file_digest(source)
    if final.exists() or final.is_symlink():
        if final.is_symlink() or not final.is_dir() or not journal.is_file():
            raise TaskError("输出路径冲突")
        recorded = json.loads(journal.read_text(encoding="utf-8"))
        if recorded != {"source": source_digest, "files": output_manifest(final)}:
            raise TaskError("现有输出与任务记录不符")
        if workspace.exists():
            shutil.rmtree(workspace)
        return "已验证现有输出"

    with budget.reserve(0):
        workspace.mkdir(exist_ok=True)
    media = walk_tree(engine, source, kind, workspace, task, state, budget)
    staged = workspace / "publish"
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir()
    seen = set()
    for target, item in media.items():
        if not valid_media(item):
            continue
        normalized = unicodedata.normalize("NFC", target.as_posix()).casefold()
        if normalized in seen:
            raise TaskError("媒体路径冲突")
        seen.add(normalized)
        destination = staged / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 暂存区与输出同卷，硬链接避免再复制一份媒体。
        os.link(item, destination)
    if not seen:
        raise TaskError("归档未产出可校验的图片或视频")

    if file_digest(source) != source_digest:
        raise TaskError("运行期间源包发生变化")
    recorded = {"source": source_digest, "files": output_manifest(staged)}
    save_json(journal, recorded)
    final.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(final)
    shutil.rmtree(workspace)
    return "媒体已发布，中间归档已清理"


def run(origin, output, workers, passwords):
    import fcntl
    try:
        from PIL import Image
    except ImportError:
        raise ValueError("请先安装 Pillow") from None

    engine = shutil.which("7zz") or shutil.which("7z")
    if not engine:
        raise ValueError("找不到 7zz 或 7z")
    output.mkdir(parents=True, exist_ok=True)
    work = output / ".ziplet"
    work.mkdir(mode=0o700, exist_ok=True)
    if work.is_symlink() or work.stat().st_mode & 0o077:
        raise ValueError(".ziplet 工作目录须为仅当前用户可访问的实际目录")
    with (work / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("已有 Ziplet 进程使用此输出目录") from None
        state = State(work, passwords)
        budget = DiskBudget(work)

        def process(source):
            task = source.relative_to(origin).as_posix()
            try:
                kind = archive_type(source)
                if not kind:
                    print(f"{task}｜非目标文件", flush=True)
                    return True
                if source.relative_to(origin).parts[0].casefold() == work.name:
                    raise TaskError("归档路径与工作目录冲突")
                for parent in (output / source.relative_to(origin)).parents:
                    if parent == output:
                        break
                    if parent.is_symlink():
                        raise TaskError("输出父目录包含链接")
                result = process_task(engine, origin, source, kind, output, work, state, budget)
                print(f"{task}｜{result}", flush=True)
                return True
            except (TaskError, OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as error:
                if isinstance(error, TaskError):
                    reason = str(error)
                elif getattr(error, "errno", None) == errno.ENOSPC:
                    reason = "磁盘空间不足，任务暂停"
                else:
                    reason = f"任务执行失败（{type(error).__name__}）"
                print(f"{task}｜{reason}", flush=True)
                return False

        paths = (item for item in sorted(origin.rglob("*")) if not item.is_symlink() and item.is_file())
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(process, paths))
        return 0 if all(results) else 1

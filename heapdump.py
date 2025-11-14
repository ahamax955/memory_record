import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from util import mkdirs, runShell


def _should_connect(device_id: str) -> bool:
    """简单判断是否需要先执行 adb connect"""
    if not device_id:
        return False
    return any(ch in device_id for ch in ('.', ':'))


def memory_snapshot(device: str = "", package: str = "technology.cariad.smartsystemdrivecube.cn",
                    output_dir: str = "./tmp/heapdump", keep_remote: bool = False) -> Path:
    """
    创建指定设备和应用的 heapdump 快照，并将文件保存到本地 output_dir。
    返回生成文件的路径。
    """
    mkdirs(output_dir)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    device_suffix = device.replace(":", "_").replace(".", "_") if device else "local"
    filename = f"heapdump.{device_suffix}.{timestamp}.hprof"
    local_path = Path(output_dir) / filename
    device_flag = f"-s {device}" if device else ""

    if device and _should_connect(device):
        runShell(f"adb connect {device}")

    remote_path = f"/data/local/tmp/{filename}"
    runShell(f"adb {device_flag} shell am dumpheap {package} {remote_path}")
    runShell(f"adb {device_flag} pull {remote_path} {local_path.as_posix()}")

    if not keep_remote:
        runShell(f"adb {device_flag} shell rm -rf {remote_path}")

    return local_path


def _parse_args(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="Create heapdump snapshot via adb.")
    parser.add_argument("--device", help="ADB 设备序列号或 IP:PORT", default="")
    parser.add_argument("--package", help="需要 dump 的包名",
                        default="technology.cariad.smartsystemdrivecube.cn")
    parser.add_argument("--output", help="本地存储 heapdump 的目录", default="./tmp/heapdump")
    parser.add_argument("--keep-remote", action="store_true", help="保留设备上的临时 hprof 文件")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None):
    args = _parse_args(argv)
    path = memory_snapshot(device=args.device, package=args.package,
                           output_dir=args.output, keep_remote=args.keep_remote)
    print(f"Heapdump saved to {path}")


if __name__ == "__main__":
    main(sys.argv[1:])

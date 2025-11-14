import argparse
import subprocess
import threading
import time
import re
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import matplotlib.pyplot as plt


class MultiDeviceMemoryMonitor:
    def __init__(
        self,
        process_name: str,
        devices: Optional[List[str]] = None,
        interval: int = 5,
        threshold_mb: float = 250.0,
        history_points: int = 200,
        refresh_interval: float = 1.0,
        heapdump_script: str = "heapdump.py",
        heapdump_output: str = "./tmp/heapdump",
    ) -> None:
        self.process_name = process_name
        self.devices = devices or []
        self.interval = interval
        self.threshold_mb = threshold_mb
        self.refresh_interval = refresh_interval
        self.heapdump_script = heapdump_script
        self.heapdump_output = heapdump_output
        self.history_points = history_points

        self.is_monitoring = False
        self.device_data: Dict[str, Dict[str, Deque[float] | Deque[datetime] | str]] = defaultdict(
            lambda: {
                "timestamps": deque(maxlen=self.history_points),
                "values": deque(maxlen=self.history_points),
                "status": "idle",
            }
        )
        self.data_lock = threading.Lock()
        self.threshold_events: Deque[Dict[str, str]] = deque(maxlen=50)
        self.heapdump_cooldown = defaultdict(bool)
        self.start_time: Optional[datetime] = None

    @staticmethod
    def get_connected_devices() -> List[str]:
        result = subprocess.run("adb devices", shell=True, capture_output=True, text=True)
        devices: List[str] = []
        for line in result.stdout.strip().split("\n")[1:]:
            if line.strip() and "\tdevice" in line:
                devices.append(line.split("\t")[0])
        return devices

    def get_memory_info(self, device_id: str) -> Dict[str, str | float]:
        cmd = f"adb -s {device_id} shell dumpsys meminfo {self.process_name}"
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "value": 0.0}

        if result.returncode != 0:
            return {"status": result.stderr.strip() or "error", "value": 0.0}

        output = result.stdout
        total_match = re.search(r"TOTAL\s+([\d,]+)", output)
        pss_match = re.search(r"TOTAL PSS:\s+([\d,]+)", output)

        if total_match:
            value_kb = int(total_match.group(1).replace(",", ""))
        elif pss_match:
            value_kb = int(pss_match.group(1).replace(",", ""))
        else:
            return {"status": "process_not_found", "value": 0.0}

        return {"status": "success", "value": value_kb / 1024}

    def monitor_single_device(self, device_id: str) -> None:
        while self.is_monitoring:
            info = self.get_memory_info(device_id)
            timestamp = datetime.now()
            with self.data_lock:
                record = self.device_data[device_id]
                record["timestamps"].append(timestamp)
                record["values"].append(info["value"])
                record["status"] = info["status"]

            if info["status"] == "success":
                self.handle_threshold(device_id, info["value"], timestamp)

            time.sleep(self.interval)

    def handle_threshold(self, device_id: str, value_mb: float, timestamp: datetime) -> None:
        if value_mb >= self.threshold_mb and not self.heapdump_cooldown[device_id]:
            event = {
                "device": device_id,
                "time": timestamp.strftime("%H:%M:%S"),
                "value": f"{value_mb:.1f}",
            }
            self.threshold_events.append(event)
            self.heapdump_cooldown[device_id] = True
            threading.Thread(
                target=self.trigger_heapdump, args=(device_id,), daemon=True
            ).start()
        elif value_mb < self.threshold_mb * 0.9:
            self.heapdump_cooldown[device_id] = False

    def trigger_heapdump(self, device_id: str) -> None:
        cmd = [
            sys.executable,
            self.heapdump_script,
            "--device",
            device_id,
            "--package",
            self.process_name,
            "--output",
            self.heapdump_output,
        ]
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {device_id} > threshold, running heapdump.py")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Heapdump failed for {device_id}: {exc}")

    def list_heapdump_files(self, limit: int = 6) -> List[Tuple[str, str, str]]:
        heapdump_dir = Path(self.heapdump_output)
        if not heapdump_dir.exists():
            return []

        files = sorted(
            heapdump_dir.glob("*.hprof"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:limit]
        listed: List[Tuple[str, str, str]] = []
        for file in files:
            mtime = datetime.fromtimestamp(file.stat().st_mtime).strftime("%m-%d %H:%M")
            size_mb = file.stat().st_size / (1024 * 1024)
            listed.append((file.name, mtime, f"{size_mb:.1f} MB"))
        return listed

    def start_threads(self) -> List[threading.Thread]:
        threads = []
        self.is_monitoring = True
        for device_id in self.devices:
            thread = threading.Thread(target=self.monitor_single_device, args=(device_id,), daemon=True)
            thread.start()
            threads.append(thread)
        return threads

    def stop(self) -> None:
        self.is_monitoring = False

    def render(self) -> None:
        plt.ion()
        fig, (ax_chart, ax_info) = plt.subplots(
            2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [3, 1]}
        )
        colors = plt.cm.tab10.colors
        lines = {}

        for idx, device_id in enumerate(self.devices):
            (line,) = ax_chart.plot([], [], label=device_id, color=colors[idx % len(colors)])
            lines[device_id] = line

        ax_chart.set_ylabel("Memory (MB)")
        ax_chart.set_xlabel("Minutes")
        ax_chart.axhline(self.threshold_mb, color="red", linestyle="--", label="Threshold")
        ax_chart.set_title(f"Memory usage for {self.process_name}")
        ax_chart.legend(loc="upper left")

        while self.is_monitoring and plt.fignum_exists(fig.number):
            with self.data_lock:
                for device_id, record in self.device_data.items():
                    timestamps = list(record["timestamps"])
                    values = list(record["values"])
                    if not timestamps:
                        continue
                    if not self.start_time:
                        self.start_time = timestamps[0]
                    elapsed_minutes = [
                        (ts - self.start_time).total_seconds() / 60 for ts in timestamps
                    ]
                    lines[device_id].set_data(elapsed_minutes, values)

                ax_chart.relim()
                ax_chart.autoscale_view()

                info_text = self.compose_info_text()

            ax_info.clear()
            ax_info.axis("off")
            ax_info.text(0.01, 0.98, info_text, va="top", family="monospace")

            fig.canvas.draw()
            fig.canvas.flush_events()
            time.sleep(self.refresh_interval)

    def compose_info_text(self) -> str:
        files = self.list_heapdump_files()
        file_lines = ["最近的 heapdump 文件:"]
        if not files:
            file_lines.append("  (暂无)")
        else:
            for name, mtime, size in files:
                file_lines.append(f"  {mtime} | {size} | {name}")

        event_lines = ["最近触发阈值的记录 (> {0:.0f} MB):".format(self.threshold_mb)]
        if not self.threshold_events:
            event_lines.append("  (暂无)")
        else:
            for event in list(self.threshold_events)[-5:]:
                event_lines.append(
                    f"  {event['time']} | {event['device']} | {event['value']} MB"
                )

        return "\n".join(file_lines + [""] + event_lines)

    def run(self) -> None:
        if not self.devices:
            self.devices = self.get_connected_devices()

        if not self.devices:
            print("未检测到任何已连接的设备。")
            return

        print(f"开始监控 {len(self.devices)} 台设备：{', '.join(self.devices)}")
        threads = self.start_threads()
        try:
            self.render()
        except KeyboardInterrupt:
            print("\n停止监控...")
        finally:
            self.stop()
            for thread in threads:
                thread.join(timeout=1)


def parse_args(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="多设备 ADB 内存监控与 heapdump 触发工具")
    parser.add_argument("--process", default="technology.cariad.smartsystemdrivecube.cn", help="目标进程包名")
    parser.add_argument("--devices", nargs="*", help="指定需要监控的设备序列号，默认读取 adb devices")
    parser.add_argument("--interval", type=int, default=3, help="采样周期（秒）")
    parser.add_argument("--threshold", type=float, default=250.0, help="触发 heapdump 的阈值（MB）")
    parser.add_argument("--history", type=int, default=200, help="折线图保留的采样点数量")
    parser.add_argument("--refresh", type=float, default=1.0, help="图表刷新频率（秒）")
    parser.add_argument("--heapdump-script", default="heapdump.py", help="heapdump 脚本路径")
    parser.add_argument("--output", default="./tmp/heapdump", help="heapdump 文件输出目录")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    devices = None
    if args.devices:
        devices = [dev for item in args.devices for dev in item.split(",") if dev]

    monitor = MultiDeviceMemoryMonitor(
        process_name=args.process,
        devices=devices,
        interval=args.interval,
        threshold_mb=args.threshold,
        history_points=args.history,
        refresh_interval=args.refresh,
        heapdump_script=args.heapdump_script,
        heapdump_output=args.output,
    )
    monitor.run()


if __name__ == "__main__":
    main(sys.argv[1:])


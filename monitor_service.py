import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional


class MemoryMonitorService:
    """
    后台服务：持续监控多台设备上指定进程的内存使用情况，并在超过阈值时自动触发 heapdump。
    监控结果存入内存，可由 Web API 查询。
    """

    def __init__(
        self,
        process_name: str,
        devices: Optional[List[str]] = None,
        interval: int = 3,
        threshold_mb: float = 250.0,
        history_points: int = 15,
        heapdump_script: str = "heapdump.py",
        heapdump_output: str = "./tmp/heapdump",
    ) -> None:
        self.process_name = process_name
        self.devices = devices or []
        self.interval = interval
        self.threshold_mb = threshold_mb
        self.history_points = history_points
        self.heapdump_script = Path(heapdump_script)
        self.heapdump_output = Path(heapdump_output)

        self._monitor_threads: List[threading.Thread] = []
        self._is_running = False
        self._lock = threading.Lock()
        self._heapdump_cooldown = defaultdict(bool)
        self._start_time = datetime.utcnow()

        self._data: Dict[str, Dict[str, Deque]] = defaultdict(
            lambda: {
                "timestamps": deque(maxlen=self.history_points),
                "values": deque(maxlen=self.history_points),
                "status": "idle",
            }
        )
        self._threshold_events: Deque[Dict[str, str]] = deque(maxlen=50)

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def start(self) -> None:
        if self._is_running:
            return
        if not self.devices:
            self.devices = self.get_connected_devices()
        if not self.devices:
            print("MemoryMonitorService: 未检测到已连接设备，监控不会启动。")
            return

        self._is_running = True
        for device in self.devices:
            thread = threading.Thread(target=self._monitor_device, args=(device,), daemon=True)
            thread.start()
            self._monitor_threads.append(thread)
        print(
            f"MemoryMonitorService: 已启动，监控 {len(self.devices)} 台设备：{', '.join(self.devices)}"
        )

    def stop(self) -> None:
        self._is_running = False
        for thread in self._monitor_threads:
            thread.join(timeout=1)
        self._monitor_threads.clear()

    def get_status(self) -> Dict[str, object]:
        with self._lock:
            devices = []
            for device_id, payload in self._data.items():
                timestamps = list(payload["timestamps"])
                values = list(payload["values"])
                latest_value = values[-1] if values else 0.0
                last_updated = (
                    timestamps[-1].isoformat() if timestamps else None
                )
                devices.append(
                    {
                        "device": device_id,
                        "status": payload["status"],
                        "latest_mb": latest_value,
                        "last_updated": last_updated,
                        "history": [
                            {"time": ts.isoformat(), "value": val}
                            for ts, val in zip(timestamps, values)
                        ],
                    }
                )

            events = list(self._threshold_events)

        return {
            "process": self.process_name,
            "threshold": self.threshold_mb,
            "devices": devices,
            "events": events,
            "start_time": self._start_time.isoformat(),
            "devices_monitored": len(self.devices),
            "interval": self.interval,
        }

    def get_heapdumps(self, limit: int = 10) -> List[Dict[str, object]]:
        directory = self.heapdump_output
        if not directory.exists():
            return []
        files = sorted(directory.glob("*.hprof"), key=lambda f: f.stat().st_mtime, reverse=True)
        items: List[Dict[str, object]] = []
        for file in files[:limit]:
            stats = file.stat()
            items.append(
                {
                    "name": file.name,
                    "path": str(file.resolve()),
                    "modified": datetime.fromtimestamp(stats.st_mtime).isoformat(),
                    "size_mb": round(stats.st_size / (1024 * 1024), 2),
                }
            )
        return items

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    @staticmethod
    def get_connected_devices() -> List[str]:
        result = subprocess.run("adb devices", shell=True, capture_output=True, text=True)
        devices: List[str] = []
        for line in result.stdout.strip().split("\n")[1:]:
            if line.strip() and "\tdevice" in line:
                devices.append(line.split("\t")[0])
        return devices

    def _monitor_device(self, device_id: str) -> None:
        while self._is_running:
            info = self._collect_memory(device_id)
            timestamp = datetime.utcnow()
            with self._lock:
                bucket = self._data[device_id]
                bucket["timestamps"].append(timestamp)
                bucket["values"].append(info["value"])
                bucket["status"] = info["status"]
            if info["status"] == "success":
                self._process_threshold(device_id, info["value"], timestamp)
            time.sleep(self.interval)

    def _collect_memory(self, device_id: str) -> Dict[str, object]:
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

    def _process_threshold(self, device_id: str, value_mb: float, timestamp: datetime) -> None:
        if value_mb >= self.threshold_mb and not self._heapdump_cooldown[device_id]:
            event = {
                "device": device_id,
                "time": timestamp.isoformat(),
                "value_mb": round(value_mb, 1),
            }
            with self._lock:
                self._threshold_events.append(event)
            self._heapdump_cooldown[device_id] = True
            threading.Thread(target=self._trigger_heapdump, args=(device_id,), daemon=True).start()
        elif value_mb < self.threshold_mb * 0.9:
            self._heapdump_cooldown[device_id] = False

    def _trigger_heapdump(self, device_id: str) -> None:
        cmd = [
            sys.executable,
            str(self.heapdump_script),
            "--device",
            device_id,
            "--package",
            self.process_name,
            "--output",
            str(self.heapdump_output),
        ]
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {device_id} 内存 {self.threshold_mb}+ MB，触发 heapdump")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Heapdump failed for {device_id}: {exc}")


def build_service_from_env() -> MemoryMonitorService:
    """
    允许通过环境变量配置服务。便于容器或 CI 中部署。
    """
    process = os.environ.get("MONITOR_PROCESS", "technology.cariad.smartsystemdrivecube.cn")
    devices_env = os.environ.get("MONITOR_DEVICES", "")
    devices = [item.strip() for item in devices_env.split(",") if item.strip()] or None
    interval = int(os.environ.get("MONITOR_INTERVAL", "3"))
    threshold = float(os.environ.get("MONITOR_THRESHOLD_MB", "250"))
    history = int(os.environ.get("MONITOR_HISTORY_POINTS", "15"))
    heapdump_script = os.environ.get("HEAPDUMP_SCRIPT", "heapdump.py")
    heapdump_output = os.environ.get("HEAPDUMP_OUTPUT", "./tmp/heapdump")

    return MemoryMonitorService(
        process_name=process,
        devices=devices,
        interval=interval,
        threshold_mb=threshold,
        history_points=history,
        heapdump_script=heapdump_script,
        heapdump_output=heapdump_output,
    )


if __name__ == "__main__":
    service = build_service_from_env()
    service.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        service.stop()


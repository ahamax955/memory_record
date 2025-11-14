import os
import subprocess
from pathlib import Path
from typing import List

from flask import Flask, jsonify, send_from_directory, url_for

from monitor_service import build_service_from_env

DEFAULT_CONNECT = [
    "172.16.250.57",
    "172.16.250.5",
    "172.16.250.91",
    "172.16.250.23",
]

HEAPDUMP_DIR = Path("./tmp/heapdump").resolve()


def connect_devices(addresses: List[str]) -> None:
    for address in addresses:
        if not address:
            continue
        cmd = ["adb", "connect", address]
        print(f"Pre-connecting {address} ...")
        try:
            subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception as exc:  # noqa: BLE001
            print(f"adb connect {address} failed: {exc}")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="frontend", static_url_path="")

    connect_env = os.environ.get("MONITOR_CONNECT_LIST")
    addresses = (
        [item.strip() for item in connect_env.split(",") if item.strip()]
        if connect_env
        else DEFAULT_CONNECT
    )
    connect_devices(addresses)

    service = build_service_from_env()
    service.start()

    @app.route("/api/status")
    def api_status():
        return jsonify(service.get_status())

    @app.route("/api/heapdumps")
    def api_heapdumps():
        files = service.get_heapdumps()
        for item in files:
            item["url"] = url_for("download_heapdump", filename=item["name"])
        return jsonify({"files": files})

    @app.route("/heapdump/<path:filename>")
    def download_heapdump(filename: str):
        return send_from_directory(HEAPDUMP_DIR, filename, as_attachment=True)

    @app.route("/")
    def root():
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/<path:path>")
    def static_proxy(path: str):
        target = Path(app.static_folder) / path
        if target.exists():
            return send_from_directory(app.static_folder, path)
        return send_from_directory(app.static_folder, "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("MONITOR_HOST", "0.0.0.0")
    port = int(os.environ.get("MONITOR_PORT", "8000"))
    debug = os.environ.get("MONITOR_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)


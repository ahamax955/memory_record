import os
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from monitor_service import MemoryMonitorService, build_service_from_env


def create_app() -> Flask:
    app = Flask(__name__, static_folder="frontend", static_url_path="")

    service = build_service_from_env()
    service.start()

    @app.route("/api/status")
    def api_status():
        return jsonify(service.get_status())

    @app.route("/api/heapdumps")
    def api_heapdumps():
        return jsonify({"files": service.get_heapdumps()})

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


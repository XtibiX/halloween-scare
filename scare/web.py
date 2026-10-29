from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from .audio import SOUND_TYPES


def create_app(ctl):
    app = Flask(__name__, template_folder=str(ctl.root / "templates"))
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    app.json.sort_keys = False  # keep modes in config.toml order

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/stream.mjpg")
    def stream():
        if ctl.camera is None:
            return "Camera not available", 503

        def frames():
            while True:
                frame = ctl.camera.jpeg.wait()
                if frame is None:
                    continue
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: %d\r\n\r\n" % len(frame) + frame + b"\r\n")

        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/status")
    def status():
        return jsonify(ctl.status())

    @app.post("/api/arm")
    def arm():
        armed = bool((request.get_json(silent=True) or {}).get("armed"))
        ctl.update_settings({"armed": armed})
        return jsonify(ctl.status())

    @app.post("/api/test")
    def test():
        text = ((request.get_json(silent=True) or {}).get("text") or "").strip() or None
        return jsonify({"started": ctl.scare(text, reason="manual")})

    @app.post("/api/settings")
    def settings():
        try:
            ctl.update_settings(request.get_json(silent=True) or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(ctl.status())

    @app.get("/api/messages")
    def get_messages():
        return jsonify({"text": ctl.speaker.messages_file.read_text()})

    @app.post("/api/messages")
    def set_messages():
        text = (request.get_json(silent=True) or {}).get("text", "")
        ctl.speaker.messages_file.write_text(text.rstrip() + "\n")
        ctl.speaker.prerender()
        return jsonify({"count": len(ctl.speaker.messages())})

    def sound_dir(kind):
        if kind not in ("scare", "ambient"):
            raise ValueError("kind must be scare or ambient")
        return ctl.speaker.scare_dir if kind == "scare" else ctl.speaker.ambient_dir

    @app.post("/api/sounds/<kind>")
    def upload_sound(kind):
        folder = sound_dir(kind)
        folder.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in request.files.getlist("files"):
            name = secure_filename(f.filename or "")
            if Path(name).suffix.lower() not in SOUND_TYPES:
                continue
            f.save(folder / name)
            saved.append(name)
        return jsonify({"saved": saved})

    @app.delete("/api/sounds/<kind>/<name>")
    def delete_sound(kind, name):
        path = sound_dir(kind) / secure_filename(name)
        path.unlink(missing_ok=True)
        return jsonify({"deleted": path.name})

    @app.get("/sounds/<kind>/<name>")
    def preview_sound(kind, name):
        return send_from_directory(sound_dir(kind), name)

    @app.errorhandler(ValueError)
    def bad_request(e):
        return jsonify({"error": str(e)}), 400

    return app

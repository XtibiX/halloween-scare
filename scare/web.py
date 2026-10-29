from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from .audio import SOUND_TYPES


def create_app(ctl):
    app = Flask(__name__, template_folder=str(ctl.root / "templates"))
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    app.json.sort_keys = False  # keep modes in config.toml order
    speaker = ctl.speaker

    def body():
        return request.get_json(silent=True) or {}

    @app.get("/")
    def index():
        # Phones otherwise keep showing an old copy of the page after updates
        response = app.make_response(render_template("index.html"))
        response.headers["Cache-Control"] = "no-store"
        return response

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
        ctl.update_settings({"armed": bool(body().get("armed"))})
        return jsonify(ctl.status())

    @app.post("/api/test")
    def test():
        text = (body().get("text") or "").strip() or None
        return jsonify({"started": ctl.scare(text, reason="manual")})

    @app.post("/api/settings")
    def settings():
        ctl.update_settings(body())
        return jsonify(ctl.status())

    @app.post("/api/calibrate")
    def calibrate():
        result = ctl.calibrate(int(body().get("height_cm", 0)))
        return jsonify({**result, "status": ctl.status()})

    # Messages and sounds take the mode to edit (?mode=), which in Auto mode
    # is not the same as the mode that will play.

    @app.get("/api/messages")
    def get_messages():
        mode = speaker.check_mode(request.args.get("mode", speaker.mode))
        return jsonify({"text": speaker.messages_file(mode).read_text()})

    @app.post("/api/messages")
    def set_messages():
        mode = speaker.check_mode(request.args.get("mode", speaker.mode))
        speaker.messages_file(mode).write_text(body().get("text", "").rstrip() + "\n")
        speaker.prerender()
        return jsonify({"count": len(speaker.messages(mode))})

    @app.post("/api/sounds/<mode>/<kind>")
    def upload_sound(mode, kind):
        folder = speaker.sound_dir(kind, speaker.check_mode(mode))
        folder.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in request.files.getlist("files"):
            name = secure_filename(f.filename or "")
            if Path(name).suffix.lower() not in SOUND_TYPES:
                continue
            f.save(folder / name)
            saved.append(name)
        return jsonify({"saved": saved})

    @app.delete("/api/sounds/<mode>/<kind>/<name>")
    def delete_sound(mode, kind, name):
        path = speaker.sound_dir(kind, speaker.check_mode(mode)) / secure_filename(name)
        path.unlink(missing_ok=True)
        return jsonify({"deleted": path.name})

    @app.get("/sounds/<mode>/<kind>/<name>")
    def preview_sound(mode, kind, name):
        return send_from_directory(speaker.sound_dir(kind, speaker.check_mode(mode)), name)

    @app.after_request
    def log_actions(response):
        # Status polling and the video are GETs; log only button presses
        if request.method != "GET":
            app.logger.info("%s %s %s -> %s", request.remote_addr, request.method,
                            request.full_path.rstrip("?"), response.status_code)
        return response

    @app.errorhandler(ValueError)
    def bad_request(e):
        return jsonify({"error": str(e)}), 400

    return app

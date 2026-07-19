import re
import secrets
import time
import threading

from flask import Flask, jsonify, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

rooms: dict[str, dict] = {}
rooms_lock = threading.Lock()


def _new_room_id() -> str:
    # token_urlsafe uses base64url which includes - and _; filter to alphanumeric only.
    # 16 bytes gives ~21 base64 chars, plenty after filtering to always get 6 alphanumeric.
    raw = secrets.token_urlsafe(16)
    return re.sub(r'[^A-Z0-9]', '', raw.upper())[:6]


def _cleanup_rooms():
    now = time.time()
    with rooms_lock:
        expired = [k for k, v in rooms.items() if now - v["created_at"] > 21600]
        for k in expired:
            del rooms[k]


def _get_room(room_id: str) -> dict | None:
    return rooms.get(room_id)


def _join_url(room_id: str) -> str:
    scheme = "https" if (request.is_secure or request.headers.get("X-Forwarded-Proto") == "https") else "http"
    return f"{scheme}://{request.host}/join/{room_id}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/new", methods=["POST"])
def new_room():
    _cleanup_rooms()
    room_id = _new_room_id()
    with rooms_lock:
        rooms[room_id] = {"words": [], "created_at": time.time(), "open": True}
    return redirect(url_for("host", room_id=room_id))


@app.route("/host/<room_id>")
def host(room_id):
    room = _get_room(room_id)
    if room is None:
        return render_template("index.html", error="Room not found or expired."), 404
    return render_template("host.html", room_id=room_id, join_url=_join_url(room_id))


@app.route("/join/<room_id>")
def join(room_id):
    room = _get_room(room_id)
    if room is None:
        return render_template("participant.html", room_id=None,
                               error="This room doesn't exist or has expired.")
    if not room["open"]:
        return render_template("participant.html", room_id=None,
                               error="This room is closed — the host has already shuffled the words.")
    return render_template("participant.html", room_id=room_id, error=None)


@app.route("/join/<room_id>/submit", methods=["POST"])
def submit_word(room_id):
    room = _get_room(room_id)
    if room is None:
        return jsonify({"error": "Room not found or has expired."}), 404
    if not room["open"]:
        return jsonify({"error": "This room is closed — the host has already shuffled."}), 403

    data = request.get_json(silent=True) or {}
    word = (data.get("word") or request.form.get("word") or "").strip()

    if not word:
        return jsonify({"error": "Please enter a word."}), 400
    if len(word) > 60:
        return jsonify({"error": "Word is too long (max 60 characters)."}), 400

    words = room["words"]
    if word.lower() in [w.lower() for w in words]:
        return jsonify({"error": "That word was already entered — try a different one."}), 409

    # list.append() is atomic under CPython's GIL; no lock needed here.
    words.append(word)
    return jsonify({"ok": True, "count": len(words)})


@app.route("/host/<room_id>/words")
def room_words(room_id):
    room = _get_room(room_id)
    if room is None:
        return jsonify({"error": "Room not found"}), 404
    # Never return actual words during collection — only count and open status.
    return jsonify({"count": len(room["words"]), "open": room["open"]})


@app.route("/host/<room_id>/lock", methods=["POST"])
def lock_room(room_id):
    room = _get_room(room_id)
    if room is None:
        return jsonify({"error": "Room not found"}), 404
    room["open"] = False
    return jsonify({"ok": True, "words": room["words"]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)

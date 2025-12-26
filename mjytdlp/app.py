from __future__ import annotations

import os
import secrets

from flask import Flask, jsonify, request

from .admin import admin_bp
from .mcp import mcp_bp


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    secret = os.getenv("MJYTDLP_SECRET_KEY")
    app.secret_key = secret.strip() if isinstance(secret, str) and secret.strip() else secrets.token_hex(32)

    if (os.getenv("MJYTDLP_ADMIN_PASSWORD") or "").strip() and not (
        (os.getenv("MJYTDLP_DISABLE_ADMIN") or "").strip().lower() in ("1", "true", "yes", "on")
    ):
        app.register_blueprint(admin_bp)

    app.register_blueprint(mcp_bp)

    @app.before_request
    def _handle_options():
        if request.method == "OPTIONS":
            return "", 204
        return None

    @app.get("/")
    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.after_request
    def _cors(resp):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, Authorization")
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        return resp

    return app

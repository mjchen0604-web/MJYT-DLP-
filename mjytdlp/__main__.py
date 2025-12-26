import os

from .app import create_app


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    app = create_app()
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()

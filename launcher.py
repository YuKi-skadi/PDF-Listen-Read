import os
import socket
import threading
import time
import webbrowser

import uvicorn

from app.server import app


HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    thread = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": app, "host": HOST, "port": PORT, "log_level": "info"},
        daemon=True,
    )
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                if os.environ.get("PDF_LISTEN_NO_BROWSER") != "1":
                    webbrowser.open(f"http://{HOST}:{PORT}")
                print(f"论文库已启动：http://{HOST}:{PORT}")
                break
        except OSError:
            time.sleep(0.2)
    else:
        print("论文服务启动失败，请查看上方日志。")

    try:
        while thread.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        print("正在退出...")


if __name__ == "__main__":
    main()

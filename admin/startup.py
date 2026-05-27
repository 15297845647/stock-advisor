"""在后台线程启动 admin web 服务，不阻塞 ACP Agent 主循环"""

import logging
import socket
import threading

import uvicorn

from agent.config import ADMIN_PORT

logger = logging.getLogger(__name__)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def start_admin_server():
    if not _port_available(ADMIN_PORT):
        logger.info("Admin port %d already in use, skipping (another agent owns it)", ADMIN_PORT)
        return

    def _run():
        uvicorn.run(
            "admin.server:app",
            host="0.0.0.0",
            port=ADMIN_PORT,
            log_level="warning",
        )

    t = threading.Thread(target=_run, daemon=True, name="admin-server")
    t.start()
    logger.info("Admin server started on http://0.0.0.0:%d", ADMIN_PORT)

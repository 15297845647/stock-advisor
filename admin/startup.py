"""在后台线程启动 admin web 服务，不阻塞 ACP Agent 主循环"""

import logging
import threading

import uvicorn

from agent.config import ADMIN_PORT

logger = logging.getLogger(__name__)


def start_admin_server():
    """非阻塞启动 — 在 daemon 线程中跑 uvicorn"""

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

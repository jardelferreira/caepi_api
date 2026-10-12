"""Processo leve que alimenta gradualmente campanhas explícitas do CAEPI.

É um serviço separado dos workers Selenium para que o dispatcher nunca fique preso
atrás de consultas longas nem gere milhares de tarefas de controle na fila Celery.
"""
from __future__ import annotations

import logging
import os
import signal
import time

from app.services.campaigns import dispatch_campaign_work

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("caepi.campaign_dispatcher")
_running = True


def _stop(_signum, _frame):
    global _running
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def main() -> None:
    logger.info("campaign_dispatcher_started", extra={"event": "campaign_dispatcher_started"})
    while _running:
        try:
            result = dispatch_campaign_work(max_dispatch_per_tick=20)
            if result.get("dispatched"):
                logger.info(
                    "campaign_tasks_dispatched",
                    extra={"event": "campaign_tasks_dispatched", **result},
                )
        except Exception:
            # Uma falha temporária no dispatcher não deve derrubar o serviço nem cancelar a campanha.
            logger.exception("campaign_dispatcher_iteration_failed")
        for _ in range(20):
            if not _running:
                break
            time.sleep(0.1)
    logger.info("campaign_dispatcher_stopped", extra={"event": "campaign_dispatcher_stopped"})


if __name__ == "__main__":
    main()

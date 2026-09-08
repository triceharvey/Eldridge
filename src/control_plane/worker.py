from __future__ import annotations

import logging
import signal
import time

from control_plane.runtime import build_runtime

LOGGER = logging.getLogger("control_plane.worker")


class Worker:
    def __init__(self) -> None:
        runtime = build_runtime(
            create_schema=False,
            activate_github_app_client=False,
            activate_oidc_client=False,
        )
        self.service = runtime.service
        self.poll_seconds = runtime.settings.worker_poll_seconds
        self.running = True

    def stop(self, *_args: object) -> None:
        self.running = False

    def run_once(self) -> bool:
        self.service.reclaim_expired_tasks(worker_id="orchestrator")
        task = self.service.lease_next_task(worker_id="orchestrator")
        if task is None:
            return False
        self.service.execute_leased_task(
            task_id=task["id"],
            lease_token=task["lease_token"],
            worker_id="orchestrator",
        )
        return True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        LOGGER.info("worker started")
        while self.running:
            if not self.run_once():
                time.sleep(self.poll_seconds)
        LOGGER.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    Worker().run_forever()


if __name__ == "__main__":
    main()

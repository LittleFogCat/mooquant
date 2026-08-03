# mookquant * Async training pipeline

import threading
import time


class TrainPipeline:
    # Async training: start_train returns task_id immediately.
    # Background thread executes training. Query status via get_status.

    _MAX_TASKS = 50  # M5: max retained completed tasks

    def __init__(self):
        self._tasks = {}
        self._lock = threading.Lock()

    def _cleanup_tasks(self):
        """M5: clean up old completed tasks to prevent unbounded growth."""
        if len(self._tasks) <= self._MAX_TASKS:
            return
        done = [(tid, t) for tid, t in self._tasks.items()
                if t.get('status') in ('done', 'error')]
        excess = len(self._tasks) - self._MAX_TASKS
        for tid, _ in done[:excess]:
            self._tasks.pop(tid, None)

    def start_train(self, config):
        # Start training in background. Returns task_id.
        task_id = 'train_' + str(int(time.time() * 1000))
        with self._lock:
            self._tasks[task_id] = {'status': 'running', 'progress': 0}
            self._cleanup_tasks()
        thread = threading.Thread(target=self._run, args=(task_id, config), daemon=True)
        thread.start()
        return task_id

    def _run(self, task_id, config):
        try:
            from training.trainer import Trainer
            trainer = Trainer()
            result = trainer.train(config)
            with self._lock:
                self._tasks[task_id] = {
                    'status': 'done',
                    'progress': 100,
                    'result': result,
                }
        except Exception as e:
            import traceback
            with self._lock:
                self._tasks[task_id] = {
                    'status': 'error',
                    'progress': 0,
                    'error': str(e),
                    'traceback': traceback.format_exc(),
                }

    def get_status(self, task_id):
        with self._lock:
            return self._tasks.get(task_id, {'status': 'not_found'})

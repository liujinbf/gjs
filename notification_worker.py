"""
notification_worker.py — 异步推送后台线程。

设计目标
--------
将 notification.py 中所有阻塞性网络 I/O（send_dingtalk / send_pushplus）
从调用方线程（MonitorWorker / AiBriefWorker）中彻底剥离，交由这里的后台
守护线程负责执行，使 MT5 行情刷新、模拟盘结算等关键路径实现 0 毫秒延迟。

用法
----
    from notification_worker import get_notification_worker

    worker = get_notification_worker()
    worker.start()           # 程序启动时调用一次
    worker.enqueue(task)     # 替代直接调用 send_dingtalk / send_pushplus
    worker.stop(timeout=5)   # 程序退出时调用

任务格式（task: dict）
----------------------
必须包含以下字段：
    {
        "send_fn":   callable,  # 如 notification.send_dingtalk
        "args":      tuple,     # 位置参数
        "on_result": callable | None,  # 接收 (ok: bool, detail: str) 的回调，可省略
    }

线程安全
--------
- 内部使用 queue.Queue（线程安全），外部无需加锁。
- on_result 回调从后台线程触发，若需更新 Qt UI 请使用信号中转。

节流策略
--------
- 每次 HTTP 请求成功后等待 INTER_SEND_DELAY_SEC 秒，防止 API 429。
- 请求失败时不等待（快速失败，错误已由 on_result 回调处理）。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# 成功推送后的错峰等待（原 time.sleep(0.35) 迁移到这里）
INTER_SEND_DELAY_SEC: float = 0.35

# 后台线程在 queue 为空时的轮询等待，控制 CPU 占用
_QUEUE_TIMEOUT_SEC: float = 1.0

# 队列最大容量：防止推送积压撑爆内存（超出后旧任务被丢弃并报警）
_MAX_QUEUE_SIZE: int = 256


class NotificationWorker(threading.Thread):
    """后台推送守护线程。

    - 作为 daemon 线程运行，主线程退出时自动销毁。
    - 通过 enqueue() 接收任务，保证调用方不阻塞。
    """

    def __init__(self) -> None:
        super().__init__(name="NotificationWorker", daemon=True)
        self._queue: queue.Queue[dict | None] = queue.Queue(maxsize=_MAX_QUEUE_SIZE)
        self._stop_event = threading.Event()

    # ── 公开接口 ──────────────────────────────────────────────────────────

    def enqueue(self, task: dict) -> bool:
        """将推送任务放入队列。

        Args:
            task: 包含 send_fn / args / on_result 的字典（见模块文档）。

        Returns:
            True  — 成功入队
            False — 队列已满，任务被丢弃（会写 WARNING 日志）
        """
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            send_fn = task.get("send_fn")
            fn_name = getattr(send_fn, "__name__", str(send_fn)) if send_fn else "?"
            logger.warning(
                "推送队列已满（容量 %d），丢弃任务 fn=%s args=%s",
                _MAX_QUEUE_SIZE,
                fn_name,
                task.get("args", ()),
            )
            return False

    def stop(self, timeout: float = 5.0) -> None:
        """优雅停机：等待现有队列任务全部处理完成，最多等待 timeout 秒。"""
        self._stop_event.set()
        self.join(timeout=timeout)
        if self.is_alive():
            logger.warning("NotificationWorker 在 %.1f 秒内未能正常退出", timeout)

    # ── 内部实现 ──────────────────────────────────────────────────────────

    def run(self) -> None:
        logger.info("🚀 NotificationWorker 已启动（后台推送队列就绪）")
        while True:
            # 如果收到停机信号，先将队列里剩余任务处理完再退出
            try:
                task = self._queue.get(timeout=_QUEUE_TIMEOUT_SEC)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue

            # 哨兵字山值 → 退出
            if task is None:
                logger.debug("NotificationWorker 收到停机哨兵，退出循环")
                break

            self._execute_task(task)

            # 处理完这个任务后再检查是否退出（队列空且已收到停机信号）
            if self._stop_event.is_set() and self._queue.empty():
                break

        logger.info("🛑 NotificationWorker 已退出")

    def _execute_task(self, task: dict) -> None:
        send_fn: Callable | None = task.get("send_fn")
        args: tuple = task.get("args", ())
        on_result: Callable | None = task.get("on_result")

        if send_fn is None:
            logger.warning("NotificationWorker 收到无效任务（send_fn 为空），已跳过")
            return

        fn_name = getattr(send_fn, "__name__", str(send_fn))
        try:
            ok, detail = send_fn(*args)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"执行异常: {exc}"
            logger.error("NotificationWorker 执行 %s 时发生异常: %s", fn_name, exc, exc_info=True)

        # 成功后错峰等待，防止 API 429
        if ok:
            time.sleep(INTER_SEND_DELAY_SEC)

        if on_result is not None:
            try:
                on_result(ok, detail)
            except Exception as exc:  # noqa: BLE001
                logger.error("NotificationWorker on_result 回调异常: %s", exc, exc_info=True)


# ── 全局单例 ──────────────────────────────────────────────────────────────

_worker_lock = threading.Lock()
_global_worker: NotificationWorker | None = None


def get_notification_worker() -> NotificationWorker:
    """获取全局 NotificationWorker 单例（懒加载，线程安全，自动启动）。

    首次调用时创建并启动 worker；此后始终返回同一个实例。
    若 worker 由于异常意外死亡，则自动重建并重启（防御性设计）。
    """
    global _global_worker
    with _worker_lock:
        if _global_worker is None or not _global_worker.is_alive():
            _global_worker = NotificationWorker()
            _global_worker.start()
            logger.info("✅ NotificationWorker 单例已创建并启动")
        return _global_worker


def ensure_worker_started() -> NotificationWorker:
    """确保全局 Worker 已启动并返回实例。在 main.py 中调用一次即可（幂等）。"""
    return get_notification_worker()

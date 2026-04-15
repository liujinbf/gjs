"""
test_notification_worker.py — NotificationWorker 后台推送线程的测试。
"""
import queue
import threading
import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from notification_worker import NotificationWorker, get_notification_worker, INTER_SEND_DELAY_SEC


# ── 辅助函数 ────────────────────────────────────────────────────────────────

def _make_worker() -> NotificationWorker:
    """每个测试用自己的 worker 实例，避免共享状态。"""
    w = NotificationWorker()
    w.start()
    return w


# ── 测试：基础投递 ───────────────────────────────────────────────────────────

def test_worker_executes_task_and_calls_on_result():
    """任务被后台线程执行，on_result 回调被调用，结果正确传递。"""
    results = []
    done_event = threading.Event()

    def fake_send(arg1, arg2):
        return True, "ok"

    def on_result(ok, detail):
        results.append((ok, detail))
        done_event.set()

    w = _make_worker()
    try:
        w.enqueue({"send_fn": fake_send, "args": ("a", "b"), "on_result": on_result})
        assert done_event.wait(timeout=3), "on_result 超时未被调用"
        assert results == [(True, "ok")]
    finally:
        w.stop(timeout=2)


def test_worker_handles_failed_send():
    """send_fn 返回 (False, ...) 时，on_result 接收到 ok=False。"""
    results = []
    done_event = threading.Event()

    def fake_send_fail(entry, token):
        return False, "connection refused"

    def on_result(ok, detail):
        results.append((ok, detail))
        done_event.set()

    w = _make_worker()
    try:
        w.enqueue({"send_fn": fake_send_fail, "args": ({}, "tok"), "on_result": on_result})
        assert done_event.wait(timeout=3)
        assert results[0][0] is False
        assert "connection refused" in results[0][1]
    finally:
        w.stop(timeout=2)


def test_worker_handles_exception_in_send_fn():
    """send_fn 抛出异常时，on_result 收到 ok=False，worker 不崩溃。"""
    results = []
    done_event = threading.Event()

    def boom(*_args):
        raise RuntimeError("network error")

    def on_result(ok, detail):
        results.append((ok, detail))
        done_event.set()

    w = _make_worker()
    try:
        w.enqueue({"send_fn": boom, "args": (), "on_result": on_result})
        assert done_event.wait(timeout=3)
        assert results[0][0] is False
        assert "network error" in results[0][1]
        # worker 应当仍然存活
        assert w.is_alive()
    finally:
        w.stop(timeout=2)


def test_worker_processes_multiple_tasks_in_order():
    """多个任务按入队顺序依次执行。"""
    order = []
    done_event = threading.Event()
    total = 3

    def make_fn(idx):
        def fn(*_):
            order.append(idx)
            return True, "ok"
        return fn

    def on_result(ok, detail):
        if len(order) == total:
            done_event.set()

    w = _make_worker()
    try:
        for i in range(total):
            w.enqueue({"send_fn": make_fn(i), "args": (), "on_result": on_result})
        assert done_event.wait(timeout=5)
        assert order == list(range(total))
    finally:
        w.stop(timeout=2)


def test_worker_skips_task_with_none_send_fn():
    """send_fn=None 的任务被跳过，不抛异常，后续任务正常处理。"""
    results = []
    done_event = threading.Event()

    def fake_send(*_):
        return True, "ok"

    def on_result(ok, detail):
        results.append(ok)
        done_event.set()

    w = _make_worker()
    try:
        # 先投一个无效任务
        w.enqueue({"send_fn": None, "args": (), "on_result": None})
        # 再投一个正常任务
        w.enqueue({"send_fn": fake_send, "args": (), "on_result": on_result})
        assert done_event.wait(timeout=3)
        assert results == [True]
    finally:
        w.stop(timeout=2)


def test_worker_stop_drains_queue():
    """stop() 后，已入队任务会全部执行完毕才退出（等待 timeout 内完成）。"""
    done_count = [0]
    lock = threading.Lock()
    all_done = threading.Event()
    total = 5

    def counting_fn(*_):
        time.sleep(0.05)
        with lock:
            done_count[0] += 1
            if done_count[0] == total:
                all_done.set()
        return True, "ok"

    w = _make_worker()
    for _ in range(total):
        w.enqueue({"send_fn": counting_fn, "args": (), "on_result": None})
    # 等待所有任务添加完毕后再调用 stop，保证 stop 正确语义
    assert all_done.wait(timeout=5), f"任务未全部完成，done={done_count[0]}"
    w.stop(timeout=3)
    assert done_count[0] == total


def test_enqueue_returns_false_when_queue_full():
    """队列满时，enqueue 返回 False，不抛异常。"""
    # 构造一个容量为 1 的 worker（不启动，防止自动消费）
    w = NotificationWorker()
    # 手动设置一个极小的队列来模拟满状态
    w._queue = queue.Queue(maxsize=1)

    def slow_fn(*_):
        time.sleep(5)
        return True, "ok"

    # 填满队列（不启动 worker，不被消费）
    result1 = w.enqueue({"send_fn": slow_fn, "args": (), "on_result": None})
    # 再次入队应失败
    result2 = w.enqueue({"send_fn": slow_fn, "args": (), "on_result": None})

    assert result1 is True
    assert result2 is False


def test_get_notification_worker_returns_singleton():
    """get_notification_worker() 返回全局单例，多次调用是同一个对象。"""
    w1 = get_notification_worker()
    w2 = get_notification_worker()
    assert w1 is w2


def test_worker_inter_send_delay_only_on_success():
    """成功发送后才有 INTER_SEND_DELAY_SEC 等待，失败时不等待。"""
    timestamps = []

    def ok_fn(*_):
        timestamps.append(time.monotonic())
        return True, "ok"

    def fail_fn(*_):
        timestamps.append(time.monotonic())
        return False, "err"

    done_event = threading.Event()

    def on_r(ok, detail):
        if len(timestamps) == 2:
            done_event.set()

    w = _make_worker()
    try:
        t0 = time.monotonic()
        w.enqueue({"send_fn": ok_fn, "args": (), "on_result": on_r})
        w.enqueue({"send_fn": fail_fn, "args": (), "on_result": on_r})
        assert done_event.wait(timeout=5)
        # 第一个任务成功后，第二个开始的时间差 >= INTER_SEND_DELAY_SEC
        gap = timestamps[1] - timestamps[0]
        assert gap >= INTER_SEND_DELAY_SEC * 0.8, f"等待时间不足: {gap:.3f}s"
    finally:
        w.stop(timeout=2)


# ── 测试：异步推送不阻塞调用方 ────────────────────────────────────────────────

def test_send_notifications_does_not_block_caller(monkeypatch):
    """send_notifications 调用后立即返回，不等待 HTTP 完成。"""
    import shutil
    import notification
    from app_config import MetalMonitorConfig

    state_dir = ROOT / ".runtime_test_async_notify"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    send_delay = 1.0  # 模拟 1 秒网络延迟

    def slow_ding(entry, webhook):
        time.sleep(send_delay)
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", slow_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda e, t: (False, "skip"))

    config = MetalMonitorConfig(
        symbols=["XAUUSD"],
        refresh_interval_sec=30,
        event_risk_mode="normal",
        mt5_path="",
        mt5_login="",
        mt5_password="",
        mt5_server="",
        dingtalk_webhook="https://example.com/hook",
        pushplus_token="",
        notify_cooldown_min=30,
        ai_api_key="k",
        ai_api_base="https://api.example.com",
        ai_model="m",
        ai_push_enabled=False,
        ai_push_summary_only=True,
    )
    # 关闭 DND 和夜间点差防护，避免测试时段干扰
    config.notify_dnd_enabled = False
    config.overnight_spread_guard_enabled = False

    entries = [
        {
            "occurred_at": "2026-04-16 10:00:00",  # 白天时段，明确避开 DND 窗口
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "异步测试。",
            "tone": "warning",
            "signature": "async-test-1",
            "event_importance_text": "高影响",
        }
    ]

    t0 = time.monotonic()
    result = notification.send_notifications(entries, config, state_file=state_file)
    elapsed = time.monotonic() - t0

    # 调用应在远小于 send_delay 的时间内返回
    assert elapsed < send_delay * 0.5, f"send_notifications 阻塞了 {elapsed:.3f}s，未实现异步"
    assert result["sent_count"] == 1

    shutil.rmtree(state_dir)

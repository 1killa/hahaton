"""A bounded bridge that abandons expired callbacks before they touch Qt."""
import threading
import time


class MainThreadBridge:
    def __init__(self, schedule, timeout=20.0, owner_thread=None):
        self.schedule = schedule
        self.timeout = timeout
        self.owner_thread = owner_thread

    def invoke(self, function, cancel=None):
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Операция отменена.")
        if self.owner_thread is not None and threading.current_thread().ident == self.owner_thread:
            return function()
        done = threading.Event()
        guard = threading.Lock()
        state = {"expired": False}
        deadline = time.monotonic() + self.timeout

        def callback():
            with guard:
                if state["expired"] or time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                    done.set()
                    return
                state["started"] = True
            try:
                state["result"] = function()
            except Exception as error:
                state["error"] = error
            finally:
                done.set()

        self.schedule(callback)
        while not done.wait(0.025):
            if time.monotonic() >= deadline or (cancel is not None and cancel.is_set()):
                with guard:
                    state["expired"] = True
                raise RuntimeError("Операция отменена или главный поток STE не ответил вовремя.")
        if "error" in state:
            raise state["error"]
        if "result" not in state:
            raise RuntimeError("Отложенная операция отменена до выполнения.")
        return state["result"]

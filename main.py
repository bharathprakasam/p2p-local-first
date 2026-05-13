"""
PhantomLink v2 - main.py
Entry point: runs asyncio loop in background thread, Qt owns main thread.
"""
import sys, os, asyncio, threading, signal
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
from ui.main_window import MainWindow
from database.db_manager import DatabaseManager
from networking.node import P2PNode
from crypto.identity import DeviceIdentity


def run_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PhantomLink")
    app.setStyle("Fusion")

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=run_loop, args=(loop,), daemon=True)
    t.start()

    db       = DatabaseManager()
    identity = DeviceIdentity(db)
    node     = P2PNode(loop, db, identity)

    window = MainWindow(app, loop, node, db, identity)
    window.show()

    asyncio.run_coroutine_threadsafe(node.start(), loop)
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    # Keep Qt+asyncio alive together
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(50)

    code = app.exec()
    asyncio.run_coroutine_threadsafe(node.stop(), loop).result(timeout=3)
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=3)
    sys.exit(code)


if __name__ == "__main__":
    main()

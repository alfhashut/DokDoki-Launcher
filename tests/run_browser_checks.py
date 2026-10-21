"""Run the mocked suite with the project's existing PySide6 dependency."""
import json
import os
import sys
import threading
from serve import make_server

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineUrlRequestInterceptor
from PySide6.QtWidgets import QApplication


class LocalOnly(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() in ('http', 'https') and info.requestUrl().host() != '127.0.0.1':
            info.block(True)


app = QApplication([])
server = make_server()
threading.Thread(target=server.serve_forever, daemon=True).start()
page = QWebEnginePage()
interceptor = LocalOnly(page)
page.profile().setUrlRequestInterceptor(interceptor)
page.load(QUrl(f'http://127.0.0.1:{server.server_port}/tests/'))
result = None
for _ in range(1200):
    loop = QEventLoop()
    values = []
    page.runJavaScript('JSON.stringify(window.DOKDOKI_TEST_RESULT || null)', lambda value: (values.append(value), loop.quit()))
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    if values and values[0]:
        result = json.loads(values[0])
        if result and result.get('done'):
            break
    pause = QEventLoop()
    QTimer.singleShot(50, pause.quit)
    pause.exec()
server.shutdown()
server.server_close()
if not result or not result.get('done'):
    print('FAIL: browser suite did not finish', flush=True)
    sys.exit(1)
print(json.dumps(result, indent=2), flush=True)
page.deleteLater()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
sys.exit(1 if result['failed'] else 0)

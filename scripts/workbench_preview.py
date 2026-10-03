"""Isolated UI preview/test server. No model calls, recording or desktop input.

The normal desktop launcher uses the real controller. This intentionally refuses
ALL writes and uses an empty temporary data directory; never point it at user data.
"""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from trace2task.web_console import create_web_server

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8775)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='trace2task-workbench-') as folder:
        server = create_web_server(Path(folder), port=args.port)
        server.RequestHandlerClass.controller._rsi_client = None
        server.RequestHandlerClass.controller._rsi_profile_error = '隔离 UI 验证不连接远程 RSI'
        def read_only(self):
            self._error(403, '隔离 UI 验证实例：所有写入与执行均禁用')
        server.RequestHandlerClass.do_POST = read_only
        print(f'READ-ONLY UI PREVIEW http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()

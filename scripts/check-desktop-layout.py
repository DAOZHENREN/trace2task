"""Read-only WebView2 layout smoke check; never starts an agent or sends input."""
import json
import tempfile
import threading
from pathlib import Path

import webview

from trace2task.web_console import create_web_server


def main():
    with tempfile.TemporaryDirectory(prefix="t2-layout-") as root:
        server = create_web_server(Path(root), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        window = webview.create_window("Trace2Task layout check", f"http://127.0.0.1:{server.server_port}",
                                       width=1440, height=1000, hidden=True)
        results = []

        def check():
            try:
                data = window.evaluate_js("""(() => {
                  const q = s => document.querySelector(s);
                  const advanced = q('#execution-advanced');
                  const top = q('#execute-panel').getBoundingClientRect();
                  const activity = q('.activity-card').getBoundingClientRect();
                  q('#execution-scope').value = 'desktop';
                  q('#execution-scope').dispatchEvent(new Event('change'));
                  const hidden = getComputedStyle(q('#input-mode-field')).display === 'none';
                  savedAPISettings = null; // Simulate first use; never writes stored settings.
                  q('#model-provider').value = 'api';
                  q('#model-provider').dispatchEvent(new Event('change'));
                  const api = !q('#api-model-primary').hidden && advanced.open;
                  q('#model-provider').value = 'local';
                  q('#local-model').value = 'trained_d';
                  q('#model-provider').dispatchEvent(new Event('change'));
                  const guiModels = ['qwen3-vl-2b', 'gui-owl-2b', 'mai-ui-2b'].every(model => {
                    q('#local-model').value = model;
                    q('#local-model').dispatchEvent(new Event('change'));
                    return q('#local-model').value === model && usesTrainedModel() &&
                      !usesModelApi() && q('#taskpack').value === '__baseline__';
                  });
                  return JSON.stringify({advanced:!!advanced, below:activity.top >= top.bottom,
                    guiModels,
                    desktopInputHidden:hidden, apiFirstSetup:api,
                    trainedOutput:!q('#trained-output').hidden,
                    continuousOnly:!q('#trained-continuous'),
                    trainedBaseline:q('#taskpack').value === '__baseline__',
                    idsUnique:new Set([...document.querySelectorAll('[id]')].map(x=>x.id)).size === document.querySelectorAll('[id]').length});
                })()""")
                results.append(json.loads(data))
            finally:
                window.destroy()

        window.events.loaded += check
        try:
            webview.start(gui="edgechromium", private_mode=True)
        finally:
            server.shutdown()
            server.server_close()
        print(json.dumps(results))
        assert results and all(results[0].values()), results


if __name__ == "__main__":
    main()

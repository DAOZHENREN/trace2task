from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parents[1]
datas = collect_data_files('trace2task', includes=['web/*'])
datas += [(str(root / 'scripts/trained_model'), 'scripts/trained_model')]
datas += [(str(root / 'scripts/local_gui'), 'scripts/local_gui')]
# The external CUDA Python process does not read this executable's PYZ archive.
datas += [(str(root / 'src/trace2task' / name), 'src/trace2task')
          for name in ['__init__.py', 'local_gui_protocol.py', 'local_gui_owl_prompt.py', 'actions.py']]
datas += [(str(root / 'integrations'), 'integrations'), (str(root / 'LICENSE'), '.')]
hidden = []
for package in ['langgraph', 'langgraph.checkpoint.sqlite', 'webview', 'clr_loader']:
    hidden += collect_submodules(package)
for package in ['trace2task', 'pywebview', 'langgraph', 'langgraph-checkpoint', 'langgraph-checkpoint-sqlite']:
    datas += copy_metadata(package)
a = Analysis([str(root / 'packaging/windows/launcher.py')], pathex=[str(root / 'src')],
             binaries=[], datas=datas, hiddenimports=hidden,
             excludes=['pytest', 'ruff', 'torch', 'transformers', 'IPython'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Trace2Task',
          console=False, debug=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Trace2Task')

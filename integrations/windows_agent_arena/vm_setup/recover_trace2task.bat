@echo off
set "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
set "PIP_DEFAULT_TIMEOUT=120"
powershell -ExecutionPolicy Bypass -File "\\host.lan\Data\setup.ps1" >> "\\host.lan\Data\ps_recovery_log.txt" 2>&1

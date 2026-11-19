@echo off
rem Evidence Echo MCP server launcher (Windows). macOS and Linux run ev_mcp instead.
rem .mcp.json starts this file by its full path. NoDefaultCurrentDirectoryInExePath keeps cmd
rem from running an interpreter that sits in the current folder (the user's project); the
rem first Python 3.11+ of python, py -3 and python3 on PATH runs ev_mcp.py.
setlocal
set "NoDefaultCurrentDirectoryInExePath=1"
for %%P in (python "py -3" python3) do (
    %%~P -c "import sys; sys.exit(sys.version_info < (3, 11))" <nul >nul 2>nul && (
        %%~P "%~dp0ev_mcp.py" %*
        exit /b
    )
)
echo Evidence Echo needs Python 3.11+ as python, py -3 or python3 on PATH 1>&2
exit /b 2

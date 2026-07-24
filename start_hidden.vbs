' autoliveblog web server: hidden background start (used by watchdog / login autostart)
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
py = ws.ExpandEnvironmentStrings("%AUTOLIVEBLOG_PYTHON%")
If py = "%AUTOLIVEBLOG_PYTHON%" Or py = "" Then py = "python"
cmd = "cmd /c cd /d """ & root & """ && set PYTHONIOENCODING=utf-8 && " & _
      """" & py & """ -m uvicorn autoliveblog.web.server:app " & _
      "--host 127.0.0.1 --port 8766 > logs\server.log 2>&1"
ws.Run cmd, 0, False

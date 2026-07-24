' autoliveblog watchdog: hidden background start (put a shortcut to this in shell:startup)
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("WScript.Shell")
ws.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & _
       root & "\watchdog.ps1""", 0, False

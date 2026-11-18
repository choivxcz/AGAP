Option Explicit

Dim shell, fso, root
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = root
shell.Run "pythonw.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000", 0, False
WScript.Sleep 4000
shell.Run "http://127.0.0.1:8000/", 1, False

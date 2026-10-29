Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
shell.Run Chr(34) & root & "\.venv\Scripts\pythonw.exe" & Chr(34) & " " & Chr(34) & root & "\run_desktop.py" & Chr(34), 0, False

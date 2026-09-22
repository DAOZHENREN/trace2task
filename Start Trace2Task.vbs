Option Explicit
Dim shell, fs, root, command
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & root & "\scripts\start-desktop.ps1"""
shell.Run command, 0, False

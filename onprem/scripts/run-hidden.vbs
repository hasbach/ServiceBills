' Runs a ServiceBills PowerShell script with no visible window and waits for it.
' Usage: wscript.exe run-hidden.vbs <script.ps1> [args...]
Option Explicit

Dim fso, sh, scriptsDir, cmd, i, a, rc
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

If WScript.Arguments.Count < 1 Then WScript.Quit 2

scriptsDir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & _
      scriptsDir & "\" & WScript.Arguments(0) & """"

For i = 1 To WScript.Arguments.Count - 1
    a = WScript.Arguments(i)
    If InStr(a, " ") > 0 Then a = """" & a & """"
    cmd = cmd & " " & a
Next

rc = sh.Run(cmd, 0, True)
WScript.Quit rc

' Runs serve.cmd with NO console window.
'
' A scheduled task triggered at logon runs in the user's own session, so a
' .cmd would flash up a black window and leave it on screen. WindowStyle 0
' hides it; the backend logs to workspace\logs\backend.log instead.
Dim shell, fso, here, target
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
target = Chr(34) & here & "\serve.cmd" & Chr(34)
shell.Run target, 0, False

' ---------------------------------------------------------------------------
' MangaLiveReader silent launcher. Double-click to start with no console window:
' the service runs in the background and a tray icon appears.
'
' If the environment is not installed yet, hands off to MangaLiveReader.cmd so
' the download and the API-key prompt are visible.
'
' KEEP THIS FILE ASCII-ONLY. wscript reads .vbs as ANSI (cp949 on Korean
' Windows), so UTF-8 Korean in string literals - including file paths - comes
' out as mojibake. That is why every path referenced here is ASCII.
' ---------------------------------------------------------------------------
Option Explicit

Dim fso, shell, scriptDir, pythonw, tray, bootstrap
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw   = scriptDir & "\.venv\Scripts\pythonw.exe"
tray      = scriptDir & "\scripts\tray.py"
bootstrap = scriptDir & "\MangaLiveReader.cmd"

shell.CurrentDirectory = scriptDir

' Not installed yet - the bootstrap needs a visible window.
If Not fso.FileExists(pythonw) Then
    If fso.FileExists(bootstrap) Then
        shell.Run """" & bootstrap & """", 1, False
    Else
        MsgBox "Not found: " & bootstrap, vbCritical, "MangaLiveReader"
    End If
    WScript.Quit 0
End If

If Not fso.FileExists(tray) Then
    MsgBox "Not found: " & tray, vbCritical, "MangaLiveReader"
    WScript.Quit 1
End If

' 0 = hidden window. pythonw has no console, so nothing flashes.
shell.Run """" & pythonw & """ """ & tray & """", 0, False

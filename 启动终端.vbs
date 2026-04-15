' 贵金属量化监控终端 — 一键启动器
' 双击此文件即可启动程序（Windows Script Host 专用，无需管理员权限）

Set oFSO   = CreateObject("Scripting.FileSystemObject")
Set oShell = CreateObject("WScript.Shell")

' 获取脚本所在目录（项目根目录）
strDir = oFSO.GetParentFolderName(WScript.ScriptFullName)
strPy  = strDir & "\launcher.py"

' 检查 launcher.py 是否存在
If Not oFSO.FileExists(strPy) Then
    MsgBox "找不到 launcher.py，请确认启动.vbs 与 launcher.py 位于同一目录。" & Chr(13) & strPy, vbCritical, "贵金属终端"
    WScript.Quit 1
End If

' 使用 cmd /k 保持窗口打开（方便查看启动日志）
' 若不需要控制台窗口，可将 /k 改为 /c，并把第3参数从1改为0
strCmd = "cmd.exe /k cd /d """ & strDir & """ && python launcher.py"
oShell.Run strCmd, 1, False

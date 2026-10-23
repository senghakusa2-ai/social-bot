Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\Lenovo\social_downloader"
WshShell.Run "pythonw.exe bot.py", 0, False

Set-Location "%USERPROFILE%\esb-smart-meter"
$logFile = "%USERPROFILE%\esb-smart-meter\esb_run.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content $logFile "$timestamp - Starting ESB meter reader..."
python esb-smart-meter-reader.py >> $logFile 2>&1
$exit = $LASTEXITCODE
Add-Content $logFile "$timestamp - Finished with exit code $exit"

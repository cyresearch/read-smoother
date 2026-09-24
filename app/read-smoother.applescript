-- Read Smoother (畅读) launcher: start the local server if it is not running, then open the shelf in a Chrome app window.
-- Build:  osacompile -o ~/Desktop/"Read Smoother.app" app/read-smoother.applescript && cp app/icon.icns ~/Desktop/"Read Smoother.app"/Contents/Resources/applet.icns
-- Change `proj` if you cloned the project somewhere else.
set proj to (POSIX path of (path to home folder)) & "projects/read-smoother"
set theURL to "http://127.0.0.1:8765/"
set probe to "curl -s -m 1 -o /dev/null -w '%{http_code}' " & theURL & "api/library"
set code to do shell script probe
if code is not "200" then
	do shell script "cd " & quoted form of proj & " && nohup ./start.sh --no-browser > server.log 2>&1 &"
	repeat 20 times
		delay 0.5
		set code to do shell script probe
		if code is "200" then exit repeat
	end repeat
end if
if code is "200" then
	try
		do shell script "open -na 'Google Chrome' --args --app=" & theURL
	on error
		open location theURL
	end try
else
	display dialog "Read Smoother did not start. Run ./start.sh in a terminal to see the error. / 畅读没能启动，请在终端里运行 ./start.sh 查看报错。" buttons {"OK"} default button 1
end if

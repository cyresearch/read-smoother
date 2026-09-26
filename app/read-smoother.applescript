-- Read Smoother (畅读) launcher: start the local server if it is not running, then open the shelf in a Chrome app window.
-- Build:  osacompile -o ~/Desktop/"Read Smoother.app" app/read-smoother.applescript && cp app/icon.icns ~/Desktop/"Read Smoother.app"/Contents/Resources/applet.icns
-- Change `proj` if you cloned the project somewhere else.
set proj to (POSIX path of (path to home folder)) & "projects/read-smoother"
set theURL to "http://127.0.0.1:8765/"
-- `|| true` keeps do shell script from raising an error when the server is not running yet (curl exits 7, output "000").
set probe to "curl -s -m 1 -o /dev/null -w '%{http_code}' " & theURL & "api/library || true"
set code to do shell script probe
if code is not "200" then
	-- Redirect and close every inherited descriptor in the outer shell first; otherwise do shell script waits on its pipes forever (macOS sh forks a wrapper that keeps them).
	do shell script "cd " & quoted form of proj & " && exec > server.log 2>&1 < /dev/null; for fd in $(seq 3 255); do eval \"exec $fd>&-\"; done; nohup ./start.sh --no-browser &"
	repeat 20 times
		delay 0.5
		set code to do shell script probe
		if code is "200" then exit repeat
	end repeat
end if
if code is "200" then
	try
		-- A dedicated profile (data/chrome-app) so an already-running Chrome cannot swallow the --app request.
		do shell script "open -na 'Google Chrome' --args --user-data-dir=" & quoted form of (proj & "/data/chrome-app") & " --no-first-run --no-default-browser-check --app=" & theURL
	on error
		open location theURL
	end try
else
	display dialog "Read Smoother did not start. Run ./start.sh in a terminal to see the error. / 畅读没能启动，请在终端里运行 ./start.sh 查看报错。" buttons {"OK"} default button 1
end if

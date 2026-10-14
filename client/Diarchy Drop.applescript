on diarchyFileCommand()
	try
		set configuredCommand to do shell script "printf %s \"$DIARCHY_FILE_BIN\""
		if configuredCommand is not "" then return configuredCommand
	end try
	return POSIX path of (path to home folder) & ".local/bin/diarchy-file"
end diarchyFileCommand

on recordNotification(messageText)
	try
		set notificationLog to do shell script "printf %s \"$DIARCHY_DROP_NOTIFICATION_LOG\""
		if notificationLog is not "" then
			do shell script "/usr/bin/printf '%s\n' " & quoted form of messageText & " >> " & quoted form of notificationLog
		end if
	end try
end recordNotification

on recordAlert(messageText)
	try
		set alertLog to do shell script "printf %s \"$DIARCHY_DROP_ALERT_LOG\""
		if alertLog is not "" then
			do shell script "/usr/bin/printf '%s\n' " & quoted form of messageText & " >> " & quoted form of alertLog
			return
		end if
	end try
	display alert "Diarchy Drop" message messageText as critical
end recordAlert

on finalErrorLine(errorText)
	try
		return do shell script "/usr/bin/printf %s " & quoted form of errorText & " | /usr/bin/tail -n 1 | /usr/bin/cut -c 1-240"
	on error
		return "Transfer failed"
	end try
end finalErrorLine

on sendPaths(posixItems)
	set attemptCount to 0
	set failureCount to 0
	set commandPath to diarchyFileCommand()
	repeat with itemPath in posixItems
		set attemptCount to attemptCount + 1
		set itemName to do shell script "/usr/bin/basename " & quoted form of (itemPath as text)
		try
			do shell script quoted form of commandPath & " send " & quoted form of (itemPath as text)
			set notificationText to "Sent " & itemName
			recordNotification(notificationText)
			display notification notificationText with title "Diarchy Drop"
		on error errorMessage
			set failureCount to failureCount + 1
			set notificationText to "Failed " & itemName
			recordNotification(notificationText)
			display notification notificationText with title "Diarchy Drop"
			recordAlert(finalErrorLine(errorMessage))
		end try
	end repeat
	if failureCount is greater than 0 then
		return (failureCount as text) & " of " & (attemptCount as text) & " item(s) failed"
	end if
	return (attemptCount as text) & " item(s) sent"
end sendPaths

on open theItems
	set posixItems to {}
	repeat with theItem in theItems
		set end of posixItems to POSIX path of theItem
	end repeat
	sendPaths(posixItems)
end open

on run argv
	return sendPaths(argv)
end run

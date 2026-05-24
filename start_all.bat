@echo off
title Master System Launcher

echo Launching Discord Bot...
start "Discord Bot" cmd /k "node botDiscord.js"

echo Launching Steam Worker...
start "Steam Token Gen" cmd /k "node StemTokenGen.js"

echo Launching Ubisoft Worker...
start "Ubisoft Worker" cmd /k "cd UbisoftBot && dotnet DenuvoTicket.dll"

echo All systems launched in separate windows!

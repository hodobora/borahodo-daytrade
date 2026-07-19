@echo off
cd /d "%~dp0"
python scan.py premarket >> scan_log.txt 2>&1

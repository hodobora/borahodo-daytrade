@echo off
cd /d "%~dp0"
python scan.py evening >> scan_log.txt 2>&1

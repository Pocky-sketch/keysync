"""
按键同步.pyw — Console-free launcher.

Double-click this file to run 按键同步 without the black terminal window.
(On Windows, .pyw files run with pythonw.exe which doesn't show a console.)
"""

import os
import sys

# Ensure we're in the project directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main

if __name__ == "__main__":
    main()

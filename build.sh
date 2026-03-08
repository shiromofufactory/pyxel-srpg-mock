#!/bin/bash
set -e
pyxel package . srpg.py
pyxel app2html pyxel-srpg-mock.pyxapp
mv pyxel-srpg-mock.html index.html
sed -i '' 's/gamepad: "enabled"/gamepad: "disabled"/' index.html
echo "Build complete: index.html"

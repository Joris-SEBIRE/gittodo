#!/usr/bin/env bash
# Construit GitTodo.app : bundle autonome (interpréteur + dépendances + sources),
# sans icône dans le Dock, juste un élément dans la barre des menus.
#
# Le bundle est un venv dont la racine est Contents/, avec une copie du binaire
# Python dans Contents/MacOS/ : c'est ce qui permet à macOS d'identifier le
# processus comme GitTodo.app (nom, lancement au démarrage, `quit app`).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="${1:-$ROOT/build/GitTodo.app}"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/src/gittodo/__init__.py")"
PYTHON="${PYTHON:-}"
for candidate in "$PYTHON" /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 "$(command -v python3 || true)"; do
    if [ -n "$candidate" ] && [ -x "$candidate" ]; then PYTHON="$candidate"; break; fi
done
[ -n "$PYTHON" ] || { echo "python3 introuvable" >&2; exit 1; }
# Le repli sur le python3 du PATH tombe sur celui d'Apple, trop vieux pour pyobjc et non
# framework : PyObjC y construirait une app incapable de tenir un élément de barre. On refuse
# plutôt que de livrer un bundle qui échoue au lancement.
"$PYTHON" - <<'CHECK' || { echo "→ installe Python 3.12+ (brew install python@3.13)" >&2; exit 1; }
import os, sys
ok = sys.version_info >= (3, 12)
framework = os.path.exists(os.path.join(sys.base_prefix, "Resources", "Python.app"))
if not ok:
    print(f"python trop ancien : {sys.version.split()[0]}, il faut 3.12 ou plus", file=sys.stderr)
if ok and not framework:
    print("cet interpréteur n'est pas une installation framework", file=sys.stderr)
sys.exit(0 if ok and framework else 1)
CHECK

echo "→ $APP (python: $PYTHON, version: $VERSION)"
# Le premier argument est effacé récursivement : on refuse tout ce qui n'est pas un bundle,
# pour qu'un chemin donné par erreur ne coûte pas un dossier de travail.
case "$APP" in
    *.app) ;;
    *) echo "cible refusée : « $APP » n'est pas un bundle .app" >&2; exit 1 ;;
esac
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

"$PYTHON" -m venv "$APP/Contents"
"$APP/Contents/bin/python" -m pip install --quiet --upgrade pip
"$APP/Contents/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"

# Vrai interpréteur : bin/python3.x d'un build framework n'est qu'un stub qui se
# ré-exécute via Resources/Python.app, ce qui ferait perdre l'identité du bundle.
REAL_PYTHON="$("$APP/Contents/bin/python" -c '
import os, sys
app = os.path.join(sys.base_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python")
print(app if os.path.exists(app) else os.path.realpath(sys._base_executable))')"
cp "$REAL_PYTHON" "$APP/Contents/MacOS/gittodo-python"
chmod +x "$APP/Contents/MacOS/gittodo-python"

cp -R "$ROOT/src/gittodo" "$APP/Contents/Resources/gittodo"
find "$APP/Contents/Resources/gittodo" -name '__pycache__' -type d -exec rm -rf {} +

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>GitTodo</string>
  <key>CFBundleDisplayName</key><string>GitTodo</string>
  <key>CFBundleIdentifier</key><string>fr.jsebire.gittodo</string>
  <key>CFBundleExecutable</key><string>GitTodo</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/GitTodo" <<'LAUNCHER'
#!/bin/sh
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$CONTENTS/Resources"
export PYTHONDONTWRITEBYTECODE=1
exec "$CONTENTS/MacOS/gittodo-python" -m gittodo "$@"
LAUNCHER
chmod +x "$APP/Contents/MacOS/GitTodo"

# Pas de codesign : pyvenv.cfg à la racine de Contents/ est rejeté comme
# sous-composant non signé, et un build local n'est pas mis en quarantaine.
rm -f "$APP/Contents/.gitignore"
echo "✓ $APP"

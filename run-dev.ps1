# Lance l'application en mode DEV RAPIDE : serveur de développement (port 5000)
# AVEC le login désactivé, pour itérer vite sans se reconnecter.
#
# Usage :  .\run-dev.ps1
# (Si PowerShell bloque le script : lancez plutôt les deux lignes à la main,
#  ou voir NOTES.md.)
#
# NE PAS utiliser ce mode en ligne — c'est uniquement pour votre PC.

$env:WATERCOLOR_NO_LOGIN = "1"
& "$PSScriptRoot\venv\Scripts\python.exe" "$PSScriptRoot\app.py"

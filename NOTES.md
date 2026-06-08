# Notes — aide-mémoire

## Lancer l'application en local

Serveur de développement (port 5000, recharge auto pendant le dev) :

    venv\Scripts\python.exe app.py
    →  http://localhost:5000/

Serveur de production en local — Waitress (port 8000) :

    venv\Scripts\python.exe serve.py
    →  http://localhost:8000/

Mot de passe de connexion par défaut en local : **aquarelle**
(remplacé en ligne par la variable WATERCOLOR_PASSWORD)

### Mode dev rapide (SANS login)

Pour itérer vite sans avoir à se reconnecter :

    .\run-dev.ps1

Ça lance le serveur (port 5000) avec le login désactivé. Uniquement pour
le PC local — le serveur en ligne garde toujours le login (il ne définit
pas WATERCOLOR_NO_LOGIN).

## Mettre à jour le code (après avoir modifié quelque chose)

    git -C 'H:\Code\Watercolor_app' add .
    git -C 'H:\Code\Watercolor_app' commit -m "ce que j'ai changé"
    git -C 'H:\Code\Watercolor_app' push

Puis, sur PythonAnywhere (console Bash) :

    cd ~/stock-aquarelle
    git pull
    # puis cliquer "Reload" dans l'onglet Web

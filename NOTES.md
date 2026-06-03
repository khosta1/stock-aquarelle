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

## Mettre à jour le code (après avoir modifié quelque chose)

    git -C 'G:\Drawing\aquarelle\WIP_app\Watercolor_app' add .
    git -C 'G:\Drawing\aquarelle\WIP_app\Watercolor_app' commit -m "ce que j'ai changé"
    git -C 'G:\Drawing\aquarelle\WIP_app\Watercolor_app' push

Puis, sur PythonAnywhere (console Bash) :

    cd ~/stock-aquarelle
    git pull
    # puis cliquer "Reload" dans l'onglet Web

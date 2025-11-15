# Steam Hour Booster (CLI)

A lightweight Python CLI that lets you configure a Steam account and idle (simulate playing) up to 33 games at once to boost playtime.  
The menu-driven workflow stores configuration in `config.json` and works on both Windows and Linux.

> ⚠️ **Security:** credentials are stored in plain text. Keep the project folder private.

## Requirements

- Python 3.11+
- Steam client installed with an active account
- (Optional) `shared_secret` from the Steam mobile authenticator for automatic 2FA codes

## Installation

### Windows (PowerShell)
```powershell
cd C:\Users\HP\Documents\adam\steam-hour-py
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Linux/macOS (bash)
```bash
cd ~/steam-hour-py
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

```bash
python main.py
```

Menu features:

1. View current account configuration
2. Enter Steam login/password
3. Set `shared_secret` for automatic 2FA codes
4. Manage the AppID list (up to 33 entries) manually or by importing from your Steam library
5. Start the booster (logs into Steam and pretends to play selected games)

A `config.json` file is created on the first run.  
Press Enter or `Ctrl+C` to stop the booster.

## Interface Language

At the first launch you can choose Polish or English. The choice is saved in `config.json` (`language`) and can be changed later in “Account configuration → Change interface language”.

## Importing Games from Your Steam Library

To use “Add from Steam library”, you need:

1. **Steam Web API Key** – obtain it from <https://steamcommunity.com/dev/apikey>.
2. **SteamID64** – find it on <https://steamid.io> or let the app resolve it from your profile URL.

Fill both values in “Account configuration”, then open “Games configuration → Add from Steam library” to see the games grid and pick AppIDs to add.

## AppID Tips

Every store URL contains the AppID, e.g. `https://store.steampowered.com/app/730` ⇒ `730`.  
See `config.example.json` for the expected config structure.

## Security Notes

- `config.json` stores credentials in plain text; protect the folder from other users.
- If you use 2FA, add `shared_secret` (Base64) to avoid entering codes manually.
- Email and app codes can still be entered interactively if you prefer not to store `shared_secret`.

## Troubleshooting

Run the idler module directly to inspect logs:

```bash
python -m steam_hour.steam_idler
```

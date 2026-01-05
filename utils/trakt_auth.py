#!/usr/bin/env python3
"""
Trakt Authentication Helper.

Run this script to authenticate with Trakt using device code flow.
Tokens are saved to trakt.yml for future use.
"""

import os
import sys
import yaml

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.trakt import TraktClient, TraktAuthError


def get_config_dir():
    """Get the config directory path."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')


def load_config():
    """Load config from config/ directory (main + trakt.yml)."""
    config_dir = get_config_dir()
    config_path = os.path.join(config_dir, 'config.yml')
    trakt_path = os.path.join(config_dir, 'trakt.yml')

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Merge trakt.yml if it exists
    if os.path.exists(trakt_path):
        with open(trakt_path, 'r') as f:
            config['trakt'] = yaml.safe_load(f)

    return config


def save_tokens(access_token: str, refresh_token: str):
    """Save tokens to trakt.yml"""
    trakt_path = os.path.join(get_config_dir(), 'trakt.yml')

    with open(trakt_path, 'r') as f:
        trakt_config = yaml.safe_load(f)

    trakt_config['access_token'] = access_token
    trakt_config['refresh_token'] = refresh_token

    with open(trakt_path, 'w') as f:
        yaml.dump(trakt_config, f, default_flow_style=False, sort_keys=False)

    print("\033[92mTokens saved to config/trakt.yml\033[0m")


def main():
    print("\033[96m=== Trakt Authentication ===\033[0m")
    print()

    # Load config
    try:
        config = load_config()
    except FileNotFoundError:
        print("\033[91mError: config/config.yml not found. Run ./run.sh first.\033[0m")
        sys.exit(1)

    trakt_config = config.get('trakt', {})

    if not trakt_config.get('enabled', False):
        print("\033[93mTrakt is disabled. Create config/trakt.yml to enable it.\033[0m")
        print("See config/trakt.example.yml for template.")
        sys.exit(1)

    client_id = trakt_config.get('client_id')
    client_secret = trakt_config.get('client_secret')

    if not client_id or not client_secret or client_id == 'null' or client_secret == 'null':
        print("\033[91mError: Trakt client_id or client_secret not configured.\033[0m")
        print("Add them to config/trakt.yml")
        sys.exit(1)

    # Check if already authenticated
    if trakt_config.get('access_token') and trakt_config.get('access_token') != 'null':
        print("Already authenticated!")
        print("To re-authenticate, remove access_token from config/trakt.yml first.")
        sys.exit(0)

    # Create client
    client = TraktClient(
        client_id=client_id,
        client_secret=client_secret,
        token_callback=save_tokens
    )

    # Start device auth flow
    try:
        print("Requesting device code from Trakt...")
        device_info = client.get_device_code()

        print()
        print("\033[96m1. Go to: \033[93m" + device_info['verification_url'] + "\033[0m")
        print("\033[96m2. Enter code: \033[93m" + device_info['user_code'] + "\033[0m")
        print()
        print("Waiting for authorization...")
        print("(Press Ctrl+C to cancel)")
        print()

        # Poll for token
        success = client.poll_for_token(
            device_code=device_info['device_code'],
            interval=device_info.get('interval', 5),
            expires_in=device_info.get('expires_in', 600)
        )

        if success:
            print()
            print("\033[92m=== Authentication Successful! ===\033[0m")
            print()
            username = client.get_username()
            if username:
                print(f"Logged in as: {username}")
            print("Trakt integration is now ready to use.")
        else:
            print("\033[91mAuthentication failed or expired.\033[0m")
            print("Please try again.")
            sys.exit(1)

    except TraktAuthError as e:
        print(f"\033[91mAuthentication error: {e}\033[0m")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\033[93mCancelled.\033[0m")
        sys.exit(1)


if __name__ == "__main__":
    main()

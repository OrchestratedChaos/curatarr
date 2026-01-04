#!/usr/bin/env python3
"""
Trakt Authentication Helper.

Run this script to authenticate with Trakt using device code flow.
Tokens are saved to config.yml for future use.
"""

import os
import sys
import yaml

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.trakt import TraktClient, TraktAuthError


def load_config():
    """Load config.yml"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def save_tokens(access_token: str, refresh_token: str):
    """Save tokens to config.yml"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yml')

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    config['trakt']['access_token'] = access_token
    config['trakt']['refresh_token'] = refresh_token

    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print("\033[92mTokens saved to config.yml\033[0m")


def main():
    print("\033[96m=== Trakt Authentication ===\033[0m")
    print()

    # Load config
    try:
        config = load_config()
    except FileNotFoundError:
        print("\033[91mError: config.yml not found. Run ./run.sh first.\033[0m")
        sys.exit(1)

    trakt_config = config.get('trakt', {})

    if not trakt_config.get('enabled', False):
        print("\033[93mTrakt is disabled in config.yml.\033[0m")
        print("Set 'trakt.enabled: true' to enable it.")
        sys.exit(1)

    client_id = trakt_config.get('client_id')
    client_secret = trakt_config.get('client_secret')

    if not client_id or not client_secret or client_id == 'null' or client_secret == 'null':
        print("\033[91mError: Trakt client_id or client_secret not configured.\033[0m")
        print("Add them to config.yml under trakt: section.")
        sys.exit(1)

    # Check if already authenticated
    if trakt_config.get('access_token') and trakt_config.get('access_token') != 'null':
        print("Already authenticated!")
        print("To re-authenticate, remove access_token from config.yml first.")
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

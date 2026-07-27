"""
Content-rating and collection-label POLICY for Plex, split out of
utils/plex.py (audit remediation batch F/I, PR1(b)).

utils/plex.py is a thin Plex API adapter (connection setup, watch-history
fetches, collection CRUD - all "how do we talk to Plex"). The four names
in this module are different in kind: they encode Curatarr's OWN business
rules about what a user is allowed to see -

  - MOVIE_RATING_HIERARCHY / TV_RATING_HIERARCHY: this app's ordering of
    content ratings from least to most restrictive (Plex itself has no
    such ordering - a rating is just a string it stores).
  - get_max_rating_for_user / is_rating_allowed: the max_rating user
    preference and the "is this item's rating within that limit" check
    built on top of the hierarchies above.
  - apply_user_label_restrictions: the PrivateCollection_*/Recommended_*
    labeling convention that keeps one user's recommendation collection
    out of another user's library Browse/Search results on the same
    server. This is UI-level separation only, not an access-control
    boundary - see the function's own docstring for the enumeration
    caveat (a user who already has, or guesses, a collection's ratingKey
    can still retrieve its contents directly via the Plex API).

apply_user_label_restrictions still talks to the Plex API directly (via
utils.plex's _capped_get/_capped_put) - it's included here anyway because
what it's DOING is enforcing this module's own label-visibility policy,
not just relaying a request/response; the API calls are how that policy
gets applied, not the reason the function exists. See utils/plex.py's own
module docstring for the client/policy split this mirrors.

Depends on utils.plex (for the shared _capped_get/_capped_put HTTP
helpers) - never the other way around. utils/plex.py must never import
from this module, or the two would form an import cycle.
"""

import logging
from typing import Dict, Optional

import requests
from plexapi.myplex import MyPlexAccount

from .config import PLEX_REQUEST_TIMEOUT
from .display import GREEN, RESET, log_warning
from .plex import _capped_get, _capped_put

logger = logging.getLogger("curatarr")

# Content rating hierarchy constants
# Movies: G < PG < PG-13 < R < NC-17
MOVIE_RATING_HIERARCHY = ["G", "PG", "PG-13", "R", "NC-17"]

# TV: TV-Y < TV-Y7 < TV-G < TV-PG < TV-14 < TV-MA
TV_RATING_HIERARCHY = ["TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"]


def get_max_rating_for_user(user_preferences: dict, username: Optional[str] = None) -> Optional[str]:
    """
    Get the maximum content rating allowed for a user.

    Args:
        user_preferences: User preferences dictionary
        username: Username to get max_rating for

    Returns:
        Content rating string (e.g., 'PG-13', 'TV-14') or None if no restriction
    """
    if not username or not user_preferences or username not in user_preferences:
        return None

    user_prefs = user_preferences[username]
    return user_prefs.get("max_rating")


def is_rating_allowed(content_rating: Optional[str], max_rating: Optional[str], media_type: str = "movie") -> bool:
    """
    Check if a content rating is allowed given the user's max_rating.

    Args:
        content_rating: The content rating of the item (e.g., 'R', 'TV-MA')
        max_rating: The user's maximum allowed rating (e.g., 'PG-13', 'TV-14')
        media_type: 'movie' or 'tv' to select the appropriate rating hierarchy

    Returns:
        True if the content rating is at or below max_rating, False otherwise
    """
    if not max_rating or not content_rating:
        return True  # No restriction or no rating = allow

    # Normalize the rating strings (uppercase, strip whitespace)
    content_rating = content_rating.upper().strip()
    max_rating = max_rating.upper().strip()

    # Select the appropriate hierarchy
    hierarchy = TV_RATING_HIERARCHY if media_type == "tv" else MOVIE_RATING_HIERARCHY

    # Get the indices of both ratings
    try:
        content_idx = hierarchy.index(content_rating)
        max_idx = hierarchy.index(max_rating)
        return content_idx <= max_idx
    except ValueError:
        # Rating not in hierarchy - allow by default (e.g., 'NR', 'Unrated')
        # Log this case for debugging but don't block the content
        logger.debug(f"Rating '{content_rating}' not in hierarchy, allowing by default")
        return True


def apply_user_label_restrictions(
    config: Dict,
    all_user_private_labels: Dict[str, str],
) -> bool:
    """
    Apply exclude restrictions so each user's collection is excluded from
    every other user's library browse/search results.

    NOT an access-control boundary - see the module docstring above and
    README.md's FAQ for the enumeration caveat (Plex applies this
    exclusion to the collection object, not to the items a client
    requests directly from it).

    Items get Recommended_* labels (visible to everyone, not excluded).
    Collections get their own PrivateCollection_* label - passed in here
    fully built via all_user_private_labels, not derived from the item
    labels by string surgery (#261: label.replace("Recommended_",
    "PrivateCollection_") silently produced the wrong, unprefixed result
    whenever the item label wasn't literally "Recommended_<user>" - e.g.
    every install running with collections.append_usernames: false,
    where it collapsed to the same bare label for every user).

    Each user gets an EXCLUDE filter for other users' PrivateCollection_* labels:
    - They can see their full library (all items, including others' recommendations)
    - They can see their own collection (their PrivateCollection label not excluded)
    - They cannot browse/search to other users' collections (those labels excluded)

    Uses direct Plex API calls (not plexapi's updateFriend which doesn't work for Home users).
    Note: Server admin cannot have restrictions applied (Plex limitation).

    Args:
        config: Configuration dict with plex token
        all_user_private_labels: Dict mapping username to their already-built
                         PrivateCollection_* label name (see
                         recommenders/base.py's manage_plex_labels, which
                         builds this the same way it builds the item-level
                         label - via build_label_name(), just rooted at
                         "PrivateCollection" instead of the configured
                         label_name)
                         e.g., {'Jason': 'PrivateCollection_Jason', 'Sarah': 'PrivateCollection_Sarah'}

    Returns:
        True if all restrictions applied successfully, False if any failed
    """
    if not all_user_private_labels:
        return True

    # Only one user - nothing to hide from anyone
    if len(all_user_private_labels) <= 1:
        return True

    plex_token = config["plex"]["token"]

    try:
        # Get admin username to skip
        account = MyPlexAccount(token=plex_token)
        admin_username = account.username.lower()

        # Fetch all users via direct API (works for both shared and managed users)
        users_url = "https://plex.tv/api/users"
        response = _capped_get(users_url, headers={"X-Plex-Token": plex_token}, timeout=PLEX_REQUEST_TIMEOUT)
        response.raise_for_status()

        # Parse XML response to get user IDs and names
        import xml.etree.ElementTree as ET

        root = ET.fromstring(response.content)

        # Build user lookup: username -> user_id
        plex_users = {}
        for user_elem in root.findall(".//User"):
            user_id = user_elem.get("id")
            title = user_elem.get("title", "")
            username_attr = user_elem.get("username", "")
            email = user_elem.get("email", "")

            if title:
                plex_users[title.lower()] = user_id
            if username_attr:
                plex_users[username_attr.lower()] = user_id
            if email:
                plex_users[email.lower()] = user_id

        logger.debug(f"Plex users available for restrictions: {list(plex_users.keys())}")

        all_success = True
        for username, _user_private_label in all_user_private_labels.items():
            # Admin can't have restrictions
            if username.lower() == admin_username:
                logger.debug(f"Skipping restrictions for admin user: {username}")
                continue

            # Find the user ID
            user_id = plex_users.get(username.lower())

            # Try normalized match if exact match fails
            if not user_id:
                username_normalized = username.lower().replace(" ", "").replace("-", "").replace("_", "")
                for key, uid in plex_users.items():
                    key_normalized = key.replace(" ", "").replace("-", "").replace("_", "")
                    if username_normalized == key_normalized:
                        user_id = uid
                        logger.debug(f"Matched '{username}' to user ID {uid} via normalized match")
                        break

            if not user_id:
                log_warning(f"User '{username}' not found for label restrictions. Available: {list(plex_users.keys())}")
                all_success = False
                continue

            # Labels to EXCLUDE: every OTHER user's already-built
            # PrivateCollection_* label (on collections), never
            # Recommended_* (on items) - this hides other users'
            # collections but keeps items visible to everyone. Used as
            # given, not derived here (#261 - see this function's docstring).
            exclude_labels = [
                private_label for u, private_label in all_user_private_labels.items() if u.lower() != username.lower()
            ]

            if not exclude_labels:
                continue  # Nothing to exclude

            # Build filter string: label!=Label1,Label2,Label3
            labels_str = ",".join(exclude_labels)
            filter_value = f"label!={labels_str}"

            # Apply restrictions via direct PUT to Plex API
            update_url = f"https://plex.tv/api/users/{user_id}"
            params = {"filterMovies": filter_value, "filterTelevision": filter_value}

            try:
                put_response = _capped_put(
                    update_url,
                    params=params,
                    headers={"X-Plex-Token": plex_token},
                    timeout=PLEX_REQUEST_TIMEOUT,
                )
                put_response.raise_for_status()
                print(f"{GREEN}Applied exclusions for {username}: hiding labels {exclude_labels}{RESET}")
            except requests.RequestException as e:
                log_warning(f"Failed to apply restrictions for {username}: {e}")
                all_success = False

        return all_success

    except requests.RequestException as e:
        log_warning(f"Error fetching Plex users: {e}")
        return False
    except Exception as e:
        log_warning(f"Unexpected error applying restrictions: {e}")
        return False

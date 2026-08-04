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
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

import requests
from plexapi.myplex import MyPlexAccount

from .config import MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV, PLEX_REQUEST_TIMEOUT
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


def _labels_for(labels: Union[str, Sequence[str], Mapping[str, Sequence[str]]], media_type: str) -> List[str]:
    """
    This user's labels for one media type.

    Accepts every shape callers have used: a bare string (pre-#332), a
    flat sequence (#332), or the per-media-type mapping (#340). The first
    two are not media-type aware, so they apply to both - that is the
    only meaning they can have.
    """
    if isinstance(labels, str):
        return [labels]
    if isinstance(labels, Mapping):
        return [str(x) for x in labels.get(media_type, []) if x]
    return [str(x) for x in labels if x]


def build_all_private_labels(
    config: Dict, users: List[str], append_usernames: bool = True
) -> Dict[str, Dict[str, List[str]]]:
    """
    Every PrivateCollection_* label a user owns, across every library.

    A user does not have one private label, they have one PER LIBRARY:
    recommenders/base.py roots the label at
    "PrivateCollection" + _library_suffix_for_label(), and that suffix is
    "_<library_id>" on any install with more than one library of a given
    media type. The movie run therefore knows only the movie labels and
    the TV run only the TV labels.

    That mattered because apply_user_label_restrictions() writes BOTH
    filterMovies and filterTelevision on every call. With each media type
    supplying only its own labels, the later run (TV, which follows
    movies) overwrote the movie exclusions in both fields - so on a
    multi-library install only the last-written media type's labels
    survived and movie collections stayed visible to everyone (#332).

    Enumerating every library here means the value written is complete
    whichever run performs the write.

    Returns {username: {"movie": [...], "tv": [...]}}. Kept SEPARATE per
    media type (#340): Plex applies filterMovies to movie libraries and
    filterTelevision to television ones, so writing the union to both
    puts labels in a filter that can never match anything there. An
    earlier fix for #332 merged them, which was cruder than needed.
    """
    from .config import get_libraries_for_media_type
    from .labels import build_label_name

    bases: Dict[str, List[str]] = {}
    for media_type in (MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV):
        libraries = get_libraries_for_media_type(config, media_type)
        if len(libraries) > 1:
            # Mirrors _library_suffix_for_label(): only a genuine
            # multi-library media type gets qualified labels, so
            # single-library installs keep exactly today's names.
            bases[media_type] = [f"PrivateCollection_{lib['id']}" for lib in libraries]
        else:
            bases[media_type] = ["PrivateCollection"]

    labels: Dict[str, Dict[str, List[str]]] = {}
    for user in users:
        per_type: Dict[str, List[str]] = {}
        for media_type, media_bases in bases.items():
            seen: List[str] = []
            for base in media_bases:
                label = build_label_name(base, users, user, append_usernames)
                if label not in seen:
                    seen.append(label)
            per_type[media_type] = seen
        labels[user] = per_type
    return labels


def apply_user_label_restrictions(
    config: Dict,
    all_user_private_labels: Mapping[str, Union[str, Sequence[str]]],
    restrict_unconfigured_users: bool = True,
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

    # One configured user hides their collection from nobody - UNLESS
    # unconfigured server users are also covered (#332), in which case
    # that single user's collection still needs hiding from everyone
    # else on the server. The old unconditional short-circuit silently
    # disabled that whole path for single-user installs.
    if len(all_user_private_labels) <= 1 and not restrict_unconfigured_users:
        return True

    plex_token = config["plex"]["token"]
    all_success = True

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

        # user_id -> {aliases, current filters}. Keyed by ID, never by
        # name: Plex exposes each user under up to THREE names (title,
        # username, email), and an earlier version iterated those name
        # keys directly. That wrote the same person up to three times per
        # run, and because only one of those names matched the config
        # key, the other two were treated as unconfigured users - which
        # excluded that person's OWN collection from their own library
        # (#340). Resolving to IDs first makes each user exactly one
        # target.
        by_id: Dict[str, Dict[str, Any]] = {}
        alias_to_id: Dict[str, str] = {}
        for user_elem in root.findall(".//User"):
            user_id = user_elem.get("id")
            if not user_id:
                continue
            aliases = [
                user_elem.get("title", ""),
                user_elem.get("username", ""),
                user_elem.get("email", ""),
            ]
            by_id[user_id] = {
                "aliases": [a for a in aliases if a],
                "filterMovies": user_elem.get("filterMovies", "") or "",
                "filterTelevision": user_elem.get("filterTelevision", "") or "",
            }
            for alias in aliases:
                if alias:
                    alias_to_id[alias.lower()] = user_id

        logger.debug(f"Plex users available for restrictions: {sorted(alias_to_id)}")

        def _resolve(name: str) -> Optional[str]:
            """A configured username -> Plex user id, tolerating the
            punctuation/spacing differences between a config key and a
            Plex display name."""
            found = alias_to_id.get(name.lower())
            if found:
                return found
            wanted = name.lower().replace(" ", "").replace("-", "").replace("_", "")
            for alias, uid in alias_to_id.items():
                if alias.replace(" ", "").replace("-", "").replace("_", "") == wanted:
                    return uid
            return None

        # Map each configured user onto their Plex id, so an id can be
        # recognized as configured no matter which alias config used.
        id_to_configured: Dict[str, str] = {}
        for configured_name in all_user_private_labels:
            # The admin is the server OWNER and is absent from
            # /api/users, so resolving them always fails. Plex does not
            # allow restrictions on them anyway - skip before resolving,
            # rather than reporting a lookup failure for a user who is
            # not supposed to be found.
            if configured_name.lower() == admin_username:
                logger.debug(f"Skipping restrictions for admin user: {configured_name}")
                continue
            resolved = _resolve(configured_name)
            if resolved:
                id_to_configured[resolved] = configured_name
            else:
                log_warning(
                    f"User '{configured_name}' not found for label restrictions. Available: {sorted(alias_to_id)}"
                )
                all_success = False

        admin_id = alias_to_id.get(admin_username)

        # #332: cover every user on the server, not only configured ones -
        # an unmanaged user still sees everyone's PrivateCollection_*
        # collections otherwise, which is the condition this prevents.
        target_ids = list(id_to_configured) if not restrict_unconfigured_users else list(by_id)

        for user_id in target_ids:
            if user_id == admin_id:
                logger.debug("Skipping restrictions for the admin user (Plex does not allow them)")
                continue

            owner = id_to_configured.get(user_id)  # None => not configured, owns no labels
            display = owner or (by_id.get(user_id, {}).get("aliases") or [user_id])[0]

            # Per media type (#340): filterMovies governs movie libraries
            # and filterTelevision television ones, so a label from the
            # other kind can never match there and does not belong in it.
            params: Dict[str, str] = {}
            for field, media_type in (("filterMovies", MEDIA_TYPE_MOVIE), ("filterTelevision", MEDIA_TYPE_TV)):
                exclude = [
                    label
                    for name, labels in all_user_private_labels.items()
                    if name != owner
                    for label in _labels_for(labels, media_type)
                ]
                params[field] = f"label!={','.join(exclude)}" if exclude else ""

            if not any(params.values()):
                continue

            # #340: only write when something actually changed. Every run
            # re-PUT an identical filter for every user, which is pure
            # noise against plex.tv and made the label state look like it
            # was churning when it was not.
            current = by_id.get(user_id, {})
            if all(current.get(field, "") == value for field, value in params.items()):
                logger.debug(f"Restrictions for {display} already correct - not rewriting")
                continue

            update_url = f"https://plex.tv/api/users/{user_id}"
            try:
                put_response = _capped_put(
                    update_url,
                    params=params,
                    headers={"X-Plex-Token": plex_token},
                    timeout=PLEX_REQUEST_TIMEOUT,
                )
                put_response.raise_for_status()
                print(f"{GREEN}Applied exclusions for {display}{RESET}")
            except requests.RequestException as e:
                log_warning(f"Failed to apply restrictions for {display}: {e}")
                all_success = False

        return all_success

    except requests.RequestException as e:
        log_warning(f"Error fetching Plex users: {e}")
        return False
    except Exception as e:
        log_warning(f"Unexpected error applying restrictions: {e}")
        return False

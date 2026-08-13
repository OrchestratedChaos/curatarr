# curatarr
# Copyright (C) 2026 OrchestratedChaos
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

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
from .private_label_cache import (
    find_orphaned_owners,
    load_private_label_owners,
    prune_orphaned_private_collections,
    save_private_label_owners,
)

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
                # #352: normalize_case - a PrivateCollection_* label must
                # never depend on which of Plex's mutable name fields
                # (title vs username) this particular run happened to
                # resolve `user` from.
                label = build_label_name(base, users, user, append_usernames, normalize_case=True)
                if label not in seen:
                    seen.append(label)
            per_type[media_type] = seen
        labels[user] = per_type
    return labels


# #351: filterMovies/filterTelevision are sent to plex.tv as URL
# query-string params (see the _capped_put call below), not a request
# body - there is no documented hard limit, but a query string growing
# without bound risks silent rejection/truncation somewhere between here
# and Plex's servers (a proxy, a load balancer, plex.tv itself).
# Retaining departed owners' labels (see the exclude-list computation
# below) makes this list monotonically non-shrinking by design, where
# previously it could only ever be as long as the currently-configured
# user count. Purely advisory: nothing here ever truncates the value
# being sent - silently dropping a label to fit under a made-up limit
# would just reintroduce this same bug for whichever label got cut - it
# only logs loudly so an install accumulating many never-pruned departed
# owners finds out from a warning, not from a quietly-broken exclusion.
MAX_EXCLUDE_FILTER_LENGTH = 4000


def apply_user_label_restrictions(
    config: Dict,
    all_user_private_labels: Mapping[str, Union[str, Sequence[str], Mapping[str, Sequence[str]]]],
    restrict_unconfigured_users: bool = True,
    cache_dir: Optional[str] = None,
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
        restrict_unconfigured_users: Cover every user on the server
                         (#332), not only configured ones - see the
                         comment on the short-circuit below.
        cache_dir: Directory holding cache/private_label_owners.json
                         (#351) - typically BaseRecommender.cache_dir.
                         When given, this run (a) retains any previously
                         -persisted owner's label in the exclude
                         computation even after they leave config, (b)
                         warns once if any such departed owners are
                         found, (c) tears their collection down if
                         collections.prune_orphaned_private_labels is
                         enabled, and (d) persists this run's owners for
                         next time. None (the default) skips all of
                         that and behaves exactly as before #351 -
                         existing callers/tests that never pass it are
                         unaffected.

    Returns:
        True if all restrictions applied successfully, False if any failed
    """
    if not all_user_private_labels:
        return True

    # #351: loaded up front (cheap, local disk) so the short-circuit
    # below can account for departed owners this run might still need to
    # retain - a non-empty persisted cache means there is potentially
    # something to do even when today's config has only one owner.
    persisted_owners: Dict[str, Dict] = {}
    if cache_dir:
        persisted_owners = load_private_label_owners(cache_dir)

    # One configured user hides their collection from nobody - UNLESS
    # unconfigured server users are also covered (#332), in which case
    # that single user's collection still needs hiding from everyone
    # else on the server. The old unconditional short-circuit silently
    # disabled that whole path for single-user installs. #351: also
    # skipped only when nothing is persisted either - a departed owner's
    # retained label still needs excluding from this one remaining user
    # even though today's config has only them.
    if len(all_user_private_labels) <= 1 and not restrict_unconfigured_users and not persisted_owners:
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

        # #351: a user removed from config must not have their label
        # silently un-hidden from everyone else - union in any persisted
        # owner whose account id is no longer among this run's resolved
        # id_to_configured. Keyed by their last-known username, which can
        # never collide with a real id_to_configured VALUE for someone
        # else (those all come from all_user_private_labels' own keys,
        # checked first) unless a live config user happens to reuse that
        # exact name, in which case the live entry wins and the stale one
        # is skipped.
        orphaned_owners = find_orphaned_owners(persisted_owners, id_to_configured.keys())

        # Defense in depth: a username that is still a key in
        # all_user_private_labels is, by definition, still configured
        # this run - never treat it as orphaned just because id
        # resolution happened to miss it this run (a transient Plex API
        # hiccup, a one-off alias mismatch, etc). Without this, a
        # resolution failure for an otherwise-still-configured user could
        # both misreport them as departed AND - with
        # prune_orphaned_private_labels enabled - actually delete their
        # real collection, which must never happen to a configured user.
        orphaned_owners = {
            account_id: entry
            for account_id, entry in orphaned_owners.items()
            if entry["username"] not in all_user_private_labels
        }

        effective_labels: Dict[str, Any] = dict(all_user_private_labels)
        for entry in orphaned_owners.values():
            key = entry["username"]
            if key in effective_labels:
                continue
            effective_labels[key] = entry["labels"]

        # #352: a still-CONFIGURED owner (present in id_to_configured,
        # never orphaned) can also carry a legacy PrivateCollection_*
        # form in the persisted cache - e.g. an install upgrading across
        # the #352 fix, where build_label_name() started case/whitespace
        # -normalizing private labels (utils.labels._sanitize_user_
        # token). The same account id's real, already-existing
        # collection may still be labeled "PrivateCollection_Alex_Pigot"
        # while this run computes the normalized
        # "PrivateCollection_alexpigot" for it. Union the legacy form
        # in here too - keyed on account id (never on username, which is
        # exactly what may have changed shape) - or it silently drops
        # out of every other user's exclude filter the moment upgrade
        # recomputes a differently-shaped label for someone who never
        # actually left config: the same class of leak #351 closed for
        # departures, just triggered by a label-shape change instead.
        for account_id, name in id_to_configured.items():
            legacy_entry = persisted_owners.get(account_id)
            if not legacy_entry:
                continue
            merged: Dict[str, List[str]] = {}
            for media_type in (MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV):
                merged_labels = list(_labels_for(effective_labels.get(name, {}), media_type))
                for label in _labels_for(legacy_entry.get("labels", {}), media_type):
                    if label not in merged_labels:
                        merged_labels.append(label)
                merged[media_type] = merged_labels
            effective_labels[name] = merged

        if orphaned_owners:
            orphan_names = sorted(entry["username"] for entry in orphaned_owners.values())
            log_warning(
                f"{len(orphan_names)} user(s) no longer configured still have a "
                f"PrivateCollection_* label excluded from every other user's filters, "
                f"so their collection stays hidden rather than unexpectedly exposed: "
                f"{', '.join(orphan_names)}. Set collections.prune_orphaned_private_labels: "
                "true in config/tuning.yml to delete their orphaned collection(s) and "
                "stop tracking them."
            )

        # #332: cover every user on the server, not only configured ones -
        # an unmanaged user still sees everyone's PrivateCollection_*
        # collections otherwise, which is the condition this prevents.
        target_ids = list(id_to_configured) if not restrict_unconfigured_users else list(by_id)

        for user_id in target_ids:
            if user_id == admin_id:
                logger.debug("Skipping restrictions for the admin user (Plex does not allow them)")
                continue

            owner = id_to_configured.get(user_id)  # None => not configured, owns no labels
            if owner is None:
                # #351: a departed-but-still-present-on-the-server owner
                # (restrict_unconfigured_users still recomputes their
                # filter too, per #332) must keep seeing their OWN old
                # collection - only every OTHER user's exclude filter is
                # supposed to retain their label. Without this fallback,
                # their retained label would never match `owner` below
                # (which is None for them) and would wrongly get
                # excluded from their own filter too.
                orphan_entry = orphaned_owners.get(user_id)
                owner = orphan_entry["username"] if orphan_entry else None
            display = owner or (by_id.get(user_id, {}).get("aliases") or [user_id])[0]

            # Per media type (#340): filterMovies governs movie libraries
            # and filterTelevision television ones, so a label from the
            # other kind can never match there and does not belong in it.
            params: Dict[str, str] = {}
            for field, media_type in (("filterMovies", MEDIA_TYPE_MOVIE), ("filterTelevision", MEDIA_TYPE_TV)):
                # #351: effective_labels (computed above), not
                # all_user_private_labels directly - the union of
                # currently configured owners and any still-retained
                # departed owners. De-duped (explicit loop, not a list
                # comprehension, to preserve order while doing it) in
                # case a departed owner's persisted labels ever overlap
                # a live one's.
                exclude: List[str] = []
                for name, labels in effective_labels.items():
                    if name == owner:
                        continue
                    for label in _labels_for(labels, media_type):
                        if label not in exclude:
                            exclude.append(label)
                value = f"label!={','.join(exclude)}" if exclude else ""
                if len(value) > MAX_EXCLUDE_FILTER_LENGTH:
                    log_warning(
                        f"{field} for {display} is {len(value)} characters across "
                        f"{len(exclude)} excluded labels - this may exceed what Plex's "
                        "API can reliably accept. Consider enabling "
                        "collections.prune_orphaned_private_labels (config/tuning.yml) "
                        "to remove labels for users no longer configured."
                    )
                params[field] = value

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

        # #351: persist this run's owners so a FUTURE run - even after
        # some of today's owners are gone from config - still knows
        # their label needs excluding. Starts from whatever was already
        # persisted (so an orphan not pruned this run stays remembered),
        # then overlays every currently-configured owner resolved above.
        #
        # #352: writes effective_labels (already the union of this run's
        # freshly-computed labels and any legacy form carried over from
        # persisted_owners above), not all_user_private_labels alone -
        # otherwise the very act of persisting here would immediately
        # discard the legacy form the union above just went to the
        # trouble of retaining, and the next run would start from a
        # cache that already forgot it.
        if cache_dir:
            updated_owners = dict(persisted_owners)
            for account_id, name in id_to_configured.items():
                updated_owners[account_id] = {
                    "username": name,
                    "labels": {
                        media_type: _labels_for(effective_labels.get(name, {}), media_type)
                        for media_type in (MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV)
                    },
                }

            prune_orphaned = bool(config.get("collections", {}).get("prune_orphaned_private_labels", False))
            if orphaned_owners and prune_orphaned:
                # #351 follow-up: only forget an orphan whose collection
                # was actually located and deleted this run - never on
                # the strength of "we tried". A discarded return value
                # here previously popped every orphan regardless of
                # outcome, which meant a Plex-unreachable run or a single
                # library section raising mid-scan silently un-hid a
                # still-existing departed user's collection from every
                # other user's exclude filter on the very next run.
                pruned_usernames = set(prune_orphaned_private_collections(config, orphaned_owners))
                for account_id, entry in orphaned_owners.items():
                    if entry.get("username") in pruned_usernames:
                        updated_owners.pop(account_id, None)

            save_private_label_owners(cache_dir, updated_owners)

        return all_success

    except requests.RequestException as e:
        log_warning(f"Error fetching Plex users: {e}")
        return False
    except Exception as e:
        log_warning(f"Unexpected error applying restrictions: {e}")
        return False
